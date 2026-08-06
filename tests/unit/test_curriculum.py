from __future__ import annotations

from pathlib import Path

from synthetic_derivatives.curriculum import AdaptiveCurriculumScheduler
from synthetic_derivatives.task_space import TaskCoordinates, TaskSpec


def _task(task_id: str, coordinates: TaskCoordinates) -> TaskSpec:
    family = "bsm_multileg" if coordinates.P > 0 else "bsm_vanilla"
    return TaskSpec(
        task_id=task_id,
        task_family_id=family,
        coordinates=coordinates,
        snapshot_id="DERIVATIVES-METALS-LIQUID-BSM-v1",
        snapshot_revision=1,
        method_id="bsm-analytic-price-v1",
        output_contract_id="canonical-float64-json-v1",
    )


def test_scheduler_preserves_20_60_20_stage_mass_and_adapts_within_stage(
    curriculum_config_path: Path,
) -> None:
    scheduler = AdaptiveCurriculumScheduler.from_path(curriculum_config_path)
    tasks = [
        _task("replay", TaskCoordinates(0, 0, 0, 0, 0, 0)),
        _task("current-core", TaskCoordinates(1, 0, 0, 0, 0, 1)),
        _task("current-mastered", TaskCoordinates(1, 0, 0, 1, 1, 1)),
        _task("explore", TaskCoordinates(2, 2, 0, 0, 1, 2)),
    ]
    before = [task.to_dict() for task in tasks]

    weights = scheduler.sampling_weights(
        tasks,
        current_stage=1,
        pass_at_1={
            "replay": 0.99,
            "current-core": 0.5,
            "current-mastered": 0.99,
            "explore": 0.1,
        },
    )

    assert weights == {
        "replay": 0.2,
        "current-core": 0.48,
        "current-mastered": 0.12,
        "explore": 0.2,
    }
    assert weights["current-core"] > weights["current-mastered"]
    assert [task.to_dict() for task in tasks] == before


def test_mastery_diagnostics_do_not_change_binary_reward(
    curriculum_config_path: Path,
) -> None:
    scheduler = AdaptiveCurriculumScheduler.from_path(curriculum_config_path)

    assert scheduler.mastery_band(0.04).action == "decompose_or_sft"
    assert scheduler.mastery_band(0.5).action == "core_rl"
    assert scheduler.mastery_band(0.99).action == "replay_only"
