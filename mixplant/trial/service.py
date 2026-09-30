"""Application service for the small-pan trial to production-recipe workflow."""

from __future__ import annotations

from typing import Any, Mapping

from ..context import SharedContext
from ..errors import MixPlantError, OrderViolationError, ValidationError
from ..recipe.grade import grade_for
from ..recipe.recipe import Recipe
from ..recipe.service import PUBLISHED_KIND, SUBJECT as RECIPE_SUBJECT
from ..recipe.validate import require_valid, validate_recipe
from ..service_base import PlantService
from ..statemachine.machine import SequenceMachine
from .model import (
    LabReceipt,
    STATUS_AWAITING_RETEST,
    STATUS_ELIGIBLE,
    STATUS_OPEN,
    STATUS_PROMOTED,
    STATUS_RETRYING,
    STATUS_TESTED,
    TrialMix,
)
from .policy import disposition_for_failure
from .registry import TrialRegistry

TRIAL_OPENED_KIND = "trial.opened"
TRIAL_COMPLETED_KIND = "trial.completed"
TRIAL_RECEIPT_KIND = "trial.receipt"
TRIAL_PROMOTED_KIND = "trial.promoted"

SUBJECT = "trial"


class TrialService(PlantService):
    """Coordinates marked trial pans, laboratory receipts and recipe promotion."""

    origin = "trial"
    line = "batch"
    subject = SUBJECT

    def __init__(
        self,
        context: SharedContext,
        recipes: Any,
        machine: SequenceMachine | None = None,
        audit: Any = None,
    ) -> None:
        super().__init__(context, machine)
        self.recipes = recipes
        self.audit = audit
        self.trials = TrialRegistry()

    def open_trial(
        self,
        *,
        name: str | None,
        grade_code: str | None,
        factors: Mapping[str, float],
        prepared_by: str,
        mix_time_ticks: int | None = None,
        moisture_ref_percent: float | None = None,
        retry_of: str | None = None,
    ) -> dict[str, Any]:
        """Register a marked small pan, optionally as a retry revision."""

        retry_trial = None if retry_of is None else self.trials.get(retry_of)
        if retry_trial is not None:
            if retry_trial.status != STATUS_RETRYING:
                raise OrderViolationError(
                    "a retry trial can only follow a receipt whose grade rule says retry",
                    trial_id=retry_trial.trial_id,
                    status=retry_trial.status,
                )
            base_id = TrialRegistry.base_id(retry_trial.trial_id)
            revision = self.trials.next_revision(base_id)
            trial_id = TrialRegistry.revision_id(base_id, revision)
            grade = retry_trial.grade
            factors = dict(factors) if factors else dict(retry_trial.factors)
            mix_time_ticks = retry_trial.mix_time_ticks if mix_time_ticks is None else mix_time_ticks
            moisture_ref_percent = (
                retry_trial.moisture_ref_percent
                if moisture_ref_percent is None
                else moisture_ref_percent
            )
        else:
            revision = 1
            trial_id = self.context.ids.next("trial")
            if not grade_code:
                raise ValidationError("a new trial needs a concrete grade")
            grade = grade_for(grade_code)
            factors = dict(factors)

        intended_name = (
            name if retry_trial is None else (name or retry_trial.intended_recipe_name)
        )
        trial = TrialMix(
            trial_id=trial_id,
            revision=revision,
            grade=grade,
            factors=factors,
            intended_recipe_name=intended_name,
            mix_time_ticks=(
                self.context.config.limits.mix_time_ticks
                if mix_time_ticks is None
                else mix_time_ticks
            ),
            moisture_ref_percent=(
                self.context.config.limits.moisture_max_percent / 2.0
                if moisture_ref_percent is None
                else moisture_ref_percent
            ),
            prepared_by=prepared_by,
            created_tick=self.context.clock.now(),
        )
        self._validate_trial_recipe(trial)
        self.trials.add(trial)
        self.publish(
            TRIAL_OPENED_KIND,
            {
                "active": True,
                **trial.to_document(),
                "retry_of": None if retry_trial is None else retry_trial.trial_id,
            },
        )
        if retry_trial is not None:
            retry_trial.mark_retry(trial.trial_id)
            self.publish(
                "trial.retried",
                {
                    "active": True,
                    "predecessor_trial_id": retry_trial.trial_id,
                    "successor_trial_id": trial.trial_id,
                },
            )
        self.emit(
            "trial.opened",
            {"trial_id": trial.trial_id, "revision": revision, "retry_of": retry_of},
        )
        self._audit(
            "trial.opened",
            {"trial_id": trial.trial_id, "revision": revision, "prepared_by": prepared_by},
        )
        return {"trial": trial.describe(), "seq": self.store.last_seq()}

    def complete_trial(self, *, trial_id: str, approved_by: str) -> dict[str, Any]:
        """Record who approved the finished pan before it goes to the laboratory."""

        trial = self.trials.get(trial_id)
        if trial.status != STATUS_OPEN:
            raise OrderViolationError(
                "only a freshly prepared trial can be completed",
                trial_id=trial.trial_id,
                status=trial.status,
            )
        trial.approved_by = approved_by
        trial.status = STATUS_TESTED
        self.publish(
            TRIAL_COMPLETED_KIND,
            {"active": True, "trial_id": trial.trial_id, "approved_by": approved_by},
        )
        self.emit("trial.completed", {"trial_id": trial.trial_id, "approved_by": approved_by})
        self._audit(
            "trial.completed",
            {"trial_id": trial.trial_id, "approved_by": approved_by},
        )
        return {"trial": trial.describe()}

    def attach_receipt(
        self,
        *,
        trial_id: str,
        passed: bool,
        report_ref: str,
        content_hash: str,
        issued_by: str,
    ) -> dict[str, Any]:
        """Attach a laboratory receipt and apply the grade-specific failure rule."""

        trial = self.trials.get(trial_id)
        if trial.status not in {STATUS_TESTED, STATUS_AWAITING_RETEST}:
            raise OrderViolationError(
                "a laboratory receipt belongs to a completed or retest-waiting trial",
                trial_id=trial.trial_id,
                status=trial.status,
            )
        receipt = LabReceipt(
            receipt_id=self.context.ids.next("receipt"),
            passed=passed,
            report_ref=report_ref,
            content_hash=content_hash,
            issued_by=issued_by,
            tick=self.context.clock.now(),
            disposition=None if passed else disposition_for_failure(trial.grade.code),
        )
        trial.attach_receipt(receipt)
        self.publish(
            TRIAL_RECEIPT_KIND,
            {"active": True, "trial_id": trial.trial_id, **receipt.describe()},
        )
        self.emit(
            "trial.receipt_attached",
            {
                "trial_id": trial.trial_id,
                "receipt_id": receipt.receipt_id,
                "passed": passed,
                "disposition": receipt.disposition,
            },
        )
        self._audit(
            "trial.receipt_attached",
            {
                "trial_id": trial.trial_id,
                "receipt_id": receipt.receipt_id,
                "passed": passed,
                "report_ref": report_ref,
                "content_hash": content_hash,
                "disposition": receipt.disposition,
            },
        )
        return {"trial": trial.describe(), "receipt": receipt.describe()}

    def attach_receipts(self, receipts: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Attach many receipts independently so one failure is not a rollback."""

        attached: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for item in receipts:
            if not isinstance(item, Mapping) or "trial_id" not in item:
                continue
            trial_id = str(item["trial_id"])
            try:
                result = self.attach_receipt(
                    trial_id=trial_id,
                    passed=self._bool(item.get("passed", False)),
                    report_ref=str(item.get("report_ref", "")),
                    content_hash=str(item.get("content_hash", "")),
                    issued_by=str(item.get("issued_by", "")),
                )
                attached.append(result["trial"])
            except MixPlantError as error:
                failed.append(
                    {
                        "trial_id": trial_id,
                        "code": getattr(error, "code", "internal_error"),
                        "message": getattr(error, "message", str(error)),
                    }
                )
        return {
            "attached": attached,
            "failed": failed,
            "partial_failure": bool(failed),
            "accepted_partial_failure": True,
        }

    @staticmethod
    def _bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes", "y"}

    def promote_trial(
        self, *, trial_id: str, name: str | None, promoted_by: str
    ) -> dict[str, Any]:
        """Convert one eligible trial revision into one immutable recipe version."""

        trial = self.trials.get(trial_id)
        if trial.status != STATUS_ELIGIBLE:
            raise OrderViolationError(
                "promotion requires the latest receipt to pass laboratory acceptance",
                trial_id=trial.trial_id,
                status=trial.status,
            )
        receipt = trial.latest_receipt
        if receipt is None:
            raise ValidationError("eligible trial is missing its laboratory receipt", trial_id=trial_id)
        recipe_name = name or trial.intended_recipe_name
        if not recipe_name:
            raise ValidationError(
                "promotion needs the production recipe name",
                trial_id=trial.trial_id,
            )

        promotion_tick = self.context.clock.now()
        existing_versions = [recipe.version for recipe in self.recipes.book.history(recipe_name)]
        token = self.context.versions.bump(RECIPE_SUBJECT, tick=promotion_tick)
        if token.value in existing_versions:
            raise ValidationError(
                "recipe version was published twice", name=recipe_name, version=token.value
            )
        trial_token = self.context.versions.bump(SUBJECT, tick=promotion_tick)
        recipe = Recipe(
            name=recipe_name,
            grade=trial.grade,
            version=token.value,
            factors=dict(trial.factors),
            mix_time_ticks=trial.mix_time_ticks,
            moisture_ref_percent=trial.moisture_ref_percent,
            provenance=self._provenance(
                trial, receipt, promoted_by, promotion_tick=promotion_tick
            ),
        )
        require_valid(validate_recipe(recipe, self.context.config.limits))
        self.recipes.book.publish(recipe)

        recipe_record = self.store.stage(
            PUBLISHED_KIND,
            self.recipes.origin,
            token.value,
            {"active": True, **recipe.describe()},
        )
        self.store.stage(
            TRIAL_PROMOTED_KIND,
            self.origin,
            trial_token.value,
            {
                "active": True,
                "trial_id": trial.trial_id,
                "revision": trial.revision,
                "receipt_id": receipt.receipt_id,
                "report_ref": receipt.report_ref,
                "content_hash": receipt.content_hash,
                "recipe_name": recipe.name,
                "recipe_version": recipe.version,
                "promoted_by": promoted_by,
                "recipe_seq": recipe_record.seq,
            },
        )
        self.store.flush()
        self.store.commit_through(self.store.last_seq())

        trial.mark_promoted(
            recipe_name=recipe.name,
            recipe_version=recipe.version,
            tick=promotion_tick,
            promoted_by=promoted_by,
        )
        self.emit("recipe.published", {"name": recipe.name, "version": recipe.version})
        self.emit(
            "trial.promoted",
            {
                "trial_id": trial.trial_id,
                "recipe_name": recipe.name,
                "recipe_version": recipe.version,
            },
        )
        self._audit(
            "trial.promoted",
            {
                "trial_id": trial.trial_id,
                "revision": trial.revision,
                "receipt_id": receipt.receipt_id,
                "recipe_name": recipe.name,
                "recipe_version": recipe.version,
                "promoted_by": promoted_by,
            },
        )
        return {
            "trial": trial.describe(),
            "recipe": recipe.describe(),
            "generation": token.describe(),
        }

    def adopt(self, record: Any) -> None:
        """Adopt one durable trial record during replay."""

        kind = record.kind
        payload = record.payload
        if kind == TRIAL_OPENED_KIND:
            trial = TrialMix.from_document(payload)
            self.trials.adopt(trial)
            self.context.ids.restore("trial", self._numeric_id(trial.trial_id))
        elif kind == TRIAL_COMPLETED_KIND:
            trial = self.trials.get(str(payload["trial_id"]))
            trial.approved_by = str(payload["approved_by"])
            trial.status = STATUS_TESTED
        elif kind == TRIAL_RECEIPT_KIND:
            trial = self.trials.get(str(payload["trial_id"]))
            receipt = LabReceipt.from_document(payload)
            trial.attach_receipt(receipt)
            self.context.ids.restore("receipt", self._numeric_id(receipt.receipt_id))
        elif kind == "trial.retried":
            predecessor = self.trials.get(str(payload["predecessor_trial_id"]))
            predecessor.mark_retry(str(payload["successor_trial_id"]))
        elif kind == TRIAL_PROMOTED_KIND:
            self.context.versions.generations.adopt(SUBJECT, record.generation)
            trial = self.trials.get(str(payload["trial_id"]))
            trial.mark_promoted(
                recipe_name=str(payload["recipe_name"]),
                recipe_version=int(payload["recipe_version"]),
                tick=record.tick,
                promoted_by=str(payload.get("promoted_by", "replay")),
            )

    def chain(self, trial_id: str) -> dict[str, Any]:
        """Return the revisions, receipts and formal recipe link for the console."""

        requested = self.trials.get(trial_id)
        base = TrialRegistry.base_id(requested.trial_id)
        revisions = [
            trial
            for trial in self.trials.all()
            if TrialRegistry.base_id(trial.trial_id) == base
        ]
        revisions.sort(key=lambda trial: trial.revision)
        return {
            "base_trial_id": base,
            "promotion_mode": "per_version",
            "revisions": [trial.describe() for trial in revisions],
            "formal_recipe": self._formal_recipe(revisions),
        }

    def status(self) -> dict[str, Any]:
        return {
            "promotion_mode": "per_version",
            "trials": self.trials.describe(),
        }

    def _validate_trial_recipe(self, trial: TrialMix) -> None:
        recipe = Recipe(
            name=trial.trial_id,
            grade=trial.grade,
            version=0,
            factors=dict(trial.factors),
            mix_time_ticks=trial.mix_time_ticks,
            moisture_ref_percent=trial.moisture_ref_percent,
        )
        require_valid(validate_recipe(recipe, self.context.config.limits))

    @staticmethod
    def _provenance(
        trial: TrialMix, receipt: LabReceipt, promoted_by: str, *, promotion_tick: int
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_kind": "trial",
            "trial_id": trial.trial_id,
            "revision": trial.revision,
            "receipt_id": receipt.receipt_id,
            "report_ref": receipt.report_ref,
            "content_hash": receipt.content_hash,
            "promoted_by": promoted_by,
            "promotion_tick": promotion_tick,
        }

    @staticmethod
    def _formal_recipe(revisions: list[TrialMix]) -> dict[str, Any] | None:
        for trial in reversed(revisions):
            if trial.status == STATUS_PROMOTED:
                return {
                    "name": trial.promoted_recipe_name,
                    "version": trial.promoted_recipe_version,
                    "trial_id": trial.trial_id,
                    "receipt_id": trial.latest_receipt.receipt_id if trial.latest_receipt else None,
                }
        return None

    @staticmethod
    def _numeric_id(identifier: str) -> int:
        if "-r" in identifier:
            return 0
        suffix = identifier.rsplit("-", 1)[-1]
        try:
            return int(suffix)
        except ValueError:
            return 0

    def _audit(self, name: str, detail: dict[str, Any]) -> None:
        """Best effort ledger note; the command records below are the source of truth."""

        if self.audit is None:
            return
        self.audit.note(name, detail)
