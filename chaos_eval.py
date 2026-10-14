"""
MachinaSense chaos simulation & robustness evaluation -- Step 4 of the blueprint.

Takes the already-trained engine (Step 2) and alert policies (Step 3), freezes
them, and replays the held-out TEST period under deliberately corrupted
telemetry. Nothing is retrained or re-tuned, so every drop in the table is a
genuine robustness cost.

Components
----------
* SensorHealthMonitor : defensive validation layer. Diagnoses missing data,
                        long gaps and stuck/flatlined sensors per channel and
                        labels each OK / DEGRADED / FAILED.
* ChaosInjector       : Gaussian noise, acquisition outages (all channels
                        blank), per-channel micro-dropouts, dead sensors.
* RobustnessEvaluator : runs every scenario through the SAME clean -> features
                        -> score path and prints the "Before vs After" table.

How missing data is counted
---------------------------
Windows that touch an unrecoverable gap (longer than the cleaner's bounded
forward-fill) are NOT scored; the pipeline refuses to guess. Metrics therefore
come in two flavours:
  * effective (precision/recall/F1): unscored fault windows count as misses --
    the honest operational view;
  * scored (recall_scored/f1_scored): only windows the system chose to score --
    isolates model quality from data availability.
`coverage` is the fraction of the baseline test windows that could be scored.
Early-warning columns reuse the Step 3 tuned policies; unscored windows enter
as sub-threshold, so persistence (k consecutive) cannot bridge a gap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from early_warning import (EarlyWarningSystem, alert_active_mask, evaluate_policy, format_duration,
                           to_epoch_seconds)
from engine import (
    COL_IS_FAULT,
    COL_WINDOW_END,
    COL_WINDOW_START,
    EngineConfig,
    FeatureConfig,
    MachinaSenseEngine,
    SyntheticConfig,
    SyntheticTelemetryGenerator,
    binary_metrics,
    configure_logging,
)

LOGGER = logging.getLogger("machinasense.chaos")
_SENTINEL = -1.0  # score for unscored windows; below every real score/threshold


# --------------------------------------------------------------------------- #
# Defensive validation layer: sensor health
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HealthConfig:
    flatline_min_run: int = 200  # identical consecutive samples => stuck sensor
    degraded_missing_frac: float = 0.01
    failed_missing_frac: float = 0.50
    degraded_flatline_frac: float = 0.05
    failed_flatline_frac: float = 0.80


def _run_lengths(mask: NDArray[np.bool_]) -> NDArray[np.int64]:
    """Lengths of consecutive True runs."""
    d = np.diff(np.r_[False, mask, False].astype(np.int8))
    return np.flatnonzero(d == -1) - np.flatnonzero(d == 1)


class SensorHealthMonitor:
    """Per-channel diagnosis of raw telemetry (no fitting, no future information).

    Note: it detects availability faults (gaps, stuck values), not additive noise.
    Quantised sensors can legitimately repeat values; raise `flatline_min_run`
    for those channels if you see false FAILED flags.
    """

    def __init__(self, channels: Sequence[str], cfg: HealthConfig | None = None) -> None:
        self.channels = tuple(channels)
        self.cfg = cfg or HealthConfig()

    def assess(self, raw: pd.DataFrame) -> pd.DataFrame:
        c = self.cfg
        rows = {}
        for ch in self.channels:
            x = pd.to_numeric(raw[ch], errors="coerce").to_numpy(dtype=np.float64)
            n = len(x)
            nan = ~np.isfinite(x)
            gaps = _run_lengths(nan)
            same = x[1:] == x[:-1]  # NaN != NaN, so gaps never count as flatline
            runs = _run_lengths(same) + 1
            flat_samples = int(runs[runs >= c.flatline_min_run].sum())
            missing = float(nan.mean())
            flat = flat_samples / n
            if flat >= c.failed_flatline_frac or missing >= c.failed_missing_frac:
                status = "FAILED"
            elif flat >= c.degraded_flatline_frac or missing >= c.degraded_missing_frac:
                status = "DEGRADED"
            else:
                status = "OK"
            rows[ch] = {
                "missing_pct": 100.0 * missing,
                "longest_gap": int(gaps.max()) if len(gaps) else 0,
                "flatline_pct": 100.0 * flat,
                "status": status,
            }
        return pd.DataFrame(rows).T

    @staticmethod
    def summarize(health: pd.DataFrame) -> str:
        bad = health[health["status"] != "OK"]
        if bad.empty:
            return "all OK"
        if len(bad) == len(health) and bad["status"].nunique() == 1:
            return f"ALL:{bad['status'].iloc[0]}"
        return ", ".join(f"{i}:{s}" for i, s in bad["status"].items())


# --------------------------------------------------------------------------- #
# Chaos injection
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChaosScenario:
    name: str
    noise_sigma: float = 0.0  # Gaussian noise std, as a multiple of each channel's std
    outage_frac: float = 0.0  # share of time with ALL channels blank (logger outage)
    outage_len: tuple[int, int] = (100, 1000)  # samples
    micro_dropout_frac: float = 0.0  # per-channel short dropouts
    micro_len: tuple[int, int] = (1, 30)  # samples (repairable by the cleaner)
    dead_channels: tuple[str, ...] = ()  # sensors stuck at a constant value


DEFAULT_SCENARIOS: tuple[ChaosScenario, ...] = (
    ChaosScenario("clean (BEFORE)"),
    ChaosScenario("noise 0.5 sigma", noise_sigma=0.5),
    ChaosScenario("noise 1.0 sigma", noise_sigma=1.0),
    ChaosScenario("micro-dropouts 10%/ch", micro_dropout_frac=0.10),
    ChaosScenario("outages 5%", outage_frac=0.05),
    ChaosScenario("outages 20%", outage_frac=0.20),
    ChaosScenario("dead sensor vib_x", dead_channels=("vib_x",)),
    ChaosScenario("combined: noise+outage+micro", noise_sigma=0.5, outage_frac=0.05, micro_dropout_frac=0.05),
)


class ChaosInjector:
    def __init__(self, channels: Sequence[str]) -> None:
        self.channels = list(channels)

    @staticmethod
    def _chunk_mask(n: int, frac: float, length_range: tuple[int, int], rng: np.random.Generator) -> NDArray[np.bool_]:
        """Random contiguous chunks until ~frac of the stream is covered (overlaps make it slightly less)."""
        mask = np.zeros(n, dtype=bool)
        lo, hi = length_range
        covered, target = 0, int(frac * n)
        while covered < target:
            length = int(rng.integers(lo, hi + 1))
            start = int(rng.integers(0, max(1, n - length)))
            mask[start:start + length] = True
            covered += length
        return mask

    def apply(self, df: pd.DataFrame, sc: ChaosScenario, seed: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        out = df.copy()
        vals = out[self.channels].to_numpy(dtype=np.float64).copy()
        n = len(vals)
        sigma = np.nanstd(vals, axis=0)  # scale of the perturbation only; nothing is fitted

        if sc.noise_sigma > 0:
            vals += rng.normal(0.0, 1.0, vals.shape) * (sc.noise_sigma * sigma)
        for ch in sc.dead_channels:
            j = self.channels.index(ch)
            first_valid = np.flatnonzero(np.isfinite(vals[:, j]))[0]
            vals[:, j] = vals[first_valid, j]  # stuck-at value
        if sc.micro_dropout_frac > 0:
            for j in range(vals.shape[1]):
                vals[self._chunk_mask(n, sc.micro_dropout_frac, sc.micro_len, rng), j] = np.nan
        if sc.outage_frac > 0:
            vals[self._chunk_mask(n, sc.outage_frac, sc.outage_len, rng), :] = np.nan

        out[self.channels] = vals
        return out


# --------------------------------------------------------------------------- #
# Evaluation harness
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChaosConfig:
    scenarios: tuple[ChaosScenario, ...] = DEFAULT_SCENARIOS
    health: HealthConfig = field(default_factory=HealthConfig)
    seed: int = 1234


@dataclass
class RobustnessReport:
    rows: pd.DataFrame  # one row per (scenario, detector)

    def render(self) -> str:
        bar = "=" * 118
        cols = ["coverage", "precision", "recall", "f1", "f1_retained_pct", "recall_scored", "f1_scored",
                "detected", "false_alarms", "normal_alarm_pct", "mean_lead_s", "sensor_health"]
        out = [bar, "MACHINASENSE | BEFORE vs AFTER DEGRADATION (frozen models, held-out TEST period)", bar,
               "precision/recall/f1 = effective (unscored fault windows count as misses); *_scored = scored windows only;",
               "detected/false_alarms/mean_lead_s use the Step 3 tuned alert policies;",
               "normal_alarm_pct = % of normal-operation windows spent in alarm (catches alerts that latch on and never clear)."]
        for det, g in self.rows.groupby("detector", sort=False):
            out += ["", f"--- {det} ---",
                    g.set_index("scenario")[cols].to_string(float_format=lambda v: f"{v:.3f}")]
        out += ["", "Worst case per detector:"]
        for det, g in self.rows.groupby("detector", sort=False):
            worst = g.loc[g["f1"].idxmin()]
            out.append(f"  {det}: lowest effective F1 = {worst['f1']:.3f} under '{worst['scenario']}' "
                       f"({worst['f1_retained_pct']:.1f}% of clean); most false alarms = {int(g['false_alarms'].max())}")
        out.append(bar)
        return "\n".join(out)

    def to_csv(self, path: str) -> None:
        self.rows.to_csv(path, index=False)


def _keys(ts) -> NDArray[np.int64]:
    """Millisecond keys for exact window alignment between clean and perturbed runs."""
    return np.round(to_epoch_seconds(ts) * 1000.0).astype(np.int64)


class RobustnessEvaluator:
    def __init__(self, engine: MachinaSenseEngine, ews: EarlyWarningSystem, raw: pd.DataFrame,
                 cfg: ChaosConfig | None = None) -> None:
        if not engine.splits_ or not ews.policies:
            raise RuntimeError("fit the engine and the early-warning system first")
        self.engine, self.ews, self.raw = engine, ews, raw
        self.cfg = cfg or ChaosConfig()
        channels = engine.config.data.channels
        self.injector = ChaosInjector(channels)
        self.monitor = SensorHealthMonitor(channels, self.cfg.health)

    def _test_slice(self) -> pd.DataFrame:
        dc = self.engine.config.data
        base = self.engine.splits_["test"]
        ts = pd.to_datetime(self.raw[dc.timestamp_col], utc=True)
        mask = (ts >= base[COL_WINDOW_START].min()) & (ts <= base[COL_WINDOW_END].max())
        return self.raw.loc[mask].reset_index(drop=True)

    def run(self) -> RobustnessReport:
        eng = self.engine
        base = eng.splits_["test"]
        ref_keys = _keys(base[COL_WINDOW_END])
        y_ref = base[COL_IS_FAULT].to_numpy()
        signals = self.ews.signals["test"]
        test_raw = self._test_slice()
        in_zone = np.zeros(len(signals.times_s), dtype=bool)  # windows inside any fault episode
        for ep in signals.episodes_all:
            in_zone |= (signals.times_s >= ep.onset_s) & (signals.times_s <= ep.failure_s)
        detectors = {
            "IsolationForest": ("iforest_score", eng.iforest.threshold),
            "LightGBM": ("lgbm_fault_prob", eng.lgbm.threshold),
        }

        rows: list[dict] = []
        for i, sc in enumerate(self.cfg.scenarios):
            perturbed = self.injector.apply(test_raw, sc, seed=self.cfg.seed + i)
            health = self.monitor.assess(perturbed)
            flags = self.monitor.summarize(health)
            LOGGER.info("Scenario '%s': missing=%.1f%% | sensor health: %s",
                        sc.name, health["missing_pct"].mean(), flags)

            scored = np.zeros(len(ref_keys), dtype=bool)
            scores = None
            try:
                feats = eng.build_features(perturbed)
                scores = eng.score_features(feats)
                keys = _keys(feats[COL_WINDOW_END])
                pos = np.searchsorted(ref_keys, keys)
                ok = (pos < len(ref_keys)) & (ref_keys[np.clip(pos, 0, len(ref_keys) - 1)] == keys)
                pos, ok_idx = pos[ok], np.flatnonzero(ok)
                scored[pos] = True
            except ValueError as exc:  # e.g. no valid windows at all
                LOGGER.warning("Scenario '%s' produced no scorable windows: %s", sc.name, exc)

            for det, (col, thr) in detectors.items():
                full = np.full(len(ref_keys), _SENTINEL)
                if scores is not None and len(pos):
                    full[pos] = scores[col].to_numpy()[ok_idx]

                eff = binary_metrics(y_ref, full, thr)
                sub = binary_metrics(y_ref[scored], full[scored], thr) if scored.any() else None
                ew = evaluate_policy(signals, full, self.ews.policies[det].policy)
                active = alert_active_mask(full, self.ews.policies[det].policy)
                rows.append({
                    "scenario": sc.name, "detector": det,
                    "coverage": float(scored.mean()),
                    "precision": eff.precision, "recall": eff.recall, "f1": eff.f1, "fpr": eff.fpr,
                    "recall_scored": sub.recall if sub else float("nan"),
                    "f1_scored": sub.f1 if sub else float("nan"),
                    "detected": f"{ew.n_detected}/{ew.n_episodes}", "false_alarms": ew.false_alarms,
                    "mean_lead_s": ew.mean_lead_s,
                    "normal_alarm_pct": 100.0 * float(active[~in_zone].mean()) if (~in_zone).any() else float("nan"),
                    "sensor_health": flags,
                })

        df = pd.DataFrame(rows)
        clean_f1 = df[df["scenario"] == self.cfg.scenarios[0].name].set_index("detector")["f1"]
        df["f1_retained_pct"] = [100.0 * f / clean_f1[d] if clean_f1[d] > 0 else float("nan")
                                 for f, d in zip(df["f1"], df["detector"])]
        return RobustnessReport(df)


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def main() -> None:
    configure_logging(logging.WARNING)  # keep the chaos table readable
    LOGGER.setLevel(logging.INFO)
    logging.getLogger("machinasense.chaos").addHandler(logging.StreamHandler())

    raw = SyntheticTelemetryGenerator(SyntheticConfig(cycles=16)).generate()
    engine = MachinaSenseEngine(EngineConfig(
        features=FeatureConfig(freq_channels=("vib_x", "vib_y", "motor_current_a", "acoustic_db"))))
    engine.fit_evaluate(raw)
    ews = EarlyWarningSystem(engine).fit(raw)

    report = RobustnessEvaluator(engine, ews, raw).run()
    print("\n" + report.render())
    report.to_csv("chaos_report.csv")
    print("\nSaved chaos_report.csv")


if __name__ == "__main__":
    main()
