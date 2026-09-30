"""The trial release gate is enforced at the runtime command surface."""

from __future__ import annotations

import json

import pytest

from mixplant.console.pages import PageStore
from mixplant.console.router import Request
from mixplant.console.server import ConsoleServer
from mixplant.errors import OrderViolationError
from mixplant.runtime import PlantRuntime
from tests.support import run

FACTORS = {"sand": 760.0, "gravel": 1010.0, "cement": 330.0, "water": 175.0}


def _prepare_accepted_trial(runtime: PlantRuntime, *, grade: str = "C30") -> str:
    trial = run(
        runtime,
        "trial.open",
        {"name": "mix-a", "grade": grade, "factors": FACTORS, "prepared_by": "preparer-1"},
    )["trial"]
    run(runtime, "trial.complete", {"trial_id": trial["trial_id"], "approved_by": "supervisor-1"})
    run(
        runtime,
        "trial.receipt",
        {
            "trial_id": trial["trial_id"],
            "passed": True,
            "report_ref": "lab/accepted.pdf",
            "content_hash": "sha256:accepted",
            "issued_by": "lab-1",
        },
    )
    return trial["trial_id"]


def test_runtime_promotes_per_version_and_blocks_batch_without_accepted_trial(
    runtime: PlantRuntime,
) -> None:
    runtime.recipes.publish_recipe(
        name="legacy",
        grade_code="C30",
        factors=FACTORS,
    )
    with pytest.raises(OrderViolationError):
        run(runtime, "recipe.select", {"name": "legacy"})

    trial_id = _prepare_accepted_trial(runtime)
    run(runtime, "trial.promote", {"trial_id": trial_id, "name": "mix-a", "promoted_by": "manager-1"})
    selected = run(runtime, "recipe.select", {"name": "mix-a"})
    assert selected["recipe"]["version"] == 2
    assert selected["recipe"]["provenance"]["trial_id"] == trial_id


def test_trial_chain_is_served_to_the_console_and_carries_audit_events(runtime: PlantRuntime) -> None:
    trial_id = _prepare_accepted_trial(runtime)
    run(runtime, "trial.promote", {"trial_id": trial_id, "name": "mix-a", "promoted_by": "manager-1"})
    server = ConsoleServer(runtime, pages=PageStore(runtime.config.web_dir))

    response = server.handle(Request(method="GET", path=f"/api/trials/{trial_id}/chain"))
    payload = json.loads(response.text)
    assert response.ok is True
    assert payload["promotion_mode"] == "per_version"
    assert payload["formal_recipe"]["name"] == "mix-a"

    audit_entries = {
        record.payload["name"]
        for record in runtime.store.visible()
        if record.kind == "audit.entry" and record.origin == "audit"
        if isinstance(record.payload.get("detail"), dict)
        and record.payload["detail"].get("trial_id") == trial_id
    }
    assert {"trial.opened", "trial.completed", "trial.receipt_attached", "trial.promoted"} <= audit_entries
