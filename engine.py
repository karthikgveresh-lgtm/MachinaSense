"""
MachinaSense core engine -- Steps 1 & 2 of the blueprint.

Step 1: Defensive cleaning -> rolling time/frequency features -> leakage-free
        chronological split (with embargo).
Step 2: Isolation Forest baseline (unsupervised) vs. LightGBM fault classifier,
        evaluated on a held-out *future* test period with Precision/Recall/F1.

Design rules
------------
* Every cleaning operation is CAUSAL (forward-fill, trailing median). Nothing
  looks into the future, so cleaning cannot leak information across the split.
* Scalers / thresholds / models are fitted on TRAIN (+ calibrated on VAL) only.
  TEST is touched exactly once, at evaluation time.
* Adjacent rolling windows overlap, so an embargo gap is inserted between
  splits and verified at runtime (`LeakageError` if violated).

Run `python engine.py` for an end-to-end demo on synthetic telemetry.
"""
from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
from numpy.typing import NDArray
from scipy import stats as sp_stats
from scipy.signal import lfilter
from sklearn.ensemble import IsolationForest
from sklearn.metrics import (
    average_precision_score,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import RobustScaler

import lightgbm as lgb

LOGGER = logging.getLogger("machinasense.engine")
_EPS = 1e-12

# Metadata columns emitted by the feature extractor (never used as model inputs).
COL_WINDOW_START = "window_start"
COL_WINDOW_END = "window_end"
COL_IMPUTED_FRAC = "imputed_frac"
COL_LABEL = "label"
COL_IS_FAULT = "is_fault"
META_COLUMNS = (COL_WINDOW_START, COL_WINDOW_END, COL_IMPUTED_FRAC, COL_LABEL, COL_IS_FAULT)
_IMPUTED_FLAG = "_imputed"


# --------------------------------------------------------------------------- #
# Logging / utilities
# --------------------------------------------------------------------------- #
def configure_logging(level: int = logging.INFO) -> None:
    """Idempotent, structured console logging."""
    root = logging.getLogger("machinasense")
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


@contextmanager
def timed(label: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        LOGGER.info("%s finished in %.2fs", label, time.perf_counter() - start)


class SchemaError(ValueError):
    """Raised when required telemetry columns are absent."""


class LeakageError(RuntimeError):
    """Raised when a temporal split would leak future information."""


# --------------------------------------------------------------------------- #
# Configuration objects
# --------------------------------------------------------------------------- #
DEFAULT_CHANNELS: tuple[str, ...] = (
    "vib_x",
    "vib_y",
    "temperature_c",
    "motor_current_a",
    "acoustic_db",
)


@dataclass(frozen=True)
class DataConfig:
    channels: tuple[str, ...] = DEFAULT_CHANNELS
    timestamp_col: str = "timestamp"
    label_col: str = "fault_label"
    normal_label: int = 0
    sampling_rate_hz: float = 1000.0
    class_names: Mapping[int, str] = field(
        default_factory=lambda: {0: "normal", 1: "bearing_wear", 2: "imbalance"}
    )

    def __post_init__(self) -> None:
        if self.sampling_rate_hz <= 0:
            raise ValueError("sampling_rate_hz must be positive")
        if not self.channels:
            raise ValueError("at least one channel is required")


@dataclass(frozen=True)
class CleaningConfig:
    max_gap_samples: int = 32  # longest dropout that is forward-filled
    smooth_channels: tuple[str, ...] = ("temperature_c",)  # slow channels only
    smooth_kernel: int = 9  # trailing median window (samples)
    physical_limits: Mapping[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "vib_x": (-50.0, 50.0),
            "vib_y": (-50.0, 50.0),
            "temperature_c": (-40.0, 200.0),
            "motor_current_a": (0.0, 100.0),
            "acoustic_db": (-50.0, 50.0),
        }
    )


@dataclass(frozen=True)
class FeatureConfig:
    window_size: int = 256
    stride: int = 128
    n_bands: int = 8
    freq_channels: tuple[str, ...] | None = None  # None -> all channels
    include_band_ratios: bool = True

    def __post_init__(self) -> None:
        if self.window_size < 16 or self.stride < 1 or self.n_bands < 2:
            raise ValueError("invalid window/stride/band configuration")


@dataclass(frozen=True)
class SplitConfig:
    train_frac: float = 0.60
    val_frac: float = 0.20  # remainder (minus embargoes) is the test set
    embargo_windows: int | None = None  # None -> ceil(window_size / stride)

    def __post_init__(self) -> None:
        if not (0 < self.train_frac < 1 and 0 < self.val_frac < 1):
            raise ValueError("fractions must be in (0, 1)")
        if self.train_frac + self.val_frac >= 0.95:
            raise ValueError("train_frac + val_frac must leave room for a test set")


@dataclass(frozen=True)
class IsolationForestConfig:
    n_estimators: int = 300
    max_samples: int | str = "auto"
    target_fpr: float = 0.01  # threshold = (1 - fpr) quantile of normal val scores
    random_state: int = 42


@dataclass(frozen=True)
class LightGBMConfig:
    n_estimators: int = 600
    learning_rate: float = 0.05
    num_leaves: int = 31
    min_child_samples: int = 20
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_lambda: float = 1.0
    early_stopping_rounds: int = 50
    class_weight: str | None = "balanced"
    n_jobs: int = -1
    random_state: int = 42


@dataclass(frozen=True)
class EngineConfig:
    data: DataConfig = field(default_factory=DataConfig)
    cleaning: CleaningConfig = field(default_factory=CleaningConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    split: SplitConfig = field(default_factory=SplitConfig)
    iforest: IsolationForestConfig = field(default_factory=IsolationForestConfig)
    lgbm: LightGBMConfig = field(default_factory=LightGBMConfig)


# --------------------------------------------------------------------------- #
# Step 1a: defensive cleaning (causal)
# --------------------------------------------------------------------------- #
@dataclass
class CleaningReport:
    rows_in: int
    rows_out: int
    bad_or_duplicate_timestamps: int
    out_of_range_samples: int
    missing_samples: int
    imputed_samples: int
    unrecoverable_samples: int

    def summary(self) -> str:
        return (
            f"rows {self.rows_in}->{self.rows_out} | bad/dup ts={self.bad_or_duplicate_timestamps} | "
            f"out-of-range={self.out_of_range_samples} | missing={self.missing_samples} | "
            f"imputed={self.imputed_samples} | unrecoverable={self.unrecoverable_samples}"
        )


class TelemetryCleaner:
    """Schema check, type coercion, physical-limit gating, causal gap filling."""

    def __init__(self, data_cfg: DataConfig, cfg: CleaningConfig) -> None:
        self.data_cfg = data_cfg
        self.cfg = cfg

    def clean(self, raw: pd.DataFrame) -> tuple[pd.DataFrame, CleaningReport]:
        dc = self.data_cfg
        channels = list(dc.channels)
        absent = [c for c in [dc.timestamp_col, *channels] if c not in raw.columns]
        if absent:
            raise SchemaError(f"telemetry is missing required columns: {absent}")

        df = raw.copy()
        n_in = len(df)
        df[dc.timestamp_col] = pd.to_datetime(df[dc.timestamp_col], utc=True, errors="coerce")
        df = df.dropna(subset=[dc.timestamp_col]).sort_values(dc.timestamp_col, kind="stable")
        df = df[~df[dc.timestamp_col].duplicated(keep="last")].reset_index(drop=True)
        ts_dropped = n_in - len(df)

        vals = (
            df[channels]
            .apply(pd.to_numeric, errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .astype("float64")
        )

        oob = 0
        for ch, (lo, hi) in self.cfg.physical_limits.items():
            if ch in vals:
                mask = (vals[ch] < lo) | (vals[ch] > hi)
                oob += int(mask.sum())
                vals.loc[mask, ch] = np.nan

        missing = int(vals.isna().to_numpy().sum())
        # Causal gap filling: carry the last valid reading forward, bounded.
        filled = vals.ffill(limit=self.cfg.max_gap_samples)
        imputed_mask = filled.notna() & vals.isna()

        # Trailing (causal) median on slow channels only; smoothing vibration
        # would erase the impulsive signatures that carry the fault signal.
        for ch in self.cfg.smooth_channels:
            if ch in filled:
                filled[ch] = filled[ch].rolling(self.cfg.smooth_kernel, min_periods=1).median()

        df[channels] = filled
        df[_IMPUTED_FLAG] = imputed_mask.any(axis=1).to_numpy()
        if dc.label_col in df.columns:
            df[dc.label_col] = pd.to_numeric(df[dc.label_col], errors="coerce")

        report = CleaningReport(
            rows_in=n_in,
            rows_out=len(df),
            bad_or_duplicate_timestamps=ts_dropped,
            out_of_range_samples=oob,
            missing_samples=missing,
            imputed_samples=int(imputed_mask.to_numpy().sum()),
            unrecoverable_samples=int(filled.isna().to_numpy().sum()),
        )
        LOGGER.info("Cleaning: %s", report.summary())
        return df, report


# --------------------------------------------------------------------------- #
# Step 1b: rolling time- and frequency-domain features
# --------------------------------------------------------------------------- #
class RollingFeatureExtractor:
    """Vectorised sliding-window feature extraction over multivariate channels.

    Window label = label of its LAST sample (the label is known at the moment
    the window closes, which is what a streaming deployment would see).
    Windows touching an unrecoverable NaN are dropped, never imputed with
    global statistics.
    """

    TIME_STATS = ("mean", "std", "rms", "crest", "kurtosis", "skew", "p2p")

    def __init__(self, data_cfg: DataConfig, cfg: FeatureConfig) -> None:
        self.data_cfg = data_cfg
        self.cfg = cfg
        self.freq_channels = tuple(cfg.freq_channels or data_cfg.channels)
        unknown = set(self.freq_channels) - set(data_cfg.channels)
        if unknown:
            raise ValueError(f"freq_channels not in channels: {sorted(unknown)}")

    @staticmethod
    def feature_columns(frame: pd.DataFrame) -> list[str]:
        return [c for c in frame.columns if c not in META_COLUMNS]

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        dc, fc = self.data_cfg, self.cfg
        channels = list(dc.channels)
        values = df[channels].to_numpy(dtype=np.float64)
        n_samples = len(values)
        if n_samples < fc.window_size:
            raise ValueError(f"need >= {fc.window_size} samples, got {n_samples}")

        n_windows = (n_samples - fc.window_size) // fc.stride + 1
        end_idx = np.arange(n_windows) * fc.stride + fc.window_size - 1
        start_idx = end_idx - fc.window_size + 1

        bad_row = np.isnan(values).any(axis=1)
        bad_win = sliding_window_view(bad_row, fc.window_size)[:: fc.stride].any(axis=1)
        valid = ~bad_win
        if valid.sum() == 0:
            raise ValueError("no valid windows after cleaning")
        dropped = int(bad_win.sum())
        if dropped:
            LOGGER.warning("Dropped %d/%d windows containing unrecoverable gaps", dropped, n_windows)

        feats: dict[str, NDArray[np.float32]] = {}
        for c, ch in enumerate(channels):
            x = sliding_window_view(values[:, c], fc.window_size)[:: fc.stride][valid]
            for name, arr in self._time_features(x).items():
                feats[f"{ch}__{name}"] = arr
            if ch in self.freq_channels:
                for name, arr in self._freq_features(x).items():
                    feats[f"{ch}__{name}"] = arr

        ts = df[dc.timestamp_col].to_numpy()
        out = pd.DataFrame(feats)
        out.insert(0, COL_WINDOW_START, ts[start_idx][valid])
        out.insert(1, COL_WINDOW_END, ts[end_idx][valid])

        if _IMPUTED_FLAG in df.columns:
            imp = sliding_window_view(df[_IMPUTED_FLAG].to_numpy(dtype=float), fc.window_size)
            out.insert(2, COL_IMPUTED_FRAC, imp[:: fc.stride].mean(axis=1)[valid].astype(np.float32))
        else:
            out.insert(2, COL_IMPUTED_FRAC, np.float32(0.0))

        if dc.label_col in df.columns:
            labels = df[dc.label_col].to_numpy()[end_idx][valid]
            if np.isnan(labels).any():
                raise ValueError("label column contains NaN at window ends")
            out[COL_LABEL] = labels.astype(int)
            out[COL_IS_FAULT] = (out[COL_LABEL] != dc.normal_label).astype(int)
        return out.reset_index(drop=True)

    # -- time domain ------------------------------------------------------- #
    @staticmethod
    def _time_features(x: NDArray[np.float64]) -> dict[str, NDArray[np.float32]]:
        with np.errstate(all="ignore"):
            mean = x.mean(axis=1)
            std = x.std(axis=1)
            rms = np.sqrt((x**2).mean(axis=1))
            peak = np.abs(x).max(axis=1)
            res = {
                "mean": mean,
                "std": std,
                "rms": rms,
                "crest": peak / (rms + _EPS),
                "kurtosis": sp_stats.kurtosis(x, axis=1, fisher=True),
                "skew": sp_stats.skew(x, axis=1),
                "p2p": x.max(axis=1) - x.min(axis=1),
            }
        return {k: np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32) for k, v in res.items()}

    # -- frequency domain -------------------------------------------------- #
    def _freq_features(self, x: NDArray[np.float64]) -> dict[str, NDArray[np.float32]]:
        fc = self.cfg
        n = x.shape[1]
        win = np.hanning(n)
        spec = np.fft.rfft((x - x.mean(axis=1, keepdims=True)) * win, axis=1)
        power = (np.abs(spec) ** 2) / (win**2).sum()
        freqs = np.fft.rfftfreq(n, d=1.0 / self.data_cfg.sampling_rate_hz)
        total = power.sum(axis=1) + _EPS

        edges = np.linspace(0.0, self.data_cfg.sampling_rate_hz / 2.0, fc.n_bands + 1)
        band_of_bin = np.clip(np.digitize(freqs, edges[1:-1]), 0, fc.n_bands - 1)
        band_energy = np.stack(
            [power[:, band_of_bin == b].sum(axis=1) for b in range(fc.n_bands)], axis=1
        )

        p = power / total[:, None]
        res: dict[str, NDArray[np.float64]] = {}
        for b in range(fc.n_bands):
            res[f"band{b}_logE"] = np.log10(band_energy[:, b] + _EPS)
            if fc.include_band_ratios:
                res[f"band{b}_ratio"] = band_energy[:, b] / total
        res["spec_logE"] = np.log10(total)
        res["spec_centroid"] = (power * freqs).sum(axis=1) / total
        res["spec_entropy"] = -(p * np.log(p + _EPS)).sum(axis=1) / math.log(power.shape[1])
        res["spec_dominant_hz"] = freqs[power.argmax(axis=1)]
        return {k: np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32) for k, v in res.items()}


# --------------------------------------------------------------------------- #
# Step 1c: strictly chronological split with embargo
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SplitIndices:
    train: NDArray[np.int64]
    val: NDArray[np.int64]
    test: NDArray[np.int64]
    embargo: int


class ChronologicalSplitter:
    def __init__(self, split_cfg: SplitConfig, feat_cfg: FeatureConfig) -> None:
        self.cfg = split_cfg
        self.embargo = (
            split_cfg.embargo_windows
            if split_cfg.embargo_windows is not None
            else math.ceil(feat_cfg.window_size / feat_cfg.stride)
        )

    def split(self, n_windows: int) -> SplitIndices:
        n_train = int(n_windows * self.cfg.train_frac)
        n_val = int(n_windows * self.cfg.val_frac)
        val_start = n_train + self.embargo
        test_start = val_start + n_val + self.embargo
        if min(n_train, n_val, n_windows - test_start) < 1:
            raise ValueError("not enough windows for the requested split + embargo")
        return SplitIndices(
            train=np.arange(0, n_train),
            val=np.arange(val_start, val_start + n_val),
            test=np.arange(test_start, n_windows),
            embargo=self.embargo,
        )

    def time_series_folds(self, n_windows: int, n_splits: int = 5) -> Iterator[tuple[NDArray, NDArray]]:
        """Expanding-window CV with an embargo gap (for later hyper-parameter search)."""
        yield from TimeSeriesSplit(n_splits=n_splits, gap=self.embargo).split(np.arange(n_windows))

    @staticmethod
    def verify_no_leakage(frame: pd.DataFrame, split: SplitIndices) -> None:
        """Hard guarantee: no sample of a later split is inside an earlier window."""
        t = frame
        pairs = (("train", "val", split.train, split.val), ("val", "test", split.val, split.test))
        for a_name, b_name, a, b in pairs:
            a_end = t[COL_WINDOW_END].iloc[a].max()
            b_start = t[COL_WINDOW_START].iloc[b].min()
            if not a_end < b_start:
                raise LeakageError(f"{a_name} windows (end {a_end}) overlap {b_name} (start {b_start})")
        LOGGER.info("Leakage check passed (embargo=%d windows)", split.embargo)


# --------------------------------------------------------------------------- #
# Step 2: models
# --------------------------------------------------------------------------- #
class FaultDetector(ABC):
    """Common contract: higher `anomaly_score` == more likely faulty."""

    name: str
    threshold: float

    @abstractmethod
    def fit(self, X_train: pd.DataFrame, y_train: NDArray, X_val: pd.DataFrame, y_val: NDArray) -> "FaultDetector":
        ...

    @abstractmethod
    def anomaly_score(self, X: pd.DataFrame) -> NDArray[np.float64]:
        ...

    def predict(self, X: pd.DataFrame) -> NDArray[np.int64]:
        return (self.anomaly_score(X) >= self.threshold).astype(np.int64)


class IsolationForestDetector(FaultDetector):
    """Unsupervised baseline: trained on NORMAL train windows only.

    The alert threshold is calibrated on validation-normal scores to hit a
    target false-positive rate, so no fault labels are needed to fit the model.
    """

    name = "IsolationForest"

    def __init__(self, cfg: IsolationForestConfig, normal_label: int) -> None:
        self.cfg = cfg
        self.normal_label = normal_label
        self.scaler = RobustScaler()
        self.model = IsolationForest(
            n_estimators=cfg.n_estimators,
            max_samples=cfg.max_samples,
            random_state=cfg.random_state,
            n_jobs=-1,
        )
        self.columns: list[str] = []
        self.threshold = 0.0

    def fit(self, X_train, y_train, X_val, y_val):
        self.columns = list(X_train.columns)
        normal = X_train.loc[np.asarray(y_train) == self.normal_label]
        if normal.empty:
            raise ValueError("no normal windows in training split")
        Z = self.scaler.fit_transform(normal.to_numpy())
        self.model.fit(Z)

        val_normal = X_val.loc[np.asarray(y_val) == self.normal_label]
        ref = val_normal if len(val_normal) else normal
        scores = self.anomaly_score(ref)
        self.threshold = float(np.quantile(scores, 1.0 - self.cfg.target_fpr))
        LOGGER.info("IsolationForest threshold=%.4f (target FPR=%.3f, n_ref=%d)",
                    self.threshold, self.cfg.target_fpr, len(ref))
        return self

    def anomaly_score(self, X):
        Z = self.scaler.transform(X[self.columns].to_numpy())
        return -self.model.score_samples(Z)  # higher == more anomalous


class LightGBMFaultClassifier(FaultDetector):
    """Supervised multi-class classifier; fault score = 1 - P(normal)."""

    name = "LightGBM"

    def __init__(self, cfg: LightGBMConfig, normal_label: int) -> None:
        self.cfg = cfg
        self.normal_label = normal_label
        self.model: lgb.LGBMClassifier | None = None
        self.columns: list[str] = []
        self.threshold = 0.5

    def fit(self, X_train, y_train, X_val, y_val):
        y_train, y_val = np.asarray(y_train), np.asarray(y_val)
        if len(np.unique(y_train)) < 2:
            raise ValueError("training split must contain at least two classes")
        self.columns = list(X_train.columns)
        c = self.cfg
        self.model = lgb.LGBMClassifier(
            n_estimators=c.n_estimators,
            learning_rate=c.learning_rate,
            num_leaves=c.num_leaves,
            min_child_samples=c.min_child_samples,
            subsample=c.subsample,
            subsample_freq=1,
            colsample_bytree=c.colsample_bytree,
            reg_lambda=c.reg_lambda,
            class_weight=c.class_weight,
            n_jobs=c.n_jobs,
            random_state=c.random_state,
            verbose=-1,
        )
        known = np.isin(y_val, np.unique(y_train))  # eval set may only contain seen classes
        self.model.fit(
            X_train,
            y_train,
            eval_set=[(X_val.loc[known], y_val[known])],
            callbacks=[lgb.early_stopping(c.early_stopping_rounds, verbose=False), lgb.log_evaluation(0)],
        )
        best = getattr(self.model, "best_iteration_", None)
        LOGGER.info("LightGBM trained (best_iteration=%s, classes=%s)", best, list(self.model.classes_))
        self._calibrate_threshold(X_val, y_val)
        return self

    def _calibrate_threshold(self, X_val: pd.DataFrame, y_val: NDArray) -> None:
        y_bin = (y_val != self.normal_label).astype(int)
        if y_bin.sum() == 0 or y_bin.sum() == len(y_bin):
            LOGGER.warning("Validation lacks both classes; keeping default threshold 0.5")
            return
        score = self.anomaly_score(X_val)
        prec, rec, thr = precision_recall_curve(y_bin, score)
        f1 = 2 * prec[:-1] * rec[:-1] / (prec[:-1] + rec[:-1] + _EPS)
        self.threshold = float(thr[int(np.argmax(f1))])
        LOGGER.info("LightGBM threshold=%.4f (max validation F1=%.4f)", self.threshold, f1.max())

    def predict_proba(self, X: pd.DataFrame) -> NDArray[np.float64]:
        assert self.model is not None, "model not fitted"
        return self.model.predict_proba(X[self.columns])

    def anomaly_score(self, X):
        assert self.model is not None, "model not fitted"
        proba = self.predict_proba(X)
        normal_col = list(self.model.classes_).index(self.normal_label)
        return 1.0 - proba[:, normal_col]

    def predict_class(self, X: pd.DataFrame) -> NDArray:
        assert self.model is not None, "model not fitted"
        return self.model.predict(X[self.columns])

    def feature_importance(self, top_k: int = 15) -> pd.DataFrame:
        assert self.model is not None, "model not fitted"
        gain = self.model.booster_.feature_importance(importance_type="gain")
        df = pd.DataFrame({"feature": self.columns, "gain": gain})
        df["gain_pct"] = 100.0 * df["gain"] / (df["gain"].sum() + _EPS)
        return df.sort_values("gain", ascending=False).head(top_k).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BinaryMetrics:
    precision: float
    recall: float
    f1: float
    fpr: float
    roc_auc: float
    pr_auc: float
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int


def binary_metrics(y_true: NDArray, score: NDArray, threshold: float) -> BinaryMetrics:
    y_true = np.asarray(y_true).astype(int)
    pred = (score >= threshold).astype(int)
    tp = int(((pred == 1) & (y_true == 1)).sum())
    fp = int(((pred == 1) & (y_true == 0)).sum())
    fn = int(((pred == 0) & (y_true == 1)).sum())
    tn = int(((pred == 0) & (y_true == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    both = 0 < y_true.sum() < len(y_true)
    return BinaryMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        fpr=fp / (fp + tn) if fp + tn else 0.0,
        roc_auc=float(roc_auc_score(y_true, score)) if both else float("nan"),
        pr_auc=float(average_precision_score(y_true, score)) if both else float("nan"),
        threshold=float(threshold),
        tp=tp, fp=fp, fn=fn, tn=tn,
    )


@dataclass
class EvaluationReport:
    split_summary: pd.DataFrame
    comparison: pd.DataFrame
    lgbm_class_report: str
    lgbm_confusion: pd.DataFrame
    lgbm_top_features: pd.DataFrame
    cleaning: CleaningReport

    def render(self) -> str:
        fmt = lambda v: f"{v:.4f}"  # noqa: E731
        bar = "=" * 78
        return "\n".join([
            bar, "MACHINASENSE | HELD-OUT TEST EVALUATION (chronologically future data)", bar,
            "", "Split summary (windows):", self.split_summary.to_string(),
            "", "Binary fault detection: IsolationForest (baseline) vs LightGBM",
            self.comparison.to_string(float_format=fmt),
            "", "LightGBM multi-class report:", self.lgbm_class_report,
            "LightGBM confusion matrix (rows=true, cols=pred):", self.lgbm_confusion.to_string(),
            "", "Top LightGBM features by gain:", self.lgbm_top_features.to_string(float_format=lambda v: f"{v:.2f}"),
            "", f"Data cleaning: {self.cleaning.summary()}", bar,
        ])


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class MachinaSenseEngine:
    """End-to-end pipeline: raw telemetry -> features -> dual-model evaluation."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        c = self.config
        self.cleaner = TelemetryCleaner(c.data, c.cleaning)
        self.extractor = RollingFeatureExtractor(c.data, c.features)
        self.splitter = ChronologicalSplitter(c.split, c.features)
        self.iforest = IsolationForestDetector(c.iforest, c.data.normal_label)
        self.lgbm = LightGBMFaultClassifier(c.lgbm, c.data.normal_label)
        self.feature_names_: list[str] = []
        self.splits_: dict[str, pd.DataFrame] = {}
        self.last_cleaning_report_: CleaningReport | None = None

    # -- Step 1 ------------------------------------------------------------ #
    def build_features(self, raw: pd.DataFrame) -> pd.DataFrame:
        with timed("Cleaning + feature extraction"):
            clean, report = self.cleaner.clean(raw)
            self.last_cleaning_report_ = report
            feats = self.extractor.transform(clean)
        LOGGER.info("Built %d windows x %d features", len(feats), len(self.extractor.feature_columns(feats)))
        return feats

    # -- Step 2 ------------------------------------------------------------ #
    def fit_evaluate(self, raw: pd.DataFrame) -> EvaluationReport:
        dc = self.config.data
        feats = self.build_features(raw)
        if COL_LABEL not in feats:
            raise SchemaError(f"'{dc.label_col}' column required for training")

        split = self.splitter.split(len(feats))
        self.splitter.verify_no_leakage(feats, split)
        self.feature_names_ = self.extractor.feature_columns(feats)

        tr, va, te = (feats.iloc[i] for i in (split.train, split.val, split.test))
        X_tr, X_va, X_te = (d[self.feature_names_] for d in (tr, va, te))
        y_tr, y_va, y_te = (d[COL_LABEL].to_numpy() for d in (tr, va, te))

        with timed("IsolationForest fit"):
            self.iforest.fit(X_tr, y_tr, X_va, y_va)
        with timed("LightGBM fit"):
            self.lgbm.fit(X_tr, y_tr, X_va, y_va)

        self.splits_ = {"train": tr, "val": va, "test": te}
        return self._evaluate(te, X_te, y_te, self.splits_)

    def _evaluate(self, te: pd.DataFrame, X_te: pd.DataFrame, y_te: NDArray,
                  parts: Mapping[str, pd.DataFrame]) -> EvaluationReport:
        dc = self.config.data
        y_bin = (y_te != dc.normal_label).astype(int)

        rows = {
            det.name: binary_metrics(y_bin, det.anomaly_score(X_te), det.threshold).__dict__
            for det in (self.iforest, self.lgbm)
        }
        comparison = pd.DataFrame(rows).T[
            ["precision", "recall", "f1", "fpr", "roc_auc", "pr_auc", "threshold", "tp", "fp", "fn", "tn"]
        ]
        for col in ("tp", "fp", "fn", "tn"):
            comparison[col] = comparison[col].astype(int)

        y_pred = self.lgbm.predict_class(X_te)
        labels = sorted(set(np.unique(y_te)) | set(np.unique(y_pred)))
        names = [dc.class_names.get(int(k), str(k)) for k in labels]
        class_report = classification_report(y_te, y_pred, labels=labels, target_names=names,
                                             digits=4, zero_division=0)
        confusion = pd.DataFrame(confusion_matrix(y_te, y_pred, labels=labels),
                                 index=[f"true_{n}" for n in names], columns=[f"pred_{n}" for n in names])

        split_summary = pd.DataFrame({
            k: {
                "windows": len(d),
                "start": str(d[COL_WINDOW_START].min()),
                "end": str(d[COL_WINDOW_END].max()),
                "fault_rate": round(float(d[COL_IS_FAULT].mean()), 4),
            }
            for k, d in parts.items()
        }).T

        report = EvaluationReport(
            split_summary=split_summary,
            comparison=comparison,
            lgbm_class_report=class_report,
            lgbm_confusion=confusion,
            lgbm_top_features=self.lgbm.feature_importance(),
            cleaning=self.last_cleaning_report_,  # type: ignore[arg-type]
        )
        return report

    # -- Inference helper (consumed by Steps 3-7) -------------------------- #
    def score_features(self, feats: pd.DataFrame) -> pd.DataFrame:
        """Batch scoring on already-extracted features."""
        X = feats[self.feature_names_]
        names = self.config.data.class_names
        cls = self.lgbm.predict_class(X)
        return pd.DataFrame({
            COL_WINDOW_END: feats[COL_WINDOW_END].to_numpy(),
            "iforest_score": self.iforest.anomaly_score(X),
            "iforest_alert": self.iforest.predict(X),
            "lgbm_fault_prob": self.lgbm.anomaly_score(X),
            "lgbm_alert": self.lgbm.predict(X),
            "predicted_class": [names.get(int(k), str(k)) for k in cls],
        })

    def score_raw(self, raw: pd.DataFrame) -> pd.DataFrame:
        return self.score_features(self.build_features(raw))

    # -- Persistence ------------------------------------------------------- #
    def save(self, path: str | Path) -> None:
        joblib.dump({"config": self.config, "iforest": self.iforest, "lgbm": self.lgbm,
                     "feature_names": self.feature_names_}, path, compress=3)
        LOGGER.info("Engine saved to %s", path)

    @classmethod
    def load(cls, path: str | Path) -> "MachinaSenseEngine":
        blob = joblib.load(path)
        eng = cls(blob["config"])
        eng.iforest, eng.lgbm, eng.feature_names_ = blob["iforest"], blob["lgbm"], blob["feature_names"]
        return eng


# --------------------------------------------------------------------------- #
# Synthetic telemetry (demo / unit tests; swap for CNC/bearing datasets)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SyntheticConfig:
    sampling_rate_hz: float = 1000.0
    cycles: int = 6
    # (label, seconds): normal -> bearing wear -> normal -> imbalance
    segments: tuple[tuple[int, float], ...] = ((0, 30.0), (1, 20.0), (0, 15.0), (2, 20.0))
    start: str = "2026-01-05T08:00:00Z"
    dropout_prob: float = 5e-4  # isolated raw NaNs, exercises the cleaner
    seed: int = 7


class SyntheticTelemetryGenerator:
    """Bearing-style telemetry with gradual degradation and varying load.

    Fault episodes ramp severity 0.2 -> 1.0, so early windows are subtle and the
    `fault_severity` / `episode_id` columns support Step 3 lead-time analysis.
    """

    SHAFT_HZ = 50.0
    DEFECT_HZ = 87.0
    RESONANCE_HZ = 300.0

    def __init__(self, cfg: SyntheticConfig | None = None) -> None:
        self.cfg = cfg or SyntheticConfig()

    def generate(self) -> pd.DataFrame:
        cfg = self.cfg
        rng = np.random.default_rng(cfg.seed)
        fs = cfg.sampling_rate_hz
        kernel_t = np.arange(40)
        kernel = np.exp(-kernel_t / 8.0) * np.sin(2 * np.pi * self.RESONANCE_HZ * kernel_t / fs)

        chunks, offset, episode = [], 0, 0
        for _ in range(cfg.cycles):
            for label, seconds in cfg.segments:
                n = int(seconds * fs)
                t = (np.arange(n) + offset) / fs
                sev = np.linspace(0.2, 1.0, n) if label else np.zeros(n)
                load = rng.uniform(0.9, 1.1)  # operating-condition shift per segment
                w = 2 * np.pi * self.SHAFT_HZ * t

                vx = load * (0.5 * np.sin(w) + 0.15 * np.sin(2 * w + 0.5)) + rng.normal(0, 0.12, n)
                vy = load * (0.5 * np.cos(w) + 0.10 * np.sin(3 * w)) + rng.normal(0, 0.12, n)
                temp = 60 + 3 * load + lfilter([0.02], [1, -0.98], rng.normal(0, 0.5, n))
                cur = 10 * load + 0.4 * np.sin(w) + rng.normal(0, 0.15, n)
                ac = rng.normal(0, 0.2, n) + 0.1 * load * np.sin(w)

                if label == 1:  # bearing wear: impulsive resonance bursts at defect frequency
                    pos = (np.arange(0, n, fs / self.DEFECT_HZ) + rng.normal(0, 1.0, int(np.ceil(n * self.DEFECT_HZ / fs)))).astype(int)
                    pos = pos[(pos >= 0) & (pos < n)]
                    imp = np.zeros(n)
                    imp[pos] = rng.uniform(0.7, 1.3, len(pos))
                    burst = np.convolve(imp, kernel, mode="same")
                    vx += 1.5 * sev * burst
                    vy += 1.0 * sev * burst
                    ac += 2.0 * sev * burst
                    temp += 8 * sev
                    cur += 0.8 * sev
                elif label == 2:  # imbalance: growing 1x component
                    vx += 1.2 * sev * np.sin(w)
                    vy += 1.2 * sev * np.cos(w)
                    temp += 5 * sev
                    cur += 1.2 * sev
                    ac += 0.3 * sev * rng.normal(0, 1, n)

                if label:
                    episode += 1
                chunks.append(pd.DataFrame({
                    "vib_x": vx, "vib_y": vy, "temperature_c": temp,
                    "motor_current_a": cur, "acoustic_db": ac,
                    "fault_label": label, "fault_severity": sev,
                    "episode_id": episode if label else 0,
                }))
                offset += n

        df = pd.concat(chunks, ignore_index=True)
        if cfg.dropout_prob > 0:
            mask = rng.random((len(df), len(DEFAULT_CHANNELS))) < cfg.dropout_prob
            df[list(DEFAULT_CHANNELS)] = df[list(DEFAULT_CHANNELS)].mask(mask)
        df.insert(0, "timestamp", pd.to_datetime(cfg.start) + pd.to_timedelta(np.arange(len(df)) / fs, unit="s"))
        return df


# --------------------------------------------------------------------------- #
# Demo entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    configure_logging()
    raw = SyntheticTelemetryGenerator().generate()
    LOGGER.info("Synthetic telemetry: %d samples, %.0f s", len(raw), len(raw) / 1000.0)

    config = EngineConfig(
        features=FeatureConfig(freq_channels=("vib_x", "vib_y", "motor_current_a", "acoustic_db")),
    )
    engine = MachinaSenseEngine(config)
    report = engine.fit_evaluate(raw)
    print("\n" + report.render())
    engine.save("machinasense_engine.joblib")


if __name__ == "__main__":
    main()
