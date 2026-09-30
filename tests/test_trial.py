"""Trial pans: declaration, laboratory receipts, disposition and promotion."""

from __future__ import annotations

import pytest

from mixplant.config import StationConfig
from mixplant.errors import (
    ArtifactExpiredError,
    InterlockBlockedError,
    NotFoundError,
    OrderViolationError,
    ValidationError,
)
from mixplant.runtime import PlantRuntime
from mixplant.trial.policy import RETRY, SUSPEND, describe_policy, disposition_for
from mixplant.trial.trial import OPEN, PROMOTED, RETURNED, SUSPENDED
from tests.support import run

TRIAL = {
    "name": "mix-b",
    "grade": "C30",
    "factors": {"sand": 760.0, "gravel": 1010.0, "cement": 330.0, "water": 175.0},
    "moisture_ref_percent": 4.5,
}


def declare(runtime: PlantRuntime, **overrides) -> str:
    payload = dict(TRIAL)
    payload.update(overrides)
    return run(runtime, "trial.declare", payload)["trial"]["trial_id"]


def receipt(runtime: PlantRuntime, trial_id: str, verdict: str, **extra) -> dict:
    return run(runtime, "trial.receipt", {"trial_id": trial_id, "verdict": verdict, "issued_by": "lab-1", **extra})


def confirm_and_promote(runtime: PlantRuntime, trial_id: str) -> dict:
    run(runtime, "trial.confirm", {"trial_id": trial_id, "operator": "qc-liu"})
    return run(runtime, "trial.promote", {"trial_id": trial_id})


def test_policy_is_pinned_per_grade() -> None:
    assert disposition_for("C20") == SUSPEND
    assert disposition_for("C30") == SUSPEND
    assert disposition_for("C40") == RETRY
    assert disposition_for("C999") == RETRY
    assert describe_policy()["default"] == RETRY


