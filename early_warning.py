"""
MachinaSense early-warning system -- Step 3 of the blueprint.

Defines the degradation horizon, measures how far BEFORE the failure point the
first alert fires, and tunes the alert policy to maximise that lead time while
holding false alarms at (near) zero.

Key definitions
---------------
* Fault episode  : onset (degradation begins) -> failure point (end of episode).
* Lead time      : failure_time - first_alert_time   (the headline metric).
* Detection delay: first_alert_time - onset.
* Alert policy   : (threshold, persistence_k). An alert fires when the fault
                   score is >= threshold for k consecutive windows, then stays
                   active until the score drops below threshold for k windows,
                   so one degradation = one alert event.
* False alarm    : an alert event outside every fault episode, counted per
                   EVENT (not per window), because operators feel events.

Leakage discipline: policies are tuned on the VALIDATION split only and then
frozen; the TEST split (later in time) is used once for reporting.
Time-to-failure regressor is trained on TRAIN fault windows only.

Time units follow the telemetry timestamps. The synthetic demo runs episodes of
seconds; on a real run-to-failure dataset the same code reports minutes/hours.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Sequence

import lightgbm as lgb
import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.stats import spearmanr

from engine import (
    COL_WINDOW_END,
    COL_WINDOW_START,
    DataConfig,
    EngineConfig,
    FeatureConfig,
    MachinaSenseEngine,
    SyntheticConfig,
    SyntheticTelemetryGenerator,
    configure_logging,
    timed,
)

LOGGER = logging.getLogger("machinasense.early_warning")
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def to_epoch_seconds(values) -> NDArray[np.float64]:
    """Resolution-independent conversion of timestamps to float epoch seconds."""
    ts = pd.to_datetime(pd.Series(values), utc=True)
    return (ts - _EPOCH).dt.total_seconds().to_numpy(dtype=np.float64)


def to_iso(t_s: float) -> str:
    return pd.Timestamp(t_s, unit="s", tz="UTC").round("ms").isoformat()  # ms rounding hides float noise


def format_duration(seconds: float) -> str:
    """Human-readable lead time: seconds, minutes or hours as appropriate."""
    if seconds != seconds:  # NaN
        return "n/a"
    if abs(seconds) < 120:
        return f"{seconds:.1f} s"
    if abs(seconds) < 7200:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.2f} h"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TTFConfig:
    n_estimators: int = 500
    learning_rate: float = 0.05
    num_leaves: int = 31
    early_stopping_rounds: int = 50
    random_state: int = 42


@dataclass(frozen=True)
class EarlyWarningConfig:
    persistence_grid: tuple[int, ...] = (1, 2, 3, 4, 5)
    n_threshold_grid: int = 150
    max_false_alarms: int = 0  # allowed false-alarm EVENTS on validation
    min_detection_rate: float = 1.0  # fraction of validation episodes that must be caught
    lead_tolerance: float = 0.02  # accept thresholds within 2% of best lead, take the safest
    ttf: TTFConfig = field(default_factory=TTFConfig)


# --------------------------------------------------------------------------- #
# Degradation horizon: fault episodes and time-to-failure targets
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FaultEpisode:
    episode_id: int
    fault_class: int
    onset_s: float  # degradation begins
    failure_s: float  # fault point

    @property
    def duration_s(self) -> float:
        return self.failure_s - self.onset_s


def extract_episodes(raw: pd.DataFrame, data_cfg: DataConfig) -> list[FaultEpisode]:
    """Contiguous runs of a non-normal label become episodes (onset -> failure)."""
    ts = to_epoch_seconds(raw[data_cfg.timestamp_col])
    label = (
        pd.to_numeric(raw[data_cfg.label_col], errors="coerce")
        .fillna(data_cfg.normal_label)
        .to_numpy()
        .astype(int)
    )
    order = np.argsort(ts, kind="stable")
    ts, label = ts[order], label[order]

    starts = np.flatnonzero(np.r_[True, label[1:] != label[:-1]])
    ends = np.r_[starts[1:], len(label)] - 1
    episodes: list[FaultEpisode] = []
    for s, e in zip(starts, ends):
        if label[s] != data_cfg.normal_label:
            episodes.append(FaultEpisode(len(episodes) + 1, int(label[s]), float(ts[s]), float(ts[e])))
    LOGGER.info("Extracted %d fault episodes (mean horizon %s)", len(episodes),
                format_duration(float(np.mean([e.duration_s for e in episodes]))) if episodes else "n/a")
    return episodes


def episodes_from_failure_times(failure_times: Sequence, horizon_s: float, fault_class: int = 1) -> list[FaultEpisode]:
    """For run-to-failure datasets where only the failure time is known:
    the degradation horizon is the final `horizon_s` seconds before failure."""
    fails = to_epoch_seconds(failure_times)
    return [FaultEpisode(i + 1, fault_class, float(f - horizon_s), float(f)) for i, f in enumerate(sorted(fails))]


def assign_ttf_seconds(window_end_s: NDArray[np.float64], episodes: Sequence[FaultEpisode]) -> NDArray[np.float64]:
    """Time-to-failure per window (NaN outside any episode)."""
    if not episodes:
        return np.full(len(window_end_s), np.nan)
    eps = sorted(episodes, key=lambda e: e.onset_s)
    onsets = np.array([e.onset_s for e in eps])
    fails = np.array([e.failure_s for e in eps])
    idx = np.searchsorted(onsets, window_end_s, side="right") - 1
    idx_c = np.clip(idx, 0, None)
    inside = (idx >= 0) & (window_end_s <= fails[idx_c])
    return np.where(inside, fails[idx_c] - window_end_s, np.nan)


# --------------------------------------------------------------------------- #
# Alert policy, event detection, and lead-time evaluation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AlertPolicy:
    threshold: float
    persistence_k: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AlertEvent:
    time_s: float
    window_index: int
    score: float


def detect_alert_events(times_s: NDArray[np.float64], scores: NDArray[np.float64],
                        policy: AlertPolicy) -> list[AlertEvent]:
    """k-consecutive trigger with symmetric hysteresis (one event per degradation)."""
    k = policy.persistence_k
    above = scores >= policy.threshold
    events: list[AlertEvent] = []
    run = below = 0
    active = False
    for i, a in enumerate(above):
        if a:
            run += 1
            below = 0
            if not active and run >= k:
                events.append(AlertEvent(float(times_s[i]), i, float(scores[i])))
                active = True
        else:
            run = 0
            if active:
                below += 1
                if below >= k:
                    active, below = False, 0
    return events


def alert_active_mask(scores: NDArray[np.float64], policy: AlertPolicy) -> NDArray[np.bool_]:
    """Boolean mask indicating whether the alert state is active at each window."""
    k = policy.persistence_k
    above = scores >= policy.threshold
    out = np.zeros(len(scores), dtype=bool)
    run = below = 0
    active = False
    for i, a in enumerate(above):
        if a:
            run += 1
            below = 0
            if not active and run >= k:
                active = True
        else:
            run = 0
            if active:
                below += 1
                if below >= k:
                    active, below = False, 0
        out[i] = active
    return out


@dataclass(frozen=True, eq=False)
class SplitSignals:
    """Timeline facts for one chronological split."""

    times_s: NDArray[np.float64]
    bounds: tuple[float, float]
    episodes_all: tuple[FaultEpisode, ...]  # overlapping the split (alerts inside are not false alarms)
    episodes_scored: tuple[FaultEpisode, ...]  # fully observed (used for lead-time statistics)
    normal_hours: float

    @classmethod
    def build(cls, frame: pd.DataFrame, episodes: Sequence[FaultEpisode], tol_s: float) -> "SplitSignals":
        times = to_epoch_seconds(frame[COL_WINDOW_END])
        start = float(to_epoch_seconds(frame[COL_WINDOW_START]).min())
        end = float(times.max())
        overlapping = tuple(e for e in episodes if e.failure_s >= start and e.onset_s <= end)
        scored = tuple(e for e in overlapping if e.onset_s >= start and e.failure_s <= end + tol_s)
        zone = sum(max(0.0, min(e.failure_s, end) - max(e.onset_s, start)) for e in overlapping)
        normal_hours = max(end - start - zone, 1e-9) / 3600.0
        return cls(times, (start, end), overlapping, scored, normal_hours)


@dataclass
class EpisodeOutcome:
    episode: FaultEpisode
    detected: bool
    first_alert_s: float = float("nan")
    first_alert_index: int = -1
    lead_s: float = float("nan")
    delay_s: float = float("nan")


@dataclass
class WarningMetrics:
    n_episodes: int
    n_detected: int
    false_alarms: int
    false_alarms_per_hour: float
    mean_lead_s: float
    median_lead_s: float
    mean_delay_s: float
    outcomes: list[EpisodeOutcome]

    @property
    def detection_rate(self) -> float:
        return self.n_detected / self.n_episodes if self.n_episodes else float("nan")


def evaluate_policy(signals: SplitSignals, scores: NDArray[np.float64], policy: AlertPolicy) -> WarningMetrics:
    events = detect_alert_events(signals.times_s, scores, policy)
    ev_t = np.array([e.time_s for e in events])

    outcomes: list[EpisodeOutcome] = []
    for ep in signals.episodes_scored:
        hit = np.flatnonzero((ev_t >= ep.onset_s) & (ev_t <= ep.failure_s)) if len(ev_t) else np.array([], int)
        if len(hit):
            ev = events[int(hit[0])]
            outcomes.append(EpisodeOutcome(ep, True, ev.time_s, ev.window_index,
                                           ep.failure_s - ev.time_s, ev.time_s - ep.onset_s))
        else:
            outcomes.append(EpisodeOutcome(ep, False))

    in_zone = np.zeros(len(ev_t), dtype=bool)
    for ep in signals.episodes_all:
        in_zone |= (ev_t >= ep.onset_s) & (ev_t <= ep.failure_s)
    false_alarms = int((~in_zone).sum()) if len(ev_t) else 0

    leads = np.array([o.lead_s for o in outcomes if o.detected])
    delays = np.array([o.delay_s for o in outcomes if o.detected])
    nan = float("nan")
    return WarningMetrics(
        n_episodes=len(outcomes),
        n_detected=int(len(leads)),
        false_alarms=false_alarms,
        false_alarms_per_hour=false_alarms / signals.normal_hours,
        mean_lead_s=float(leads.mean()) if len(leads) else nan,
        median_lead_s=float(np.median(leads)) if len(leads) else nan,
        mean_delay_s=float(delays.mean()) if len(delays) else nan,
        outcomes=outcomes,
    )


# --------------------------------------------------------------------------- #
# Threshold / persistence optimisation (validation only)
# --------------------------------------------------------------------------- #
@dataclass
class TunedPolicy:
    detector: str
    policy: AlertPolicy
    val_metrics: WarningMetrics
    feasible: bool
    grid: pd.DataFrame


class ThresholdOptimizer:
    """Grid-search (threshold x persistence) for max lead under a false-alarm budget."""

    def __init__(self, cfg: EarlyWarningConfig) -> None:
        self.cfg = cfg

    def optimize(self, detector: str, signals: SplitSignals, scores: NDArray[np.float64]) -> TunedPolicy:
        cfg = self.cfg
        if not signals.episodes_scored:
            raise ValueError("validation split contains no fully-observed fault episode to tune on")

        thresholds = np.unique(np.quantile(scores, np.linspace(0.0, 1.0, cfg.n_threshold_grid)))
        rows = []
        for k in cfg.persistence_grid:
            for thr in thresholds:
                m = evaluate_policy(signals, scores, AlertPolicy(float(thr), k))
                rows.append({
                    "threshold": float(thr), "persistence_k": k,
                    "detection_rate": m.detection_rate, "false_alarms": m.false_alarms,
                    "mean_lead_s": m.mean_lead_s, "mean_delay_s": m.mean_delay_s,
                })
        grid = pd.DataFrame(rows)

        ok = (grid["detection_rate"] >= cfg.min_detection_rate) & (grid["false_alarms"] <= cfg.max_false_alarms)
        feasible = bool(ok.any())
        if feasible:
            cand = grid[ok]
            best = cand["mean_lead_s"].max()
            near = cand[cand["mean_lead_s"] >= best * (1.0 - cfg.lead_tolerance)]
            # among near-optimal operating points take the most conservative one
            pick = near.sort_values(["threshold", "persistence_k"], ascending=[False, True]).iloc[0]
        else:
            LOGGER.warning("%s: no operating point meets the constraints; falling back to fewest false alarms", detector)
            pick = grid.assign(_lead=grid["mean_lead_s"].fillna(-np.inf)).sort_values(
                ["false_alarms", "detection_rate", "_lead"], ascending=[True, False, False]).iloc[0]

        policy = AlertPolicy(float(pick["threshold"]), int(pick["persistence_k"]))
        metrics = evaluate_policy(signals, scores, policy)
        LOGGER.info("%s tuned: threshold=%.5f k=%d | val lead mean=%s, false alarms=%d, detected %d/%d",
                    detector, policy.threshold, policy.persistence_k, format_duration(metrics.mean_lead_s),
                    metrics.false_alarms, metrics.n_detected, metrics.n_episodes)
        return TunedPolicy(detector, policy, metrics, feasible, grid)


# --------------------------------------------------------------------------- #
# Time-to-failure regressor (dynamic remaining-time estimate)
# --------------------------------------------------------------------------- #
class TimeToFailureRegressor:
    """LightGBM regressor on log1p(time-to-failure), trained on fault windows only."""

    def __init__(self, cfg: TTFConfig) -> None:
        self.cfg = cfg
        self.model: lgb.LGBMRegressor | None = None
        self.columns: list[str] = []

    def fit(self, X_tr: pd.DataFrame, ttf_tr: NDArray, X_va: pd.DataFrame, ttf_va: NDArray) -> "TimeToFailureRegressor":
        c = self.cfg
        m_tr, m_va = np.isfinite(ttf_tr), np.isfinite(ttf_va)
        if m_tr.sum() < 10:
            raise ValueError("too few fault windows to fit a time-to-failure model")
        self.columns = list(X_tr.columns)
        self.model = lgb.LGBMRegressor(
            n_estimators=c.n_estimators, learning_rate=c.learning_rate, num_leaves=c.num_leaves,
            subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
            random_state=c.random_state, verbose=-1,
        )
        fit_kwargs = {}
        if m_va.sum() >= 5:
            fit_kwargs = {
                "eval_set": [(X_va.loc[m_va], np.log1p(ttf_va[m_va]))],
                "callbacks": [lgb.early_stopping(c.early_stopping_rounds, verbose=False), lgb.log_evaluation(0)],
            }
        self.model.fit(X_tr.loc[m_tr], np.log1p(ttf_tr[m_tr]), **fit_kwargs)
        return self

    def predict_seconds(self, X: pd.DataFrame) -> NDArray[np.float64]:
        assert self.model is not None, "model not fitted"
        return np.clip(np.expm1(self.model.predict(X[self.columns])), 0.0, None)

    def evaluate(self, X: pd.DataFrame, ttf_true: NDArray) -> dict[str, float]:
        m = np.isfinite(ttf_true)
        if m.sum() < 3:
            return {"n": float(m.sum()), "mae_s": float("nan"), "median_ae_s": float("nan"), "spearman": float("nan")}
        pred = self.predict_seconds(X.loc[m])
        err = np.abs(pred - ttf_true[m])
        rho = spearmanr(pred, ttf_true[m]).statistic
        return {"n": float(m.sum()), "mae_s": float(err.mean()), "median_ae_s": float(np.median(err)),
                "spearman": float(rho)}


# --------------------------------------------------------------------------- #
# Orchestrator + report
# --------------------------------------------------------------------------- #
@dataclass
class EarlyWarningReport:
    tuning: pd.DataFrame
    comparison: pd.DataFrame
    episodes: pd.DataFrame
    ttf_metrics: dict[str, float]

    def render(self) -> str:
        bar = "=" * 100
        f4 = lambda v: f"{v:.4f}"  # noqa: E731
        f2 = lambda v: f"{v:.2f}"  # noqa: E731
        return "\n".join([
            bar, "MACHINASENSE | EARLY-WARNING EVALUATION (held-out test, policies tuned on validation only)", bar,
            "", "Selected alert policies (validation):", self.tuning.to_string(float_format=f4),
            "", "Default (Step 2 threshold, k=1) vs tuned policy on TEST:", self.comparison.to_string(float_format=f2),
            "", "Per-episode lead time before the fault point (tuned policies, TEST):",
            self.episodes.to_string(float_format=f2),
            "", "Time-to-failure regressor on TEST fault windows:",
            "  " + ", ".join(f"{k}={v:.3f}" for k, v in self.ttf_metrics.items()), bar,
        ])


class EarlyWarningSystem:
    """Wraps a fitted MachinaSenseEngine with episodes, TTF model and tuned alert policies."""

    def __init__(self, engine: MachinaSenseEngine, cfg: EarlyWarningConfig | None = None) -> None:
        self.engine = engine
        self.cfg = cfg or EarlyWarningConfig()
        self.optimizer = ThresholdOptimizer(self.cfg)
        self.ttf = TimeToFailureRegressor(self.cfg.ttf)
        self.episodes: list[FaultEpisode] = []
        self.policies: dict[str, TunedPolicy] = {}
        self._signals: dict[str, SplitSignals] = {}

    @property
    def signals(self) -> dict[str, SplitSignals]:
        return self._signals

    def _detectors(self) -> dict[str, tuple[str, float]]:
        return {
            "IsolationForest": ("iforest_score", self.engine.iforest.threshold),
            "LightGBM": ("lgbm_fault_prob", self.engine.lgbm.threshold),
        }

    def fit(self, raw: pd.DataFrame) -> "EarlyWarningSystem":
        eng = self.engine
        if not eng.splits_:
            raise RuntimeError("engine must be fitted first: call engine.fit_evaluate(raw)")
        dc, fc = eng.config.data, eng.config.features
        tol_s = fc.window_size / dc.sampling_rate_hz

        self.episodes = extract_episodes(raw, dc)
        frames = eng.splits_
        self._signals = {k: SplitSignals.build(frames[k], self.episodes, tol_s) for k in ("train", "val", "test")}
        for k, s in self._signals.items():
            LOGGER.info("%s split: %d fully-observed episodes (%d overlapping), %.4f h of normal operation",
                        k, len(s.episodes_scored), len(s.episodes_all), s.normal_hours)

        ttf = {k: assign_ttf_seconds(self._signals[k].times_s, self.episodes) for k in frames}
        cols = eng.feature_names_
        with timed("Time-to-failure regressor fit"):
            self.ttf.fit(frames["train"][cols], ttf["train"], frames["val"][cols], ttf["val"])

        val_scores = eng.score_features(frames["val"])
        with timed("Alert-policy optimisation"):
            for name, (col, _) in self._detectors().items():
                self.policies[name] = self.optimizer.optimize(
                    name, self._signals["val"], val_scores[col].to_numpy())
        return self

    def evaluate(self) -> EarlyWarningReport:
        eng = self.engine
        test_frame = eng.splits_["test"]
        sig = self._signals["test"]
        test_scores = eng.score_features(test_frame)
        ttf_true = assign_ttf_seconds(sig.times_s, self.episodes)
        ttf_pred = self.ttf.predict_seconds(test_frame[eng.feature_names_])
        names = eng.config.data.class_names

        tuning_rows, cmp_rows, ep_rows = {}, {}, []
        for det, (col, default_thr) in self._detectors().items():
            scores = test_scores[col].to_numpy()
            tuned = self.policies[det]
            tuning_rows[det] = {
                "threshold": tuned.policy.threshold, "persistence_k": tuned.policy.persistence_k,
                "val_detected": f"{tuned.val_metrics.n_detected}/{tuned.val_metrics.n_episodes}",
                "val_false_alarms": tuned.val_metrics.false_alarms,
                "val_mean_lead_s": tuned.val_metrics.mean_lead_s, "constraints_met": tuned.feasible,
            }
            for label, pol in (("default", AlertPolicy(default_thr, 1)), ("tuned", tuned.policy)):
                m = evaluate_policy(sig, scores, pol)
                cmp_rows[(det, label)] = {
                    "threshold": pol.threshold, "k": pol.persistence_k,
                    "detected": f"{m.n_detected}/{m.n_episodes}", "false_alarms": m.false_alarms,
                    "FA_per_hour": m.false_alarms_per_hour, "mean_lead_s": m.mean_lead_s,
                    "median_lead_s": m.median_lead_s, "mean_delay_s": m.mean_delay_s,
                }
                if label != "tuned":
                    continue
                for o in m.outcomes:
                    ep = o.episode
                    est = float(ttf_pred[o.first_alert_index]) if o.detected else float("nan")
                    row = {
                        "detector": det, "episode": ep.episode_id,
                        "fault": names.get(ep.fault_class, str(ep.fault_class)),
                        "onset": to_iso(ep.onset_s), "failure": to_iso(ep.failure_s),
                        "first_alert": to_iso(o.first_alert_s) if o.detected else "MISSED",
                        "lead_s": o.lead_s, "lead_min": o.lead_s / 60.0,
                        "lead_pct_of_horizon": 100.0 * o.lead_s / ep.duration_s if o.detected else float("nan"),
                        "delay_s": o.delay_s, "est_ttf_at_alert_s": est,
                        "ttf_est_err_s": abs(est - o.lead_s) if o.detected else float("nan"),
                    }
                    ep_rows.append(row)
                    if o.detected:
                        LOGGER.info("[%s] episode %d (%s): first alert %s BEFORE the fault point "
                                    "(%.0f%% of the degradation horizon; %.2f min)", det, ep.episode_id, row["fault"],
                                    format_duration(o.lead_s), row["lead_pct_of_horizon"], row["lead_min"])
                    else:
                        LOGGER.warning("[%s] episode %d (%s): MISSED", det, ep.episode_id, row["fault"])

        comparison = pd.DataFrame(cmp_rows).T
        comparison.index.names = ["detector", "policy"]
        return EarlyWarningReport(
            tuning=pd.DataFrame(tuning_rows).T,
            comparison=comparison,
            episodes=pd.DataFrame(ep_rows),
            ttf_metrics=self.ttf.evaluate(test_frame[eng.feature_names_], ttf_true),
        )

    def policies_as_dict(self) -> dict[str, dict]:
        """JSON-friendly export for the FastAPI service (Step 5/7)."""
        return {k: v.policy.to_dict() for k, v in self.policies.items()}


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def main() -> None:
    configure_logging()
    raw = SyntheticTelemetryGenerator(SyntheticConfig(cycles=16)).generate()  # more episodes -> stabler tuning
    engine = MachinaSenseEngine(EngineConfig(
        features=FeatureConfig(freq_channels=("vib_x", "vib_y", "motor_current_a", "acoustic_db"))))
    engine.fit_evaluate(raw)

    ews = EarlyWarningSystem(engine).fit(raw)
    print("\n" + ews.evaluate().render())
    print("\nPolicies for deployment:", ews.policies_as_dict())


if __name__ == "__main__":
    main()
