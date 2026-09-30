"""Trial pans: declare the pan, hang the laboratory receipt, promote to formal."""

from __future__ import annotations

from typing import Any, Mapping

from ..errors import InterlockBlockedError, OrderViolationError
from ..recipe.grade import grade_for
from ..recipe.recipe import Recipe
from ..recipe.service import RecipeService
from ..recipe.validate import require_valid, validate_recipe
from ..service_base import PlantService
from .policy import RETRY, describe_policy, disposition_for
from .receipt import LabReceipt, verdict_of
from .registry import TrialRegistry
from .trial import OPEN, PROMOTED, RETURNED, SUSPENDED, TrialBatch

DECLARED_KIND = "trial.declared"
RECEIPT_KIND = "trial.receipt"
PROMOTED_KIND = "trial.promoted"

SUBJECT = "trial"


class TrialService(PlantService):
    """Owns the trial registry and the migration of a pan into the book."""

    origin = "trial"
    line = "batch"
    subject = SUBJECT

    def __init__(self, context, recipes: RecipeService, machine=None) -> None:
        super().__init__(context, machine)
        self.recipes = recipes
        self.registry = TrialRegistry()

    def declare_trial(
        self,
        *,
        name: str,
        grade_code: str,
        factors: Mapping[str, float],
        mix_time_ticks: int | None = None,
        moisture_ref_percent: float | None = None,
        declared_by: str = "console",
    ) -> dict[str, Any]:
        """Mark a small pan as a trial and freeze its candidate snapshot."""

        candidate = Recipe(
            name=name,
            grade=grade_for(grade_code),
            version=0,  # a draft: the formal version is only minted at promotion
            factors=dict(factors),
            mix_time_ticks=(
                self.context.config.limits.mix_time_ticks if mix_time_ticks is None else mix_time_ticks
            ),
            moisture_ref_percent=(
                self.context.config.limits.moisture_max_percent / 2.0
                if moisture_ref_percent is None
                else moisture_ref_percent
            ),
        )
        require_valid(validate_recipe(candidate, self.context.config.limits))
        trial = TrialBatch(
            trial_id=self.context.ids.next("trial"),
            name=candidate.name,
            grade_code=candidate.grade.code,
            factors=dict(candidate.factors),
            mix_time_ticks=candidate.mix_time_ticks,
            moisture_ref_percent=candidate.moisture_ref_percent,
            declared_by=declared_by,
            declared_tick=self.context.clock.now(),
        )
        self.registry.declare(trial)
        self.publish(DECLARED_KIND, {"active": True, **trial.describe()})
        self.emit("trial.declared", {"trial_id": trial.trial_id, "name": trial.name, "grade_code": trial.grade_code})
        return {"trial": trial.describe()}

    def attach_receipt(
        self,
        *,
        trial_id: str,
        verdict: str,
        issued_by: str,
        strength_mpa: float | None = None,
        slump_mm: int | None = None,
    ) -> dict[str, Any]:
        """Hang the laboratory receipt on its pan and apply the pan's policy.

        A receipt that arrives after the pan was sealed is backfill: it is
        recorded with ``late`` set, changes nothing, and only raises an alarm
        when it contradicts the verdict the pan was sealed on.
        """

        trial = self.registry.require(trial_id)
        late = trial.sealed
        receipt = LabReceipt(
            receipt_id=self.context.ids.next("receipt"),
            trial_id=trial.trial_id,
            verdict=verdict_of(verdict),
            issued_by=issued_by,
            tick=self.context.clock.now(),
            strength_mpa=strength_mpa,
            slump_mm=slump_mm,
            late=late,
        )
        updated = self.registry.attach(trial_id, receipt)
        disposition: dict[str, Any] | None = None
        conflict: dict[str, Any] | None = None
        if late:
            basis = trial.effective_receipt()
            if basis is not None and basis.verdict != receipt.verdict:
                conflict = {
                    "trial_id": trial_id,
                    "receipt_id": receipt.receipt_id,
                    "sealed_on": basis.verdict,
                    "late_verdict": receipt.verdict,
                }
        elif receipt.qualified:
            if updated.status == SUSPENDED:
                updated = self.registry.transition(trial_id, OPEN)
        else:
            decision = disposition_for(updated.grade_code)
            status = RETURNED if decision == RETRY else SUSPENDED
            updated = self.registry.transition(trial_id, status, disposition=decision)
            disposition = {"disposition": decision, "grade_code": updated.grade_code}
        self.publish(
            RECEIPT_KIND,
            {
                "active": True,
                **receipt.describe(),
                "status": updated.status,
                "disposition": updated.disposition,
            },
        )
        self.emit(
            "trial.receipt",
            {"trial_id": trial_id, "receipt_id": receipt.receipt_id, "verdict": receipt.verdict, "late": late},
        )
        if disposition is not None:
            self.emit(
                "trial.disposition",
                {"trial_id": trial_id, "receipt_id": receipt.receipt_id, **disposition},
            )
        if conflict is not None:
            self.raise_alarm("trial.late_receipt_conflict", conflict)
        return {"receipt": receipt.describe(), "trial": updated.describe(), "disposition": disposition}

    def promote(self, trial_id: str) -> dict[str, Any]:
        """Migrate one confirmed, qualified pan into the formal recipe book.

        Promotion is per pan and per version: exactly one trial is migrated
        by one call, and only while a fresh confirmation for the pan exists.
        """

        trial = self.registry.require(trial_id)
        if trial.status == PROMOTED:
            raise OrderViolationError("trial pan was already promoted", trial_id=trial_id)
        if trial.status == RETURNED:
            raise OrderViolationError(
                "a returned trial pan can only come back as a newly declared trial",
                trial_id=trial_id,
                disposition=trial.disposition,
            )
        receipt = trial.effective_receipt()
        if receipt is None or not receipt.qualified:
            raise InterlockBlockedError(
                "a trial pan may only be promoted against a qualified laboratory receipt",
                trial_id=trial_id,
                verdict=None if receipt is None else receipt.verdict,
            )
        confirmation = self.context.versions.confirmations.require(f"trial:{trial_id}")
        confirmation.require_fresh(self.context.clock.now())
        published = self.recipes.publish_recipe(
            name=trial.name,
            grade_code=trial.grade_code,
            factors=dict(trial.factors),
            mix_time_ticks=trial.mix_time_ticks,
            moisture_ref_percent=trial.moisture_ref_percent,
            source={
                "trial_id": trial.trial_id,
                "receipt_id": receipt.receipt_id,
                "confirmation_id": confirmation.confirmation_id,
            },
        )
        version = int(published["recipe"]["version"])
        sealed = self.registry.seal_promoted(
            trial_id,
            version=version,
            receipt_id=receipt.receipt_id,
            confirmation_id=confirmation.confirmation_id,
            issuer=confirmation.issuer,
        )
        self.publish(
            PROMOTED_KIND,
            {
                "active": True,
                "trial_id": trial_id,
                "name": trial.name,
                "version": version,
                "receipt_id": receipt.receipt_id,
                "confirmation_id": confirmation.confirmation_id,
                "issuer": confirmation.issuer,
            },
        )
        self.emit("trial.promoted", {"trial_id": trial_id, "name": trial.name, "version": version})
        return {"trial": sealed.describe(), "recipe": published["recipe"], "generation": published["generation"]}

    def adopt_declared(self, payload: Mapping[str, Any]) -> None:
        """Adopt a declared pan read back from the stream after a restart."""

        self.registry.declare(TrialBatch.from_document(payload))

    def adopt_receipt(self, payload: Mapping[str, Any]) -> None:
        """Adopt a receipt and the pan state it was recorded with."""

        trial_id = str(payload["trial_id"])
        self.registry.attach(trial_id, LabReceipt.from_document(payload))
        status = payload.get("status")
        if status is not None:
            disposition = payload.get("disposition")
            self.registry.transition(
                trial_id,
                str(status),
                disposition=None if disposition is None else str(disposition),
            )

    def adopt_promoted(self, payload: Mapping[str, Any]) -> None:
        """Adopt the promotion seal read back from the stream after a restart."""

        self.registry.seal_promoted(
            str(payload["trial_id"]),
            version=int(payload["version"]),
            receipt_id=str(payload["receipt_id"]),
            confirmation_id=str(payload["confirmation_id"]),
            issuer=str(payload["issuer"]),
        )

    def status(self) -> dict[str, Any]:
        trials = self.registry.all()
        return {
            "trials": len(trials),
            "open": sum(1 for trial in trials if trial.status == OPEN),
            "suspended": sum(1 for trial in trials if trial.status == SUSPENDED),
            "promoted": sum(1 for trial in trials if trial.status == PROMOTED),
            "returned": sum(1 for trial in trials if trial.status == RETURNED),
            "late_receipts": sum(1 for trial in trials for receipt in trial.receipts if receipt.late),
            "policy": describe_policy(),
        }
