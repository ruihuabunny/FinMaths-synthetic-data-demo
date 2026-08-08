"""Private task-seed FP/FN and maximum-statistic calibration for F2A v5."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from statistics import NormalDist
from typing import Any, Iterable, Mapping, Sequence


SIGNATURES = tuple(f"{value:03b}" for value in range(8))
FAMILIES = ("X", "U", "T")


def _validate_signature(value: str) -> str:
    if value not in SIGNATURES:
        raise ValueError("XUT signature must be one of 000..111")
    return value


def _wilson_bound(
    successes: int,
    trials: int,
    *,
    confidence: float = 0.95,
    upper: bool,
) -> float | None:
    """One-sided Wilson score bound for a binomial task-level event."""

    if trials == 0:
        return None
    if not 0 <= successes <= trials:
        raise ValueError("binomial count is outside its denominator")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must lie in (0.5, 1)")
    z = NormalDist().inv_cdf(confidence)
    proportion = successes / trials
    denominator = 1.0 + z * z / trials
    center = (proportion + z * z / (2.0 * trials)) / denominator
    radius = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z * z / (4.0 * trials * trials)
        )
        / denominator
    )
    return min(1.0, center + radius) if upper else max(0.0, center - radius)


@dataclass(frozen=True)
class CandidateCalibration:
    family: str
    public_active: bool
    private_active: bool
    standardized_estimation_error: float | None = None

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError("candidate calibration family must be X, U or T")
        if self.standardized_estimation_error is not None and not math.isfinite(
            self.standardized_estimation_error
        ):
            raise ValueError("standardized candidate error must be finite")


@dataclass(frozen=True)
class TaskCalibrationRecord:
    task_seed_id: str
    public_signature: str
    private_signature: str
    mutated: bool
    stratum: Mapping[str, Any]
    candidates: tuple[CandidateCalibration, ...] = ()

    def __post_init__(self) -> None:
        if not self.task_seed_id:
            raise ValueError("task calibration record requires a stable seed id")
        _validate_signature(self.public_signature)
        _validate_signature(self.private_signature)

    @property
    def any_fp(self) -> bool:
        return any(
            public == "1" and private == "0"
            for public, private in zip(self.public_signature, self.private_signature)
        )

    @property
    def any_fn(self) -> bool:
        return any(
            public == "0" and private == "1"
            for public, private in zip(self.public_signature, self.private_signature)
        )

    @property
    def hamming_error(self) -> int:
        return sum(
            public != private
            for public, private in zip(self.public_signature, self.private_signature)
        )


@dataclass(frozen=True)
class CalibrationGates:
    minimum_task_seeds: int = 2000
    clean_task_any_fp_upper_95: float = 0.01
    mutated_task_any_fn_upper_95: float = 0.05
    family_fp_upper_95: float = 0.01
    family_fn_upper_95: float = 0.05
    exact_signature_accuracy_lower_95: float = 0.95


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _nonnegative_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _validated_binomial_summary(
    summary: Any,
    *,
    name: str,
    trials: int,
    bound_key: str,
    upper: bool,
    expected_count: int | None = None,
) -> tuple[int, float]:
    if not isinstance(summary, Mapping):
        raise ValueError(f"{name} summary is missing")
    count = _nonnegative_count(summary.get("count"), f"{name} count")
    if count > trials or (expected_count is not None and count != expected_count):
        raise ValueError(f"{name} count disagrees with its denominator or confusion matrix")
    expected_rate = _rate(count, trials)
    raw_rate = summary.get("rate")
    if raw_rate is None or not math.isfinite(float(raw_rate)) or float(raw_rate) != expected_rate:
        raise ValueError(f"{name} rate disagrees with its counts")
    expected_bound = _wilson_bound(count, trials, upper=upper)
    raw_bound = summary.get(bound_key)
    if (
        expected_bound is None
        or raw_bound is None
        or not math.isfinite(float(raw_bound))
        or float(raw_bound) != expected_bound
    ):
        raise ValueError(f"{name} confidence bound disagrees with its counts")
    return count, expected_bound


def _empirical_quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    if not 0.0 < probability <= 1.0:
        raise ValueError("empirical quantile probability must lie in (0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def calibration_report_checksum(report: Mapping[str, Any]) -> str:
    """Return the stable identity of a private cohort report."""

    return sha256(_canonical_json_bytes(report)).hexdigest()


def _stratified_metrics(tasks: Sequence[TaskCalibrationRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, tuple[Mapping[str, Any], list[TaskCalibrationRecord]]] = {}
    for task in tasks:
        key = json.dumps(
            dict(task.stratum),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        grouped.setdefault(key, (dict(task.stratum), []))[1].append(task)
    result = []
    for key in sorted(grouped):
        stratum, members = grouped[key]
        clean = [item for item in members if not item.mutated]
        mutated = [item for item in members if item.mutated]
        family_metrics = {}
        for index, family in enumerate(FAMILIES):
            negatives = sum(item.private_signature[index] == "0" for item in members)
            positives = sum(item.private_signature[index] == "1" for item in members)
            false_positives = sum(
                item.public_signature[index] == "1"
                and item.private_signature[index] == "0"
                for item in members
            )
            false_negatives = sum(
                item.public_signature[index] == "0"
                and item.private_signature[index] == "1"
                for item in members
            )
            family_metrics[family] = {
                "fpr": _rate(false_positives, negatives),
                "fnr": _rate(false_negatives, positives),
            }
        result.append(
            {
                "stratum": stratum,
                "task_count": len(members),
                "clean_any_fp_rate": _rate(sum(item.any_fp for item in clean), len(clean)),
                "mutated_any_fn_rate": _rate(
                    sum(item.any_fn for item in mutated), len(mutated)
                ),
                "exact_signature_accuracy": _rate(
                    sum(item.public_signature == item.private_signature for item in members),
                    len(members),
                ),
                "mean_hamming_error": sum(item.hamming_error for item in members)
                / len(members),
                "family_metrics": family_metrics,
            }
        )
    return result


def calibration_report(
    records: Iterable[TaskCalibrationRecord],
    *,
    gates: CalibrationGates | None = None,
    maximum_statistic_probability: float = 0.99,
    cohort_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate only at the independent complete-task seed level."""

    contract = gates or CalibrationGates()
    tasks = tuple(records)
    ids = [item.task_seed_id for item in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("task-seed calibration units must be independent and unique")
    confusion = {
        truth: {estimate: 0 for estimate in SIGNATURES} for truth in SIGNATURES
    }
    for item in tasks:
        confusion[item.private_signature][item.public_signature] += 1
    if sum(sum(row.values()) for row in confusion.values()) != len(tasks):
        raise AssertionError("signature confusion matrix does not conserve task count")

    clean = tuple(item for item in tasks if not item.mutated)
    mutated = tuple(item for item in tasks if item.mutated)
    clean_fp = sum(item.any_fp for item in clean)
    mutated_fn = sum(item.any_fn for item in mutated)
    exact = sum(item.public_signature == item.private_signature for item in tasks)
    family_metrics: dict[str, Any] = {}
    for index, family in enumerate(FAMILIES):
        truth_negative = sum(item.private_signature[index] == "0" for item in tasks)
        truth_positive = sum(item.private_signature[index] == "1" for item in tasks)
        fp = sum(
            item.public_signature[index] == "1" and item.private_signature[index] == "0"
            for item in tasks
        )
        fn = sum(
            item.public_signature[index] == "0" and item.private_signature[index] == "1"
            for item in tasks
        )
        family_metrics[family] = {
            "false_positive_count": fp,
            "truth_negative_count": truth_negative,
            "fpr": _rate(fp, truth_negative),
            "fpr_upper_95": _wilson_bound(fp, truth_negative, upper=True),
            "false_negative_count": fn,
            "truth_positive_count": truth_positive,
            "fnr": _rate(fn, truth_positive),
            "fnr_upper_95": _wilson_bound(fn, truth_positive, upper=True),
        }

    candidate_counts = {
        "false_positive": 0,
        "truth_negative": 0,
        "false_negative": 0,
        "truth_positive": 0,
    }
    maxima: list[float] = []
    for task in tasks:
        standardized = []
        for candidate in task.candidates:
            if candidate.private_active:
                candidate_counts["truth_positive"] += 1
                if not candidate.public_active:
                    candidate_counts["false_negative"] += 1
            else:
                candidate_counts["truth_negative"] += 1
                if candidate.public_active:
                    candidate_counts["false_positive"] += 1
            if candidate.standardized_estimation_error is not None:
                standardized.append(abs(candidate.standardized_estimation_error))
        if standardized:
            maxima.append(max(standardized))

    candidate_metrics = {
        **candidate_counts,
        "fpr": _rate(
            candidate_counts["false_positive"],
            candidate_counts["truth_negative"],
        ),
        "fnr": _rate(
            candidate_counts["false_negative"],
            candidate_counts["truth_positive"],
        ),
        "independence_warning": (
            "candidate rows are descriptive only; confidence bounds use task seeds"
        ),
    }
    clean_upper = _wilson_bound(clean_fp, len(clean), upper=True)
    mutated_upper = _wilson_bound(mutated_fn, len(mutated), upper=True)
    exact_lower = _wilson_bound(exact, len(tasks), upper=False)
    family_gate = all(
        metric[bound] is not None and metric[bound] <= threshold
        for metric in family_metrics.values()
        for bound, threshold in (
            ("fpr_upper_95", contract.family_fp_upper_95),
            ("fnr_upper_95", contract.family_fn_upper_95),
        )
    )
    gate_results = {
        "minimum_task_seeds": len(tasks) >= contract.minimum_task_seeds,
        "clean_task_any_fp": clean_upper is not None
        and clean_upper <= contract.clean_task_any_fp_upper_95,
        "mutated_task_any_fn": mutated_upper is not None
        and mutated_upper <= contract.mutated_task_any_fn_upper_95,
        "family_fp_fn": family_gate,
        "exact_signature_accuracy": exact_lower is not None
        and exact_lower >= contract.exact_signature_accuracy_lower_95,
    }
    result = {
        "independent_unit": "task_seed",
        "confidence_bound_method": "one_sided_wilson_95",
        "empirical_method": "independent_parametric_bootstrap_task_replicates",
        "multiple_scan_method": "task_seed_empirical_maximum_statistic",
        "task_count": len(tasks),
        "clean_task_count": len(clean),
        "mutated_task_count": len(mutated),
        "clean_any_fp": {
            "count": clean_fp,
            "rate": _rate(clean_fp, len(clean)),
            "upper_95": clean_upper,
        },
        "mutated_any_fn": {
            "count": mutated_fn,
            "rate": _rate(mutated_fn, len(mutated)),
            "upper_95": mutated_upper,
        },
        "exact_signature_accuracy": {
            "count": exact,
            "rate": _rate(exact, len(tasks)),
            "lower_95": exact_lower,
        },
        "mean_hamming_error": (
            sum(item.hamming_error for item in tasks) / len(tasks) if tasks else None
        ),
        "family_metrics": family_metrics,
        "candidate_metrics": candidate_metrics,
        "stratified_metrics": _stratified_metrics(tasks),
        "signature_confusion_matrix": confusion,
        "bootstrap_maximum_statistic": {
            "probability": maximum_statistic_probability,
            "task_replicate_count": len(maxima),
            "critical_value": _empirical_quantile(
                maxima,
                maximum_statistic_probability,
            ),
        },
        "gate_results": gate_results,
        "release_gate_passed": all(gate_results.values()),
    }
    if cohort_contract is not None:
        result["cohort_contract"] = dict(cohort_contract)
    return result


def validate_release_calibration_report(
    report: Mapping[str, Any],
    *,
    gates: CalibrationGates | None = None,
) -> None:
    """Reject a release unless the report itself proves every frozen gate.

    The private authoring CLI calls this at the publication boundary.  It does
    not trust the report's aggregate boolean without rechecking the underlying
    counts, confidence bounds, and confusion-matrix conservation.
    """

    contract = gates or CalibrationGates()
    if report.get("independent_unit") != "task_seed":
        raise ValueError("release calibration must use independent task seeds")
    if (
        report.get("confidence_bound_method") != "one_sided_wilson_95"
        or report.get("empirical_method")
        != "independent_parametric_bootstrap_task_replicates"
        or report.get("multiple_scan_method")
        != "task_seed_empirical_maximum_statistic"
    ):
        raise ValueError("release calibration uses an unfrozen statistical method")
    task_count = _nonnegative_count(report.get("task_count"), "release task_count")
    if task_count < contract.minimum_task_seeds:
        raise ValueError(
            f"release calibration requires at least {contract.minimum_task_seeds} task seeds"
        )
    clean_task_count = _nonnegative_count(
        report.get("clean_task_count"), "clean task_count"
    )
    mutated_task_count = _nonnegative_count(
        report.get("mutated_task_count"), "mutated task_count"
    )
    if clean_task_count <= 0 or mutated_task_count <= 0:
        raise ValueError("release calibration requires both clean and mutated task seeds")
    if clean_task_count + mutated_task_count != task_count:
        raise ValueError("clean and mutated task counts do not conserve task_count")

    confusion = report.get("signature_confusion_matrix")
    if not isinstance(confusion, Mapping) or set(confusion) != set(SIGNATURES):
        raise ValueError("release calibration requires a complete 8x8 confusion matrix")
    counts: dict[str, dict[str, int]] = {}
    for truth in SIGNATURES:
        row = confusion[truth]
        if not isinstance(row, Mapping) or set(row) != set(SIGNATURES):
            raise ValueError("release calibration confusion matrix is malformed")
        counts[truth] = {
            estimate: _nonnegative_count(
                row[estimate],
                f"confusion[{truth}][{estimate}]",
            )
            for estimate in SIGNATURES
        }
    total = sum(sum(row.values()) for row in counts.values())
    if total != task_count:
        raise ValueError("release calibration confusion matrix does not conserve task count")

    _, clean_upper = _validated_binomial_summary(
        report.get("clean_any_fp"),
        name="clean task-level any-FP",
        trials=clean_task_count,
        bound_key="upper_95",
        upper=True,
    )
    _, mutated_upper = _validated_binomial_summary(
        report.get("mutated_any_fn"),
        name="mutated task-level any-FN",
        trials=mutated_task_count,
        bound_key="upper_95",
        upper=True,
    )
    exact_count = sum(counts[signature][signature] for signature in SIGNATURES)
    _, exact_lower = _validated_binomial_summary(
        report.get("exact_signature_accuracy"),
        name="exact-signature accuracy",
        trials=task_count,
        bound_key="lower_95",
        upper=False,
        expected_count=exact_count,
    )
    if clean_upper > contract.clean_task_any_fp_upper_95:
        raise ValueError("clean task-level any-FP confidence bound fails the release gate")
    if mutated_upper > contract.mutated_task_any_fn_upper_95:
        raise ValueError("mutated task-level any-FN confidence bound fails the release gate")
    if exact_lower < contract.exact_signature_accuracy_lower_95:
        raise ValueError("exact-signature confidence bound fails the release gate")

    family_metrics = report.get("family_metrics")
    if not isinstance(family_metrics, Mapping) or set(family_metrics) != set(FAMILIES):
        raise ValueError("release calibration must report X/U/T family metrics")
    family_gate = True
    for index, family in enumerate(FAMILIES):
        metric = family_metrics[family]
        if not isinstance(metric, Mapping):
            raise ValueError(f"{family} calibration metric is malformed")
        truth_negative = sum(
            count
            for truth, row in counts.items()
            if truth[index] == "0"
            for count in row.values()
        )
        truth_positive = task_count - truth_negative
        false_positive = sum(
            count
            for truth, row in counts.items()
            if truth[index] == "0"
            for estimate, count in row.items()
            if estimate[index] == "1"
        )
        false_negative = sum(
            count
            for truth, row in counts.items()
            if truth[index] == "1"
            for estimate, count in row.items()
            if estimate[index] == "0"
        )
        for key, expected in (
            ("truth_negative_count", truth_negative),
            ("truth_positive_count", truth_positive),
            ("false_positive_count", false_positive),
            ("false_negative_count", false_negative),
        ):
            if _nonnegative_count(metric.get(key), f"{family} {key}") != expected:
                raise ValueError(f"{family} counts disagree with the confusion matrix")
        expected_fpr = _rate(false_positive, truth_negative)
        expected_fnr = _rate(false_negative, truth_positive)
        fpr_upper = _wilson_bound(false_positive, truth_negative, upper=True)
        fnr_upper = _wilson_bound(false_negative, truth_positive, upper=True)
        if (
            expected_fpr is None
            or expected_fnr is None
            or fpr_upper is None
            or fnr_upper is None
            or metric.get("fpr") != expected_fpr
            or metric.get("fnr") != expected_fnr
            or metric.get("fpr_upper_95") != fpr_upper
            or metric.get("fnr_upper_95") != fnr_upper
        ):
            raise ValueError(f"{family} rates or confidence bounds disagree with its counts")
        family_gate = family_gate and (
            fpr_upper <= contract.family_fp_upper_95
            and fnr_upper <= contract.family_fn_upper_95
        )
    if not family_gate:
        raise ValueError("family false-positive/false-negative confidence bound fails the gate")

    candidate = report.get("candidate_metrics")
    if not isinstance(candidate, Mapping):
        raise ValueError("release calibration lacks candidate-level metrics")
    candidate_fp = _nonnegative_count(
        candidate.get("false_positive"), "candidate false-positive count"
    )
    candidate_negative = _nonnegative_count(
        candidate.get("truth_negative"), "candidate truth-negative count"
    )
    candidate_fn = _nonnegative_count(
        candidate.get("false_negative"), "candidate false-negative count"
    )
    candidate_positive = _nonnegative_count(
        candidate.get("truth_positive"), "candidate truth-positive count"
    )
    if (
        candidate_fp > candidate_negative
        or candidate_fn > candidate_positive
        or candidate.get("fpr") != _rate(candidate_fp, candidate_negative)
        or candidate.get("fnr") != _rate(candidate_fn, candidate_positive)
    ):
        raise ValueError("candidate-level metrics disagree with their counts")

    maximum = report.get("bootstrap_maximum_statistic", {})
    probability = maximum.get("probability")
    replicate_count = _nonnegative_count(
        maximum.get("task_replicate_count"),
        "maximum-statistic task replicate count",
    )
    critical = maximum.get("critical_value")
    if (
        probability is None
        or not math.isfinite(float(probability))
        or not 0.0 < float(probability) <= 1.0
        or replicate_count != task_count
        or critical is None
        or not math.isfinite(float(critical))
        or float(critical) < 0.0
    ):
        raise ValueError("release calibration lacks a finite maximum-statistic critical value")
    expected_gate_results = {
        "minimum_task_seeds": task_count >= contract.minimum_task_seeds,
        "clean_task_any_fp": clean_upper <= contract.clean_task_any_fp_upper_95,
        "mutated_task_any_fn": mutated_upper <= contract.mutated_task_any_fn_upper_95,
        "family_fp_fn": family_gate,
        "exact_signature_accuracy": exact_lower
        >= contract.exact_signature_accuracy_lower_95,
    }
    if (
        report.get("release_gate_passed") is not all(expected_gate_results.values())
        or report.get("gate_results") != expected_gate_results
    ):
        raise ValueError("release calibration report is not marked as gate-passing")


__all__ = [
    "CalibrationGates",
    "CandidateCalibration",
    "FAMILIES",
    "SIGNATURES",
    "TaskCalibrationRecord",
    "calibration_report_checksum",
    "calibration_report",
    "validate_release_calibration_report",
]