def test_declared_trial_is_marked_separately(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    assert trial_id.startswith("trial-")
    status = runtime.trials.status()
    assert status["trials"] == 1
    assert status["open"] == 1
    # a trial pan never enters the production batch line
    assert runtime.batches.latest() is None
    assert runtime.recipes.book.names() == ()


def test_declare_validates_the_candidate(runtime: PlantRuntime) -> None:
    with pytest.raises(ValidationError):
        declare(runtime, factors={"sand": 0.0})
    with pytest.raises(ValidationError):
        declare(runtime, grade="C999")


def test_unqualified_receipt_blocks_promotion(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "unqualified", strength_mpa=28.1)
    with pytest.raises(InterlockBlockedError):
        confirm_and_promote(runtime, trial_id)


def test_promotion_needs_a_receipt_at_all(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    with pytest.raises(InterlockBlockedError):
        confirm_and_promote(runtime, trial_id)


def test_c30_failure_suspends_and_reinspection_clears(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime, grade="C30")
    failed = receipt(runtime, trial_id, "unqualified")
    assert failed["trial"]["status"] == SUSPENDED
    assert failed["disposition"] == {"disposition": "suspend", "grade_code": "C30"}
    cleared = receipt(runtime, trial_id, "qualified", strength_mpa=33.2)
    assert cleared["trial"]["status"] == OPEN
    promoted = confirm_and_promote(runtime, trial_id)
    assert promoted["trial"]["status"] == PROMOTED


def test_c40_failure_returns_the_pan(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime, grade="C40")
    failed = receipt(runtime, trial_id, "unqualified")
    assert failed["trial"]["status"] == RETURNED
    assert failed["disposition"] == {"disposition": "retry", "grade_code": "C40"}
    with pytest.raises(OrderViolationError):
        confirm_and_promote(runtime, trial_id)


def test_returned_pan_only_takes_late_receipts(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime, grade="C40")
    receipt(runtime, trial_id, "unqualified")
    late = receipt(runtime, trial_id, "qualified")
    assert late["receipt"]["late"] is True
    assert late["trial"]["status"] == RETURNED
    with pytest.raises(OrderViolationError):
        confirm_and_promote(runtime, trial_id)


def test_promotion_is_per_pan_and_needs_its_own_confirmation(runtime: PlantRuntime) -> None:
    first = declare(runtime)
    second = declare(runtime)
    receipt(runtime, first, "qualified")
    receipt(runtime, second, "qualified")
    # no confirmation at all
    with pytest.raises(NotFoundError):
        run(runtime, "trial.promote", {"trial_id": first})
    # a confirmation for one pan does not promote the other
    run(runtime, "trial.confirm", {"trial_id": first})
    with pytest.raises(NotFoundError):
        run(runtime, "trial.promote", {"trial_id": second})
    promoted = run(runtime, "trial.promote", {"trial_id": first})
    assert promoted["trial"]["promotion"]["issuer"] == "console"
    # one promotion migrates exactly one version
    assert runtime.recipes.book.current(TRIAL["name"]).version == promoted["recipe"]["version"]
    with pytest.raises(OrderViolationError):
        run(runtime, "trial.promote", {"trial_id": first})


def test_an_expired_confirmation_refuses_promotion(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "qualified")
    run(runtime, "trial.confirm", {"trial_id": trial_id})
    run(runtime, "clock.advance", {"ticks": 11})
    with pytest.raises(ArtifactExpiredError):
        run(runtime, "trial.promote", {"trial_id": trial_id})


def test_promotion_migrates_a_formal_version_with_provenance(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    qualified = receipt(runtime, trial_id, "qualified", strength_mpa=33.2)
    promoted = confirm_and_promote(runtime, trial_id)
    version = promoted["recipe"]["version"]
    recipe = runtime.recipes.book.current(TRIAL["name"])
    assert recipe.version == version
    assert recipe.factors["cement"] == 330.0
    promotion = promoted["trial"]["promotion"]
    assert promotion["receipt_id"] == qualified["receipt"]["receipt_id"]
    assert promotion["issuer"] == "qc-liu"
    # the formal version traces back to the trial it came from
    chain = runtime.controller.trial_provenance(TRIAL["name"], version)
    assert chain["trial"]["trial_id"] == trial_id
    assert chain["trial"]["status"] == PROMOTED
    # and the recipe record itself carries the source
    published = runtime.view.latest("recipe.published")
    assert published is not None
    assert published.payload["source"]["trial_id"] == trial_id


def test_versions_before_and_after_promotion_stay_on_their_own(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "qualified")
    promoted = confirm_and_promote(runtime, trial_id)
    formal_version = promoted["recipe"]["version"]
    # a later formal publish is a new version and does not rewrite the trial
    run(runtime, "recipe.publish", {**TRIAL, "factors": {"sand": 700.0, "cement": 350.0, "water": 170.0}})
    assert runtime.recipes.book.current(TRIAL["name"]).version == formal_version + 1
    trial = runtime.trials.registry.require(trial_id)
    assert trial.factors["cement"] == 330.0
    assert trial.promotion["version"] == formal_version
    assert runtime.trials.registry.promotion_of(TRIAL["name"], formal_version + 1) is None


def test_late_backfill_never_rewrites_a_promoted_recipe(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "qualified")
    promoted = confirm_and_promote(runtime, trial_id)
    version = promoted["recipe"]["version"]
    before = runtime.recipes.book.current(TRIAL["name"])
    late = receipt(runtime, trial_id, "unqualified", issued_by="lab-2")
    assert late["receipt"]["late"] is True
    assert late["trial"]["status"] == PROMOTED
    after = runtime.recipes.book.current(TRIAL["name"])
    assert after.version == before.version
    assert dict(after.factors) == dict(before.factors)
    # the conflict is visible instead of silent
    assert runtime.alarms.count() == 1
    alarm = runtime.alarms.recent()[0]
    assert alarm["name"] == "trial.late_receipt_conflict"
    # the effective verdict the pan was sealed on is untouched
    trial = runtime.trials.registry.require(trial_id)
    assert trial.effective_receipt().verdict == "qualified"


def test_agreeing_late_receipt_stays_quiet(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "qualified")
    confirm_and_promote(runtime, trial_id)
    late = receipt(runtime, trial_id, "qualified", issued_by="lab-2")
    assert late["receipt"]["late"] is True
    assert runtime.alarms.count() == 0


def test_trial_chain_is_served_to_the_console(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "qualified")
    promoted = confirm_and_promote(runtime, trial_id)
    view = runtime.controller.trial_view()
    assert [trial["trial_id"] for trial in view["trials"]] == [trial_id]
    assert view["promotions"] == [
        {
            "name": TRIAL["name"],
            "version": promoted["recipe"]["version"],
            "trial_id": trial_id,
            "grade_code": "C30",
            "receipt_id": promoted["trial"]["promotion"]["receipt_id"],
            "confirmation_id": promoted["trial"]["promotion"]["confirmation_id"],
            "issuer": "qc-liu",
        }
    ]
    assert view["policy"]["C40"] == "retry"
    recipe_view = runtime.controller.recipe_view()
    assert recipe_view["trials"]["promotions"][0]["trial_id"] == trial_id


def test_trial_transitions_land_in_the_audit_ledger(runtime: PlantRuntime) -> None:
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "unqualified")
    receipt(runtime, trial_id, "qualified")
    confirm_and_promote(runtime, trial_id)
    names = [entry["name"] for entry in runtime.audit.recent(50)]
    assert "trial.declared" in names
    assert "trial.receipt" in names
    assert "trial.promoted" in names
    counts = runtime.audit.counts()
    assert counts["trial.receipt"] == 2
    # and the durable stream carries the whole chain
    kinds = {record.kind for record in runtime.store.visible()}
    assert {"trial.declared", "trial.receipt", "trial.promoted"} <= kinds


def test_restart_rebuilds_the_registry_and_the_chain(tmp_path) -> None:
    config = StationConfig(data_dir=tmp_path / "state")
    first = PlantRuntime(config)
    first.bootstrap()
    trial_id = declare(first)
    receipt(first, trial_id, "unqualified")
    receipt(first, trial_id, "qualified")
    promoted = confirm_and_promote(first, trial_id)
    receipt(first, trial_id, "unqualified", issued_by="lab-2")  # late backfill
    first.close()

    second = PlantRuntime(config)
    second.bootstrap()
    trial = second.trials.registry.require(trial_id)
    assert trial.status == PROMOTED
    assert len(trial.receipts) == 3
    assert trial.receipts[-1].late is True
    assert trial.effective_receipt().verdict == "qualified"
    assert second.recipes.book.current(TRIAL["name"]).version == promoted["recipe"]["version"]
    chain = second.controller.trial_provenance(TRIAL["name"], promoted["recipe"]["version"])
    assert chain["trial"]["trial_id"] == trial_id
    # identifiers continue instead of colliding after the restart
    assert declare(second) != trial_id
    second.close()


def test_console_serves_the_trial_routes(runtime: PlantRuntime) -> None:
    from mixplant.console.pages import PageStore
    from mixplant.console.router import Request
    from mixplant.console.server import ConsoleServer

    server = ConsoleServer(runtime, pages=PageStore(runtime.config.web_dir))
    trial_id = declare(runtime)
    receipt(runtime, trial_id, "qualified")
    promoted = confirm_and_promote(runtime, trial_id)
    import json

    trials = server.handle(Request(method="GET", path="/api/trials"))
    assert trials.ok is True
    assert json.loads(trials.text)["promotions"][0]["trial_id"] == trial_id
    provenance = server.handle(
        Request(
            method="GET",
            path="/api/trials/provenance",
            query={"name": TRIAL["name"], "version": str(promoted["recipe"]["version"])},
        )
    )
    assert json.loads(provenance.text)["trial"]["trial_id"] == trial_id
    posted = server.handle(
        Request(method="POST", path="/api/trial/declare", body={**TRIAL, "name": "mix-c"})
    )
    assert posted.status == 200
    assert json.loads(posted.text)["result"]["trial"]["trial_id"].startswith("trial-")
