"""Trial history must rebuild after a restart without reopening promoted versions."""

from __future__ import annotations

from mixplant.config import StationConfig
from mixplant.errors import OrderViolationError
from mixplant.runtime import PlantRuntime
from tests.support import run

FACTORS = {"sand": 760.0, "gravel": 1010.0, "cement": 330.0, "water": 175.0}


def test_restart_restores_trial_chain_and_immutable_promotion(tmp_path) -> None:
    config = StationConfig(data_dir=tmp_path / "state")
    first = PlantRuntime(config)
    first.bootstrap()
    trial = run(
        first,
        "trial.open",
        {"name": "mix-a", "grade": "C30", "factors": FACTORS, "prepared_by": "preparer-1"},
    )["trial"]
    run(first, "trial.complete", {"trial_id": trial["trial_id"], "approved_by": "supervisor-1"})
    run(
        first,
        "trial.receipt",
        {
            "trial_id": trial["trial_id"],
            "passed": False,
            "report_ref": "lab/fail-1.pdf",
            "content_hash": "sha256:fail-1",
            "issued_by": "lab-1",
        },
    )
    retry = run(
        first,
        "trial.open",
        {"retry_of": trial["trial_id"], "prepared_by": "preparer-2"},
    )["trial"]
    run(first, "trial.complete", {"trial_id": retry["trial_id"], "approved_by": "supervisor-2"})
    run(
        first,
        "trial.receipt",
        {
            "trial_id": retry["trial_id"],
            "passed": True,
            "report_ref": "lab/pass-2.pdf",
            "content_hash": "sha256:pass-2",
            "issued_by": "lab-1",
        },
    )
    run(
        first,
        "trial.promote",
        {"trial_id": retry["trial_id"], "promoted_by": "manager-1"},
    )
    first.close()

    second = PlantRuntime(config)
    second.bootstrap()
    chain = run(second, "trial.chain", {"trial_id": trial["trial_id"]})
    assert [item["revision"] for item in chain["revisions"]] == [1, 2]
    assert chain["revisions"][0]["successor_trial_id"] == retry["trial_id"]
    assert chain["formal_recipe"]["version"] == 1
    try:
        run(
            second,
            "trial.receipt",
            {
                "trial_id": retry["trial_id"],
                "passed": False,
                "report_ref": "lab/late.pdf",
                "content_hash": "sha256:late",
                "issued_by": "lab-1",
            },
        )
    except OrderViolationError as error:
        assert error.code == "order_violation"
    else:  # pragma: no cover
        raise AssertionError("late receipt rewrote promoted history")
    assert second.recipes.book.version("mix-a", 1).provenance["trial_id"] == retry["trial_id"]
    second.close()
