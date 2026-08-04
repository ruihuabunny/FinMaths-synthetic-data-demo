from __future__ import annotations

from pathlib import Path

from synthetic_derivatives.authoring.config import load_generator_config
from synthetic_derivatives.authoring.pipeline import AuthoringPipeline


def test_quantlib_bsm_authoring_template_is_runnable(
    tmp_path: Path, repository_root: Path
) -> None:
    template_path = (
        repository_root
        / "authoring/templates/quantlib_bsm_generator.template.json"
    )
    config = load_generator_config(template_path)

    assert len(config.underlyings) == 1
    assert {item.call_put for item in config.option_templates} == {"call", "put"}

    with AuthoringPipeline(tmp_path / "template.duckdb", config) as pipeline:
        result = pipeline.create_smoke_snapshot()

    assert result["status"] == "COMPLETED"
    assert result["summary"]["business_date_count"] == 5
    assert result["summary"]["underlying_daily_count"] == 5
    assert result["summary"]["option_contract_count"] == 2
    assert result["summary"]["option_daily_count"] == 10
