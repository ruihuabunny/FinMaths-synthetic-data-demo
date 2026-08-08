#!/usr/bin/env python3
"""Derive the distinct tick-aligned F2A v5 parent config deterministically."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


SNAPSHOT_ID = "DERIVATIVES-METALS-F2A-MODEL-SIGNAL-TICK-ALIGNED-TDGBM-Q-v1"
CONFIG_ID = "quantlib-randomized-tdgbm-metals-f2a-model-signal-parent-v1"
SOURCE_SNAPSHOT_ID = "DERIVATIVES-METALS-LIQUID-RANDOMIZED-TDGBM-Q-v4"


def derive(raw: dict) -> dict:
    for key, expected in (
        ("schema_version", "1.6.0"),
        ("generator_version", "0.8.0"),
        ("snapshot_id", SOURCE_SNAPSHOT_ID),
        ("business_days", 126),
    ):
        if raw.get(key) != expected:
            raise ValueError(f"source {key} must equal {expected!r}")
    underlyings = raw.get("underlyings", [])
    if len(underlyings) != 22:
        raise ValueError("F2A v5 parent requires the frozen 22-underlying universe")
    for underlying in underlyings:
        drift = underlying["physical_drift"]["nodes"]
        diffusion = underlying["physical_volatility"]["nodes"]
        drift_offsets = [item["day_offset"] for item in drift]
        diffusion_offsets = [item["day_offset"] for item in diffusion]
        if len(drift_offsets) != 3 or drift_offsets != diffusion_offsets:
            raise ValueError("F2A v5 parent requires one shared three-node grid")
        if drift_offsets[0] != 0 or drift_offsets[-1] > 175:
            raise ValueError("every scored F2A v5 node must lie in the 126-day horizon")
    result = json.loads(json.dumps(raw))
    result["snapshot_id"] = SNAPSHOT_ID
    result["generator_config_id"] = CONFIG_ID
    result["underlying_minimum_price_increment"] = "0.01"
    result["option_minimum_price_increment"] = "0.01"
    result["f2a_parent_contract"] = {
        "variant_id": "bsm_model_reconstruction_xut_signal_f2a_v5",
        "role": "distinct_tick_aligned_parent_never_legacy_identity",
        "physical_node_count": 3,
        "shared_drift_diffusion_grid": True,
        "all_scored_nodes_within_public_horizon": True,
        "option_minimum_price_increment": "0.01",
        "underlying_minimum_price_increment": "0.01",
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/generators/quantlib_bsm_metals_f2a_v5_parent_v1.json"),
    )
    args = parser.parse_args()
    result = derive(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
