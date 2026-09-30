"""Small-pan trials gate and trace production recipe versions."""

from __future__ import annotations

import pytest

from mixplant.errors import OrderViolationError
from mixplant.runtime import PlantRuntime
from mixplant.trial.policy import FAILURE_RETEST, FAILURE_RETRY, disposition_for_failure
from tests.support import run

FACTORS = {"sand": 760.0, "gravel": 1010.0, "cement": 330.0, "water": 175.0}


def _open_trial(runtime: PlantRuntime, *, grade: str = "C30", retry_of: str | None = None) -> dict:
    payload = {
        "name": "mix-a",
        "grade": grade,
        "factors": FACTORS,
        "prepared_by": "preparer-1",
    }
    if retry_of is not None:
        payload["retry_of"] = retry_of
    return run(runtime, "trial.open", payload)["trial"]


def _complete(runtime: PlantRuntime, trial_id: str) -> dict:
    return run(
        runtime,
        "trial.complete",
        {"trial_id": trial_id, "approved_by": "supervisor-1"},
    )["trial"]


def _receipt(
    runtime: PlantRuntime,
    trial_id: str,
    *,
    passed: bool,
    report_ref: str = "lab/report-1.pdf",
) -> dict:
    return run(
        runtime,
        "trial.receipt",
        {
            "trial_id": trial_id,
            "passed": passed,
            "report_ref": report_ref,
            "content_hash": "sha256:report",
            "issued_by": "lab-1",
        },
    )["trial"]


def _promote(runtime: PlantRuntime, trial_id: str) -> dict:
    return run(
        runtime,
        "trial.promote",
        {"trial_id": trial_id, "name": "mix-a", "promoted_by": "manager-1"},
    )


def test_failure_disposition_is_fixed_by_grade_label() -> None:
    assert disposition_for_failure("C20") == FAILURE_RETRY
    assert disposition_for_failure("C30") == FAILURE_RETRY
    assert disposition_for_failure("C40") == FAILURE_RETEST


def test_trial_is_marked_and_passing_receipt_promotes_one_version(runtime: PlantRuntime) -> None:
    trial = _open_trial(runtime)
    assert trial["kind"] == "trial"
    assert trial["status"] == "open"
    _complete(runtime, trial["trial_id"])
    _receipt(runtime, trial["trial_id"], passed=True)

    result = _promote(runtime, trial["trial_id"])
    recipe = result["recipe"]

    assert recipe["version"] == 1
    assert recipe["provenance"]["trial_id"] == trial["trial_id"]
    assert recipe["provenance"]["receipt_id"] == "receipt-000001"
    assert runtime.recipes.book.version("mix-a", 1).provenance["report_ref"] == "lab/report-1.pdf"
    chain = run(runtime, "trial.chain", {"trial_id": trial["trial_id"]})
    assert chain["promotion_mode"] == "per_version"
    assert chain["formal_recipe"] == {
        "name": "mix-a",
        "version": 1,
        "trial_id": trial["trial_id"],
        "receipt_id": "receipt-000001",
    }


def test_failed_receipt_blocks_promotion_and_grade_rule_selects_retry(runtime: PlantRuntime) -> None:
    trial = _open_trial(runtime, grade="C30")
    _complete(runtime, trial["trial_id"])
    failed = _receipt(runtime, trial["trial_id"], passed=False, report_ref="lab/fail-1.pdf")

    assert failed["status"] == "retrying"
    assert failed["receipts"][-1]["disposition"] == "retry"
    with pytest.raises(OrderViolationError):
        _promote(runtime, trial["trial_id"])

    retry = run(
        runtime,
        "trial.open",
        {
            "name": "mix-a",
            "grade": "C30",
            "factors": FACTORS,
            "prepared_by": "preparer-2",
            "retry_of": trial["trial_id"],
        },
    )["trial"]
    assert retry["trial_id"] == "trial-000001-r2"
    _complete(runtime, retry["trial_id"])
    _receipt(runtime, retry["trial_id"], passed=True, report_ref="lab/pass-2.pdf")
    promoted = _promote(runtime, retry["trial_id"])

    chain = run(runtime, "trial.chain", {"trial_id": trial["trial_id"]})
    assert [item["revision"] for item in chain["revisions"]] == [1, 2]
    assert chain["formal_recipe"]["trial_id"] == retry["trial_id"]
    assert promoted["recipe"]["provenance"]["trial_id"] == retry["trial_id"]


def test_c40_failure_is_suspended_for_retest_until_a_passing_receipt(runtime: PlantRuntime) -> None:
    trial = _open_trial(runtime, grade="C40")
    _complete(runtime, trial["trial_id"])
    failed = _receipt(runtime, trial["trial_id"], passed=False)

    assert failed["status"] == "awaiting_retest"
    assert failed["receipts"][-1]["disposition"] == "retest"
    with pytest.raises(OrderViolationError):
        run(
            runtime,
            "trial.open",
            {
                "name": "mix-a",
                "grade": "C40",
                "factors": FACTORS,
                "prepared_by": "preparer-2",
                "retry_of": trial["trial_id"],
            },
        )

    passed = _receipt(runtime, trial["trial_id"], passed=True, report_ref="lab/retest.pdf")
    assert passed["status"] == "eligible"
    assert _promote(runtime, trial["trial_id"])["recipe"]["provenance"]["receipt_id"] == "receipt-000002"


def test_batch_receipt_completion_is_partial_and_cannot_touch_promoted_history(
    runtime: PlantRuntime,
) -> None:
    first = _open_trial(runtime)
    _complete(runtime, first["trial_id"])
    _receipt(runtime, first["trial_id"], passed=True)
    _promote(runtime, first["trial_id"])

    second = _open_trial(runtime, retry_of=None)
    _complete(runtime, second["trial_id"])
    result = run(
        runtime,
        "trial.receipts",
        {
            "receipts": [
                {
                    "trial_id": second["trial_id"],
                    "passed": True,
                    "report_ref": "lab/second.pdf",
                    "content_hash": "sha256:second",
                    "issued_by": "lab-1",
                },
                {
                    "trial_id": first["trial_id"],
                    "passed": False,
                    "report_ref": "lab/late.pdf",
                    "content_hash": "sha256:late",
                    "issued_by": "lab-1",
                },
            ]
        },
    )

    assert result["partial_failure"] is True
    assert [item["trial_id"] for item in result["attached"]] == [second["trial_id"]]
    assert result["failed"][0]["trial_id"] == first["trial_id"]
    promoted = _promote(runtime, second["trial_id"])
    assert promoted["recipe"]["version"] == 2
    assert runtime.recipes.book.version("mix-a", 1).provenance["receipt_id"] == "receipt-000001"
    assert runtime.recipes.book.version("mix-a", 2).provenance["receipt_id"] == "receipt-000002"


def test_a_late_receipt_cannot_rewrite_an_already_promoted_recipe(runtime: PlantRuntime) -> None:
    trial = _open_trial(runtime)
    _complete(runtime, trial["trial_id"])
    _receipt(runtime, trial["trial_id"], passed=True, report_ref="lab/pass.pdf")
    promoted = _promote(runtime, trial["trial_id"])
    before = dict(promoted["recipe"])

    with pytest.raises(OrderViolationError):
        _receipt(runtime, trial["trial_id"], passed=False, report_ref="lab/late-fail.pdf")

    stored = runtime.recipes.book.version("mix-a", 1).describe()
    assert stored == before
    assert stored["provenance"]["report_ref"] == "lab/pass.pdf"
