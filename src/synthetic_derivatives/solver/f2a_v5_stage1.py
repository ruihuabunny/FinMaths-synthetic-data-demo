"""Independent Solver Stage-1 estimator for F2A v5.

The public knot locations are fixed.  Only the drift and diffusion node heights
are estimated.  All time coordinates are Actual/365 calendar-year offsets, and
the interval likelihood uses exact integrals of the piecewise-linear hat basis.
The parent restarts from its published 0.01 USD close, so this is the frozen
latent-Gaussian quasi-likelihood; it does not pretend endpoint quantization has
a continuous Gaussian observation density.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from itertools import product
import math
from typing import Any, Mapping, Sequence


STAGE1_LOSS_ID = "exact-tih-gbm-gaussian-node-loss-v1"
STAGE1_SOLVER_ID = "bounded-profile-nelder-mead-v1"
STAGE1_COVARIANCE_ID = "observed-hessian-drift-profiled-schur-v1"


def _finite(name: str, value: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _quantize(value: float, precision: int) -> float:
    if not math.isfinite(value):
        raise ValueError("canonical numeric output must be finite")
    quantum = Decimal(1).scaleb(-precision)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN))


def _matrix(rows: int, columns: int, value: float = 0.0) -> list[list[float]]:
    return [[value for _ in range(columns)] for _ in range(rows)]


def _transpose(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    if not matrix:
        return []
    return [list(column) for column in zip(*matrix)]


def _matmul(
    left: Sequence[Sequence[float]],
    right: Sequence[Sequence[float]],
) -> list[list[float]]:
    right_t = _transpose(right)
    return [
        [sum(a * b for a, b in zip(row, column)) for column in right_t]
        for row in left
    ]


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(a * b for a, b in zip(row, vector)) for row in matrix]


def _solve_linear(
    matrix: Sequence[Sequence[float]],
    vector: Sequence[float],
    *,
    singular_tolerance: float = 1e-14,
) -> list[float]:
    """Solve a small dense system with deterministic partial pivoting."""

    size = len(vector)
    if len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError("linear system must be square")
    augmented = [list(row) + [float(rhs)] for row, rhs in zip(matrix, vector)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= singular_tolerance:
            raise ValueError("singular linear system")
        if pivot != column:
            augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        for item in range(column, size + 1):
            augmented[column][item] /= pivot_value
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            for item in range(column, size + 1):
                augmented[row][item] -= factor * augmented[column][item]
    return [augmented[row][-1] for row in range(size)]


def _inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(matrix)
    columns = []
    for column in range(size):
        unit = [0.0] * size
        unit[column] = 1.0
        columns.append(_solve_linear(matrix, unit))
    return _transpose(columns)


def _symmetric_eigenvalues(
    matrix: Sequence[Sequence[float]],
    *,
    tolerance: float = 1e-13,
    max_iterations: int = 200,
) -> tuple[float, ...]:
    """Return eigenvalues of a small real symmetric matrix via Jacobi sweeps."""

    work = [list(row) for row in matrix]
    size = len(work)
    if any(len(row) != size for row in work):
        raise ValueError("eigenvalue matrix must be square")
    if size == 1:
        return (work[0][0],)
    for _ in range(max_iterations):
        p, q = max(
            ((i, j) for i in range(size) for j in range(i + 1, size)),
            key=lambda pair: abs(work[pair[0]][pair[1]]),
        )
        if abs(work[p][q]) <= tolerance:
            break
        angle = 0.5 * math.atan2(2.0 * work[p][q], work[q][q] - work[p][p])
        cosine = math.cos(angle)
        sine = math.sin(angle)
        app = work[p][p]
        aqq = work[q][q]
        apq = work[p][q]
        for index in range(size):
            if index in (p, q):
                continue
            aip = work[index][p]
            aiq = work[index][q]
            work[index][p] = work[p][index] = cosine * aip - sine * aiq
            work[index][q] = work[q][index] = sine * aip + cosine * aiq
        work[p][p] = (
            cosine * cosine * app
            - 2.0 * sine * cosine * apq
            + sine * sine * aqq
        )
        work[q][q] = (
            sine * sine * app
            + 2.0 * sine * cosine * apq
            + cosine * cosine * aqq
        )
        work[p][q] = work[q][p] = 0.0
    return tuple(sorted(work[index][index] for index in range(size)))


def _rank(matrix: Sequence[Sequence[float]], tolerance: float = 1e-12) -> int:
    if not matrix:
        return 0
    work = [list(row) for row in matrix]
    rows = len(work)
    columns = len(work[0])
    rank = 0
    for column in range(columns):
        pivot = max(range(rank, rows), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= tolerance:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        pivot_value = work[rank][column]
        for item in range(column, columns):
            work[rank][item] /= pivot_value
        for row in range(rows):
            if row == rank:
                continue
            factor = work[row][column]
            for item in range(column, columns):
                work[row][item] -= factor * work[rank][item]
        rank += 1
        if rank == rows:
            break
    return rank


@dataclass(frozen=True)
class PhysicalFittingContract:
    """Frozen public estimator settings for one underlying node grid."""

    time_origin: str
    node_offsets_calendar_days: tuple[float, ...]
    measure: str = "P"
    state_variable: str = "published_ex_dividend_spot_close"
    conditioning_information: str = (
        "previous_published_close_and_deterministic_node_functions"
    )
    transition_moment_rule: str = "exact_integrated_tih_gbm_between_public_dates"
    published_state_likelihood_semantics: str = (
        "latent_gaussian_transition_quasi_likelihood_ignores_0p01_usd_endpoint_quantization"
    )
    drift_bounds: tuple[float, float] = (-0.05, 0.15)
    diffusion_bounds: tuple[float, float] = (0.05, 0.80)
    initial_drift: float = 0.05
    initial_diffusion: float = 0.20
    day_count: str = "Actual365Fixed"
    interpolation: str = "linear"
    extrapolation: str = "flat"
    loss_id: str = STAGE1_LOSS_ID
    solver_id: str = STAGE1_SOLVER_ID
    covariance_method: str = STAGE1_COVARIANCE_ID
    max_evaluations: int = 6000
    parameter_tolerance: float = 1e-9
    objective_tolerance: float = 1e-11
    output_precision: int = 10
    max_condition_number: float = 1e12
    max_diffusion_node_rse: float = 0.25

    def __post_init__(self) -> None:
        date.fromisoformat(self.time_origin)
        if (
            self.measure != "P"
            or self.state_variable != "published_ex_dividend_spot_close"
            or self.conditioning_information
            != "previous_published_close_and_deterministic_node_functions"
            or self.transition_moment_rule
            != "exact_integrated_tih_gbm_between_public_dates"
            or self.published_state_likelihood_semantics
            != "latent_gaussian_transition_quasi_likelihood_ignores_0p01_usd_endpoint_quantization"
        ):
            raise ValueError("unknown Stage-1 P-measure state/observation contract")
        nodes = self.node_offsets_calendar_days
        if len(nodes) < 1 or any(not math.isfinite(float(item)) for item in nodes):
            raise ValueError("physical node offsets must be finite and nonempty")
        if any(right <= left for left, right in zip(nodes, nodes[1:])):
            raise ValueError("physical node offsets must be strictly increasing")
        if self.day_count != "Actual365Fixed":
            raise ValueError("F2A v5 freezes Actual365Fixed")
        if self.interpolation != "linear" or self.extrapolation != "flat":
            raise ValueError("F2A v5 requires linear interpolation and flat extrapolation")
        if self.loss_id != STAGE1_LOSS_ID or self.solver_id != STAGE1_SOLVER_ID:
            raise ValueError("unknown Stage-1 estimator contract")
        if self.covariance_method != STAGE1_COVARIANCE_ID:
            raise ValueError("unknown Stage-1 covariance contract")
        for name, bounds in (
            ("drift", self.drift_bounds),
            ("diffusion", self.diffusion_bounds),
        ):
            if len(bounds) != 2 or not bounds[0] < bounds[1]:
                raise ValueError(f"{name} bounds must be increasing")
        if self.diffusion_bounds[0] <= 0.0:
            raise ValueError("diffusion lower bound must be strictly positive")
        if not self.drift_bounds[0] <= self.initial_drift <= self.drift_bounds[1]:
            raise ValueError("initial drift is outside its public bounds")
        if not self.diffusion_bounds[0] <= self.initial_diffusion <= self.diffusion_bounds[1]:
            raise ValueError("initial diffusion is outside its public bounds")
        if (
            self.max_evaluations <= 0
            or self.parameter_tolerance <= 0.0
            or self.objective_tolerance <= 0.0
            or self.output_precision < 0
            or self.max_condition_number <= 1.0
            or self.max_diffusion_node_rse <= 0.0
        ):
            raise ValueError("invalid Stage-1 numerical contract")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PhysicalFittingContract":
        offsets = raw.get("node_offsets_calendar_days")
        if offsets is None:
            offsets = raw.get("drift_node_dates_or_offsets")
        return cls(
            time_origin=str(raw["time_origin"]),
            node_offsets_calendar_days=tuple(float(item) for item in offsets),
            measure=str(raw.get("measure", "P")),
            state_variable=str(
                raw.get("state_variable", "published_ex_dividend_spot_close")
            ),
            conditioning_information=str(
                raw.get(
                    "conditioning_information",
                    "previous_published_close_and_deterministic_node_functions",
                )
            ),
            transition_moment_rule=str(
                raw.get(
                    "transition_moment_rule",
                    "exact_integrated_tih_gbm_between_public_dates",
                )
            ),
            published_state_likelihood_semantics=str(
                raw.get(
                    "published_state_likelihood_semantics",
                    "latent_gaussian_transition_quasi_likelihood_ignores_0p01_usd_endpoint_quantization",
                )
            ),
            drift_bounds=tuple(float(item) for item in raw.get("drift_bounds", (-0.05, 0.15))),  # type: ignore[arg-type]
            diffusion_bounds=tuple(float(item) for item in raw.get("diffusion_bounds", (0.05, 0.80))),  # type: ignore[arg-type]
            initial_drift=float(raw.get("initial_drift", 0.05)),
            initial_diffusion=float(raw.get("initial_diffusion", 0.20)),
            day_count=str(raw.get("day_count", "Actual365Fixed")),
            interpolation=str(raw.get("interpolation", "linear")),
            extrapolation=str(raw.get("extrapolation", "flat")),
            loss_id=str(raw.get("canonical_loss", raw.get("loss_id", STAGE1_LOSS_ID))),
            solver_id=str(raw.get("canonical_solver", raw.get("solver_id", STAGE1_SOLVER_ID))),
            covariance_method=str(raw.get("covariance_method", STAGE1_COVARIANCE_ID)),
            max_evaluations=int(raw.get("max_evaluations", 6000)),
            parameter_tolerance=float(raw.get("parameter_tolerance", 1e-9)),
            objective_tolerance=float(raw.get("objective_tolerance", 1e-11)),
            output_precision=int(raw.get("output_precision", 10)),
            max_condition_number=float(raw.get("max_condition_number", 1e12)),
            max_diffusion_node_rse=float(raw.get("node_rse_diagnostic_gate", 0.25)),
        )

    @property
    def node_times(self) -> tuple[float, ...]:
        return tuple(offset / 365.0 for offset in self.node_offsets_calendar_days)


@dataclass(frozen=True)
class UnderlyingFit:
    underlying_id: str
    node_offsets_calendar_days: tuple[float, ...]
    fitted_drift_node_values: tuple[float, ...]
    fitted_diffusion_node_values: tuple[float, ...]
    observed_hessian: tuple[tuple[float, ...], ...]
    diffusion_information_matrix: tuple[tuple[float, ...], ...]
    diffusion_covariance_matrix: tuple[tuple[float, ...], ...]
    diffusion_rse_by_node: tuple[float, ...]
    information_effective_sample_size_by_node: tuple[float, ...]
    objective_value: float
    usable_return_count: int
    hessian_condition_number: float
    drift_active_bound_node_indices: tuple[int, ...]
    solver_status: str
    solver_evaluations: int
    output_precision: int = 10

    def to_dict(self) -> dict[str, Any]:
        q = lambda value: _quantize(value, self.output_precision)
        return {
            "underlying_id": self.underlying_id,
            "node_dates_or_offsets": [q(item) for item in self.node_offsets_calendar_days],
            "fitted_drift_node_values": [q(item) for item in self.fitted_drift_node_values],
            "fitted_diffusion_node_values": [q(item) for item in self.fitted_diffusion_node_values],
            "observed_hessian": [[q(item) for item in row] for row in self.observed_hessian],
            "diffusion_information_matrix": [
                [q(item) for item in row] for row in self.diffusion_information_matrix
            ],
            "diffusion_covariance_matrix": [
                [q(item) for item in row] for row in self.diffusion_covariance_matrix
            ],
            "diffusion_rse_by_node": [q(item) for item in self.diffusion_rse_by_node],
            "information_effective_sample_size_by_node": [
                q(item) for item in self.information_effective_sample_size_by_node
            ],
            "objective_value": q(self.objective_value),
            "usable_return_count": self.usable_return_count,
            "hessian_condition_number": q(self.hessian_condition_number),
            "drift_active_bound_node_indices": list(
                self.drift_active_bound_node_indices
            ),
            "solver_status": self.solver_status,
            "solver_evaluations": self.solver_evaluations,
        }


def hat_basis(node_times: Sequence[float], time: float) -> tuple[float, ...]:
    """Evaluate the linear hat basis with flat boundary extrapolation."""

    nodes = tuple(float(item) for item in node_times)
    if not nodes or any(right <= left for left, right in zip(nodes, nodes[1:])):
        raise ValueError("hat-basis nodes must be strictly increasing")
    result = [0.0] * len(nodes)
    if len(nodes) == 1 or time <= nodes[0]:
        result[0] = 1.0
        return tuple(result)
    if time >= nodes[-1]:
        result[-1] = 1.0
        return tuple(result)
    for index, (left, right) in enumerate(zip(nodes, nodes[1:])):
        if left <= time <= right:
            fraction = (time - left) / (right - left)
            result[index] = 1.0 - fraction
            result[index + 1] = fraction
            return tuple(result)
    raise AssertionError("time did not resolve on a valid node grid")


def integrate_hat_basis(
    node_times: Sequence[float],
    start: float,
    end: float,
) -> tuple[tuple[float, ...], tuple[tuple[float, ...], ...]]:
    """Return exact ``A`` and ``Q`` over one calendar-time interval."""

    nodes = tuple(float(item) for item in node_times)
    if end <= start:
        raise ValueError("integration interval must have positive length")
    cuts = [start]
    cuts.extend(node for node in nodes if start < node < end)
    cuts.append(end)
    size = len(nodes)
    a_result = [0.0] * size
    q_result = _matrix(size, size)
    for left, right in zip(cuts, cuts[1:]):
        width = right - left
        left_basis = hat_basis(nodes, left)
        right_basis = hat_basis(nodes, right)
        for i in range(size):
            a_result[i] += width * (left_basis[i] + right_basis[i]) / 2.0
            for j in range(size):
                q_result[i][j] += width * (
                    2.0 * left_basis[i] * left_basis[j]
                    + left_basis[i] * right_basis[j]
                    + right_basis[i] * left_basis[j]
                    + 2.0 * right_basis[i] * right_basis[j]
                ) / 6.0
    return tuple(a_result), tuple(tuple(row) for row in q_result)


def interval_design_from_dates(
    observation_dates: Sequence[str],
    contract: PhysicalFittingContract,
) -> tuple[
    tuple[tuple[float, ...], ...],
    tuple[tuple[tuple[float, ...], ...], ...],
]:
    origin = date.fromisoformat(contract.time_origin)
    parsed = tuple(date.fromisoformat(str(item)) for item in observation_dates)
    if any(right <= left for left, right in zip(parsed, parsed[1:])):
        raise ValueError("underlying observation dates must be strictly increasing")
    times = tuple((item - origin).days / 365.0 for item in parsed)
    a_rows: list[tuple[float, ...]] = []
    q_rows: list[tuple[tuple[float, ...], ...]] = []
    for start, end in zip(times, times[1:]):
        a_row, q_row = integrate_hat_basis(contract.node_times, start, end)
        a_rows.append(a_row)
        q_rows.append(q_row)
    return tuple(a_rows), tuple(q_rows)


def _quadratic(vector: Sequence[float], matrix: Sequence[Sequence[float]]) -> float:
    return sum(
        vector[i] * matrix[i][j] * vector[j]
        for i in range(len(vector))
        for j in range(len(vector))
    )


def _bounded_alpha_fit(
    targets: Sequence[float],
    variances: Sequence[float],
    a_rows: Sequence[Sequence[float]],
    bounds: tuple[float, float],
) -> tuple[tuple[float, ...], float]:
    """Profile drift exactly by enumerating its small box-constrained active set."""

    size = len(a_rows[0])
    best: tuple[float, tuple[float, ...]] | None = None
    for statuses in product((-1, 0, 1), repeat=size):
        alpha = [0.0] * size
        fixed = [index for index, status in enumerate(statuses) if status]
        free = [index for index, status in enumerate(statuses) if not status]
        for index in fixed:
            alpha[index] = bounds[0] if statuses[index] < 0 else bounds[1]
        if free:
            normal = _matrix(len(free), len(free))
            rhs = [0.0] * len(free)
            for target, variance, row in zip(targets, variances, a_rows):
                adjusted = target - sum(row[index] * alpha[index] for index in fixed)
                weight = 1.0 / variance
                for i, parameter_i in enumerate(free):
                    rhs[i] += weight * row[parameter_i] * adjusted
                    for j, parameter_j in enumerate(free):
                        normal[i][j] += weight * row[parameter_i] * row[parameter_j]
            try:
                solution = _solve_linear(normal, rhs)
            except ValueError:
                continue
            if any(value < bounds[0] or value > bounds[1] for value in solution):
                continue
            for index, value in zip(free, solution):
                alpha[index] = value
        loss = sum(
            (target - sum(coefficient * value for coefficient, value in zip(row, alpha)))
            ** 2
            / variance
            for target, variance, row in zip(targets, variances, a_rows)
        )
        candidate = (loss, tuple(alpha))
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ValueError("drift profile is rank deficient")
    return best[1], best[0]


def _profile_objective(
    beta: Sequence[float],
    returns: Sequence[float],
    a_rows: Sequence[Sequence[float]],
    q_rows: Sequence[Sequence[Sequence[float]]],
    drift_bounds: tuple[float, float],
) -> tuple[float, tuple[float, ...]]:
    variances = tuple(_quadratic(beta, q_row) for q_row in q_rows)
    if any(not math.isfinite(value) or value <= 0.0 for value in variances):
        return math.inf, tuple()
    targets = tuple(value + 0.5 * variance for value, variance in zip(returns, variances))
    try:
        alpha, residual_loss = _bounded_alpha_fit(
            targets,
            variances,
            a_rows,
            drift_bounds,
        )
    except ValueError:
        return math.inf, tuple()
    objective = sum(math.log(value) for value in variances) + residual_loss
    return objective, alpha


def _clip_vector(vector: Sequence[float], bounds: tuple[float, float]) -> tuple[float, ...]:
    return tuple(min(bounds[1], max(bounds[0], value)) for value in vector)


def _nelder_mead_profile(
    initial: Sequence[float],
    objective,
    bounds: tuple[float, float],
    *,
    max_evaluations: int,
    parameter_tolerance: float,
    objective_tolerance: float,
) -> tuple[tuple[float, ...], float, int, bool]:
    size = len(initial)
    initial = _clip_vector(initial, bounds)
    span = bounds[1] - bounds[0]
    simplex = [initial]
    for index in range(size):
        point = list(initial)
        step = max(span * 0.08, abs(initial[index]) * 0.15, 1e-3)
        point[index] = min(bounds[1], initial[index] + step)
        if point[index] == initial[index]:
            point[index] = max(bounds[0], initial[index] - step)
        simplex.append(tuple(point))
    values = [objective(point)[0] for point in simplex]
    evaluations = len(simplex)
    converged = False
    while evaluations < max_evaluations:
        ordered = sorted(zip(values, simplex), key=lambda item: (item[0], item[1]))
        values = [item[0] for item in ordered]
        simplex = [item[1] for item in ordered]
        coordinate_spread = max(
            abs(simplex[row][column] - simplex[0][column])
            for row in range(1, size + 1)
            for column in range(size)
        )
        if (
            coordinate_spread <= parameter_tolerance
            and max(abs(value - values[0]) for value in values[1:]) <= objective_tolerance
        ):
            converged = True
            break
        centroid = tuple(
            sum(simplex[row][column] for row in range(size)) / size
            for column in range(size)
        )
        worst = simplex[-1]
        reflected = _clip_vector(
            tuple(centroid[i] + (centroid[i] - worst[i]) for i in range(size)),
            bounds,
        )
        reflected_value = objective(reflected)[0]
        evaluations += 1
        if values[0] <= reflected_value < values[-2]:
            simplex[-1], values[-1] = reflected, reflected_value
            continue
        if reflected_value < values[0]:
            expanded = _clip_vector(
                tuple(centroid[i] + 2.0 * (reflected[i] - centroid[i]) for i in range(size)),
                bounds,
            )
            expanded_value = objective(expanded)[0]
            evaluations += 1
            if expanded_value < reflected_value:
                simplex[-1], values[-1] = expanded, expanded_value
            else:
                simplex[-1], values[-1] = reflected, reflected_value
            continue
        if reflected_value < values[-1]:
            contracted = _clip_vector(
                tuple(centroid[i] + 0.5 * (reflected[i] - centroid[i]) for i in range(size)),
                bounds,
            )
        else:
            contracted = _clip_vector(
                tuple(centroid[i] + 0.5 * (worst[i] - centroid[i]) for i in range(size)),
                bounds,
            )
        contracted_value = objective(contracted)[0]
        evaluations += 1
        if contracted_value < min(reflected_value, values[-1]):
            simplex[-1], values[-1] = contracted, contracted_value
            continue
        best = simplex[0]
        for row in range(1, size + 1):
            simplex[row] = _clip_vector(
                tuple(best[i] + 0.5 * (simplex[row][i] - best[i]) for i in range(size)),
                bounds,
            )
            values[row] = objective(simplex[row])[0]
        evaluations += size
    ordered = sorted(zip(values, simplex), key=lambda item: (item[0], item[1]))
    return ordered[0][1], ordered[0][0], evaluations, converged


def _full_loss(
    parameters: Sequence[float],
    returns: Sequence[float],
    a_rows: Sequence[Sequence[float]],
    q_rows: Sequence[Sequence[Sequence[float]]],
) -> float:
    size = len(parameters) // 2
    alpha = parameters[:size]
    beta = parameters[size:]
    result = 0.0
    for observed, a_row, q_row in zip(returns, a_rows, q_rows):
        variance = _quadratic(beta, q_row)
        if variance <= 0.0:
            return math.inf
        mean = sum(value * coefficient for value, coefficient in zip(alpha, a_row)) - 0.5 * variance
        residual = observed - mean
        result += math.log(variance) + residual * residual / variance
    return result


def _observed_hessian(
    parameters: Sequence[float],
    loss,
    parameter_bounds: Sequence[tuple[float, float]],
) -> list[list[float]]:
    """Central-difference Hessian of the negative log likelihood (loss / 2)."""

    size = len(parameters)
    steps = [
        max(abs(value) * 2e-4, (upper - lower) * 2e-5, 2e-6)
        for value, (lower, upper) in zip(parameters, parameter_bounds)
    ]
    for value, step, (lower, upper) in zip(parameters, steps, parameter_bounds):
        if value - step <= lower or value + step >= upper:
            raise ValueError("canonical optimum is pinned too close to a parameter bound")
    base = 0.5 * loss(parameters)
    result = _matrix(size, size)
    for i in range(size):
        plus = list(parameters)
        minus = list(parameters)
        plus[i] += steps[i]
        minus[i] -= steps[i]
        result[i][i] = (
            0.5 * loss(plus) - 2.0 * base + 0.5 * loss(minus)
        ) / (steps[i] * steps[i])
        for j in range(i + 1, size):
            pp = list(parameters)
            pm = list(parameters)
            mp = list(parameters)
            mm = list(parameters)
            pp[i] += steps[i]
            pp[j] += steps[j]
            pm[i] += steps[i]
            pm[j] -= steps[j]
            mp[i] -= steps[i]
            mp[j] += steps[j]
            mm[i] -= steps[i]
            mm[j] -= steps[j]
            value = 0.5 * (loss(pp) - loss(pm) - loss(mp) + loss(mm)) / (
                4.0 * steps[i] * steps[j]
            )
            result[i][j] = result[j][i] = value
    return result


def _analytic_observed_hessian(
    parameters: Sequence[float],
    returns: Sequence[float],
    a_rows: Sequence[Sequence[float]],
    q_rows: Sequence[Sequence[Sequence[float]]],
) -> list[list[float]]:
    """Observed Hessian of the Gaussian negative log likelihood.

    This expression remains defined at a box-constrained drift solution.  The
    active drift coordinates are reported and treated as locally fixed nuisance
    parameters when the profiled diffusion information is formed below.
    """

    size = len(parameters) // 2
    alpha = parameters[:size]
    beta = parameters[size:]
    result = _matrix(2 * size, 2 * size)
    for observed, a_row, q_row in zip(returns, a_rows, q_rows):
        variance = _quadratic(beta, q_row)
        mean = sum(value * coefficient for value, coefficient in zip(alpha, a_row)) - 0.5 * variance
        residual = observed - mean
        q_beta = _matvec(q_row, beta)
        h = 1.0 / variance + residual / variance - residual * residual / (variance * variance)
        dh_d_variance = (
            0.5 / variance
            - (1.0 + 2.0 * residual) / (variance * variance)
            + 2.0 * residual * residual / (variance * variance * variance)
        )
        for i in range(size):
            for j in range(size):
                result[i][j] += a_row[i] * a_row[j] / variance
                cross = (
                    -a_row[i]
                    * q_beta[j]
                    * (variance - 2.0 * residual)
                    / (variance * variance)
                )
                result[i][size + j] += cross
                result[size + j][i] += cross
                result[size + i][size + j] += (
                    q_row[i][j] * h
                    + 2.0 * dh_d_variance * q_beta[i] * q_beta[j]
                )
    return result


def _schur_information(
    hessian: Sequence[Sequence[float]],
    drift_size: int,
    active_drift_indices: Sequence[int] = (),
) -> list[list[float]]:
    active = set(active_drift_indices)
    if any(index < 0 or index >= drift_size for index in active):
        raise ValueError("active drift-bound index is outside the node grid")
    free = [index for index in range(drift_size) if index not in active]
    h_bb = [list(row[drift_size:]) for row in hessian[drift_size:]]
    if not free:
        return [
            [(h_bb[i][j] + h_bb[j][i]) / 2.0 for j in range(drift_size)]
            for i in range(drift_size)
        ]
    h_aa = [[hessian[i][j] for j in free] for i in free]
    h_ab = [[hessian[i][drift_size + j] for j in range(drift_size)] for i in free]
    h_ba = [[hessian[drift_size + i][j] for j in free] for i in range(drift_size)]
    adjustment = _matmul(_matmul(h_ba, _inverse(h_aa)), h_ab)
    result = [
        [h_bb[i][j] - adjustment[i][j] for j in range(drift_size)]
        for i in range(drift_size)
    ]
    return [
        [(result[i][j] + result[j][i]) / 2.0 for j in range(drift_size)]
        for i in range(drift_size)
    ]


def fit_underlying_path(
    underlying_id: str,
    observation_dates: Sequence[str],
    spot_closes: Sequence[float],
    contract: PhysicalFittingContract,
) -> UnderlyingFit:
    """Fit the canonical public estimator and its diffusion covariance."""

    if len(observation_dates) != len(spot_closes) or len(spot_closes) < 3:
        raise ValueError("underlying fit requires aligned dates and at least three closes")
    spots = tuple(_finite("spot close", value) for value in spot_closes)
    if any(value <= 0.0 for value in spots):
        raise ValueError("spot closes must be strictly positive")
    returns = tuple(math.log(right / left) for left, right in zip(spots, spots[1:]))
    a_rows, q_rows = interval_design_from_dates(observation_dates, contract)
    size = len(contract.node_times)
    if len(returns) < 2 * size + 1:
        raise ValueError("insufficient return intervals for the scored node count")
    if _rank(a_rows) < size:
        raise ValueError("drift node design is rank deficient")
    support = [sum(q_row[index][index] for q_row in q_rows) for index in range(size)]
    if any(value <= 0.0 for value in support):
        raise ValueError("every scored diffusion node must have observation support")

    elapsed = sum(sum(row) for row in a_rows)
    mean_return = sum(returns) / len(returns)
    centered_sum = sum((value - mean_return) ** 2 for value in returns)
    realized = math.sqrt(max(centered_sum / max(elapsed, 1e-12), 1e-12))
    realized = min(
        contract.diffusion_bounds[1] * 0.95,
        max(contract.diffusion_bounds[0] * 1.05, realized),
    )
    objective = lambda beta: _profile_objective(
        beta,
        returns,
        a_rows,
        q_rows,
        contract.drift_bounds,
    )
    starts = [
        tuple(realized for _ in range(size)),
        tuple(contract.initial_diffusion for _ in range(size)),
        tuple(
            min(
                contract.diffusion_bounds[1] * 0.95,
                max(
                    contract.diffusion_bounds[0] * 1.05,
                    realized * (0.85 + 0.15 * index),
                ),
            )
            for index in range(size)
        ),
        tuple(
            min(
                contract.diffusion_bounds[1] * 0.95,
                max(
                    contract.diffusion_bounds[0] * 1.05,
                    realized * (1.15 - 0.15 * index),
                ),
            )
            for index in range(size)
        ),
    ]
    best: tuple[float, tuple[float, ...], int, bool] | None = None
    budget = max(contract.max_evaluations // len(starts), 500)
    total_evaluations = 0
    for start in starts:
        beta, value, evaluations, converged = _nelder_mead_profile(
            start,
            objective,
            contract.diffusion_bounds,
            max_evaluations=budget,
            parameter_tolerance=contract.parameter_tolerance,
            objective_tolerance=contract.objective_tolerance,
        )
        total_evaluations += evaluations
        candidate = (value, beta, evaluations, converged)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    assert best is not None
    objective_value, beta, _, converged = best
    objective_value, alpha = objective(beta)
    if not math.isfinite(objective_value) or not alpha:
        raise ValueError("canonical Stage-1 solver failed")

    parameters = tuple(alpha) + tuple(beta)
    parameter_bounds = [contract.drift_bounds] * size + [contract.diffusion_bounds] * size
    hessian = _analytic_observed_hessian(parameters, returns, a_rows, q_rows)
    drift_active_bounds = tuple(
        index
        for index, value in enumerate(alpha)
        if any(
            abs(value - bound) <= contract.parameter_tolerance * 10.0
            for bound in contract.drift_bounds
        )
    )
    information = _schur_information(hessian, size, drift_active_bounds)
    eigenvalues = _symmetric_eigenvalues(information)
    if not eigenvalues or eigenvalues[0] <= 0.0:
        raise ValueError("diffusion Schur information is not positive definite")
    condition = eigenvalues[-1] / eigenvalues[0]
    covariance = _inverse(information)
    rse = tuple(math.sqrt(covariance[index][index]) / beta[index] for index in range(size))
    effective = tuple(1.0 / (2.0 * value * value) for value in rse)
    diffusion_pinned = any(
        abs(value - bound) <= contract.parameter_tolerance * 10.0
        for value in beta
        for bound in contract.diffusion_bounds
    )
    status = "CONVERGED"
    if not converged:
        status = "MAX_EVALUATIONS"
    elif diffusion_pinned:
        status = "BOUND_SOLUTION"
    elif condition > contract.max_condition_number:
        status = "ILL_CONDITIONED"

    return UnderlyingFit(
        underlying_id=str(underlying_id),
        node_offsets_calendar_days=contract.node_offsets_calendar_days,
        fitted_drift_node_values=tuple(alpha),
        fitted_diffusion_node_values=tuple(beta),
        observed_hessian=tuple(tuple(value for value in row) for row in hessian),
        diffusion_information_matrix=tuple(tuple(value for value in row) for row in information),
        diffusion_covariance_matrix=tuple(tuple(value for value in row) for row in covariance),
        diffusion_rse_by_node=rse,
        information_effective_sample_size_by_node=effective,
        objective_value=objective_value,
        usable_return_count=len(returns),
        hessian_condition_number=condition,
        drift_active_bound_node_indices=drift_active_bounds,
        solver_status=status,
        solver_evaluations=total_evaluations,
        output_precision=contract.output_precision,
    )


def fit_passes_publication_gate(
    fit: UnderlyingFit,
    contract: PhysicalFittingContract,
) -> bool:
    """Apply the declared information diagnostics without changing the estimate."""

    return (
        fit.solver_status == "CONVERGED"
        and fit.hessian_condition_number <= contract.max_condition_number
        and max(fit.diffusion_rse_by_node) <= contract.max_diffusion_node_rse
    )


__all__ = [
    "PhysicalFittingContract",
    "STAGE1_COVARIANCE_ID",
    "STAGE1_LOSS_ID",
    "STAGE1_SOLVER_ID",
    "UnderlyingFit",
    "fit_passes_publication_gate",
    "fit_underlying_path",
    "hat_basis",
    "integrate_hat_basis",
    "interval_design_from_dates",
]
