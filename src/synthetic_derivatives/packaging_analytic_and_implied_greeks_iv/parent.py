"""Deterministically materialize the private 22-asset P/Q parent for packaging."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def joint_parent_config(raw_base: dict[str, Any]) -> dict[str, Any]:
    """Upgrade the frozen 1.6 law to explicit P/Q dependence identities.

    The P historical state remains the same deterministic time-inhomogeneous
    rounded-restart GBM configured by the base file.  The Q row declares the
    existing common USD money-market measure and a drift-only Girsanov mapping
    that preserves the Brownian covariance; it is provenance for marginal BSM
    rows and does not enter their single-asset pricing formula.
    """

    raw = deepcopy(raw_base)
    if raw.get("schema_version") != "1.6.0":
        raise ValueError("golden parent builder requires a 1.6.0 base config")
    if (
        raw.get("generator_config_id")
        != "quantlib-randomized-tdgbm-metals-liquid-option-chain-v4"
        or raw.get("snapshot_id")
        != "DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v4"
        or raw.get("business_days") != 65
        or len(raw.get("underlyings", [])) != 22
    ):
        raise ValueError("golden parent builder requires the exact 22-asset v4 base")
    raw.update(
        {
            "schema_version": "1.7.0",
            "generator_config_id": "quantlib-bsm-greeks-parent-v1",
            "generator_version": "0.9.0",
            "snapshot_id": "DERIVATIVES-METALS-BSM-GREEKS-PARENT-v1",
        }
    )
    physical = raw.pop("underlying_simulation")
    physical["dependence_spec_id"] = "BSM-GREEKS-P-SPOT-FACTOR-v1"
    pricing = deepcopy(physical)
    pricing.update(
        {
            "dependence_spec_id": "BSM-GREEKS-Q-SPOT-FACTOR-v1",
            "measure": "Q",
            "source_dependence_spec_id": physical["dependence_spec_id"],
            "mapping_id": "BSM-GREEKS-GIRSANOV-COVARIANCE-v1",
            "mapping_type": "girsanov_drift_only_same_brownian_covariance",
            "risk_neutral_measure_id": raw["q_pricing"][
                "risk_neutral_measure_id"
            ],
            "numeraire_id": raw["q_pricing"]["numeraire_id"],
            "rate_path_id": raw["q_pricing"]["rate_path_id"],
        }
    )
    raw["underlying_dependence_specs"] = [physical, pricing]
    return raw


def materialize_joint_parent(
    *,
    base_config: str | Path,
    private_config_output: str | Path,
    parent_database: str | Path,
) -> Path:
    """Write the complete private config, generate all rows, and freeze once."""

    base = json.loads(Path(base_config).read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        raise ValueError("base generator config must be an object")
    private_config = Path(private_config_output)
    database = Path(parent_database)
    if private_config.exists() or database.exists():
        raise FileExistsError("refusing to overwrite parent build artifacts")
    private_config.parent.mkdir(parents=True, exist_ok=True)
    private_config.write_text(
        json.dumps(joint_parent_config(base), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    config = load_generator_config(private_config)
    with AuthoringPipeline(database, config) as pipeline:
        pipeline.create_smoke_snapshot()
        frozen = pipeline.freeze()
    if frozen["status"] != "FROZEN":
        raise RuntimeError("golden parent did not freeze")
    return database


__all__ = ["joint_parent_config", "materialize_joint_parent"]
