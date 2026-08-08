from __future__ import annotations

from datetime import date, timedelta
import math
import random

import pytest

from synthetic_derivatives.verifier.f2a_stage1 import (
    PhysicalFittingContract,
    _schur_information,
    diffusion_fisher_covariance,
    fit_underlying_path,
    hat_basis,
    integrate_hat_basis,
    interval_design_from_dates,
)


def test_schur_information_profiles_only_free_drift_coordinates() -> None:
    # alpha_0 is active at a box bound; alpha_1 remains a free nuisance.
    hessian = (
        (4.0, 0.0, 1.0, 0.0),
        (0.0, 2.0, 0.0, 1.0),
        (1.0, 0.0, 5.0, 1.0),
        (0.0, 1.0, 1.0, 4.0),
    )
    information = _schur_information(hessian, 2, (0,))
    assert information[0] == pytest.approx((5.0, 1.0))
    assert information[1] == pytest.approx((1.0, 3.5))


def test_hat_basis_and_exact_interval_integrals() -> None:
    nodes = (0.0, 0.5, 1.0)
    assert hat_basis(nodes, -1.0) == (1.0, 0.0, 0.0)
    assert hat_basis(nodes, 0.25) == (0.5, 0.5, 0.0)
    assert hat_basis(nodes, 2.0) == (0.0, 0.0, 1.0)
    a_row, q_row = integrate_hat_basis(nodes, 0.0, 1.0)
    assert a_row == pytest.approx((0.25, 0.5, 0.25))
    expected_q = (
        (1.0 / 6.0, 1.0 / 12.0, 0.0),
        (1.0 / 12.0, 1.0 / 3.0, 1.0 / 12.0),
        (0.0, 1.0 / 12.0, 1.0 / 6.0),
    )
    for actual, expected in zip(q_row, expected_q):
        assert actual == pytest.approx(expected)
    assert sum(a_row) == pytest.approx(1.0)
    assert sum(sum(row) for row in q_row) == pytest.approx(1.0)


def test_integrals_include_weekend_cross_node_and_flat_extrapolation() -> None:
    contract = PhysicalFittingContract(
        time_origin="2026-01-01",
        node_offsets_calendar_days=(0.0, 2.0, 4.0),
    )
    dates = ("2026-01-02", "2026-01-05", "2026-01-08")
    a_rows, q_rows = interval_design_from_dates(dates, contract)
    assert sum(a_rows[0]) == pytest.approx(3.0 / 365.0)
    assert sum(sum(row) for row in q_rows[0]) == pytest.approx(3.0 / 365.0)
    # The second interval lies partly beyond the final node and therefore has
    # positive flat terminal-node support.
    assert a_rows[1][-1] > 0.0
    assert q_rows[1][-1][-1] > 0.0


def test_three_node_fisher_benchmark_has_documented_sqrt_n_scaling() -> None:
    constants = []
    for return_count in (65, 126, 252):
        nodes = (0.0, return_count / 2.0 / 252.0, return_count / 252.0)
        q_rows = tuple(
            integrate_hat_basis(nodes, index / 252.0, (index + 1) / 252.0)[1]
            for index in range(return_count)
        )
        covariance = diffusion_fisher_covariance((0.2, 0.2, 0.2), q_rows)
        rse = tuple(math.sqrt(covariance[i][i]) / 0.2 for i in range(3))
        constants.append(tuple(value * math.sqrt(return_count) for value in rse))
    for boundary, center, other_boundary in constants:
        assert boundary == pytest.approx(1.871, abs=0.002)
        assert center == pytest.approx(1.414, abs=0.002)
        assert other_boundary == pytest.approx(boundary, abs=1e-12)


def test_canonical_estimator_recovers_diffusion_and_reports_schur_covariance() -> None:
    contract = PhysicalFittingContract(
        time_origin="2024-01-01",
        node_offsets_calendar_days=(0.0, 365.0, 730.0),
        max_diffusion_node_rse=1.0,
    )
    observation_dates = []
    current = date(2024, 1, 1)
    while len(observation_dates) < 505:
        if current.weekday() < 5:
            observation_dates.append(current.isoformat())
        current += timedelta(days=1)
    a_rows, q_rows = interval_design_from_dates(observation_dates, contract)
    alpha = (0.04, 0.06, 0.03)
    beta = (0.18, 0.24, 0.20)
    generator = random.Random(20260808)
    spots = [100.0]
    for a_row, q_row in zip(a_rows, q_rows):
        variance = sum(
            beta[i] * q_row[i][j] * beta[j]
            for i in range(3)
            for j in range(3)
        )
        mean = sum(a * b for a, b in zip(a_row, alpha)) - 0.5 * variance
        spots.append(
            spots[-1]
            * math.exp(mean + math.sqrt(variance) * generator.gauss(0.0, 1.0))
        )
    fit = fit_underlying_path(
        "SYNTH-V5-STAGE1",
        observation_dates,
        spots,
        contract,
    )
    assert fit.solver_status == "CONVERGED"
    assert fit.usable_return_count == 504
    assert fit.fitted_diffusion_node_values == pytest.approx(beta, abs=0.06)
    assert all(value > 0.0 for value in fit.diffusion_rse_by_node)
    assert all(
        fit.diffusion_covariance_matrix[index][index] > 0.0
        for index in range(3)
    )


def test_node_grid_outside_observation_horizon_has_no_terminal_support() -> None:
    contract = PhysicalFittingContract(
        time_origin="2026-01-01",
        node_offsets_calendar_days=(0.0, 100.0, 200.0),
    )
    dates = tuple((date(2026, 1, 1) + timedelta(days=index)).isoformat() for index in range(30))
    _, q_rows = interval_design_from_dates(dates, contract)
    assert sum(row[-1][-1] for row in q_rows) == 0.0
