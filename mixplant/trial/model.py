"""Immutable records describing a small-pan trial mix and its laboratory receipt."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import ValidationError
from ..recipe.grade import Grade, grade_for
from .policy import FAILURE_RETEST, FAILURE_RETRY

TRIAL_SCHEMA_VERSION = 1
TRIAL_KIND = "trial"

STATUS_OPEN = "open"
STATUS_TESTED = "tested"
STATUS_ELIGIBLE = "eligible"
STATUS_RETRYING = "retrying"
STATUS_AWAITING_RETEST = "awaiting_retest"
STATUS_PROMOTED = "promoted"

RECEIPT_DISPOSITIONS = frozenset({FAILURE_RETRY, FAILURE_RETEST})


@dataclass(frozen=True, slots=True)
class LabReceipt:
    """The laboratory report attached to exactly one trial revision."""

    receipt_id: str
    passed: bool
    report_ref: str
    content_hash: str
    issued_by: str
    tick: int
    disposition: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("receipt_id", self.receipt_id),
            ("report_ref", self.report_ref),
            ("content_hash", self.content_hash),
            ("issued_by", self.issued_by),
        ):
            if not value:
                raise ValidationError(f"laboratory receipt {name} must not be empty")
        if self.tick < 0:
            raise ValidationError("laboratory receipt tick must not be negative")
        if self.passed and self.disposition is not None:
            raise ValidationError("a passed receipt cannot carry a failure disposition")
        if not self.passed and self.disposition not in RECEIPT_DISPOSITIONS:
            raise ValidationError(
                "a failed receipt needs a retry or retest disposition", disposition=self.disposition
            )

    def describe(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "passed": self.passed,
            "report_ref": self.report_ref,
            "content_hash": self.content_hash,
            "issued_by": self.issued_by,
            "tick": self.tick,
            "disposition": self.disposition,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "LabReceipt":
        raw_disposition = document.get("disposition")
        return cls(
            receipt_id=str(document["receipt_id"]),
            passed=bool(document["passed"]),
            report_ref=str(document["report_ref"]),
            content_hash=str(document["content_hash"]),
            issued_by=str(document["issued_by"]),
            tick=int(document["tick"]),
            disposition=None if raw_disposition is None else str(raw_disposition),
        )


@dataclass(slots=True)
class TrialMix:
    """A marked small-pan trial and the append-only receipts filed against it."""

    trial_id: str
    revision: int
    grade: Grade
    factors: Mapping[str, float]
    mix_time_ticks: int
    moisture_ref_percent: float
    prepared_by: str
    intended_recipe_name: str | None = None
    approved_by: str | None = None
    status: str = STATUS_OPEN
    receipts: list[LabReceipt] = field(default_factory=list)
    successor_trial_id: str | None = None
    promoted_recipe_name: str | None = None
    promoted_recipe_version: int | None = None
    promotion_tick: int | None = None
    promoted_by: str | None = None
    created_tick: int = 0
    schema_version: int = TRIAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValidationError("trial id must not be empty")
        if self.revision < 1:
            raise ValidationError("trial revision starts at one", trial_id=self.trial_id)
        if not self.prepared_by:
            raise ValidationError("a trial needs the person who prepared the pan")
        if not self.factors:
            raise ValidationError("a trial needs at least one material factor", trial_id=self.trial_id)
        for material, factor in self.factors.items():
            if not material or factor <= 0:
                raise ValidationError(
                    "trial factor must identify a positive material amount",
                    trial_id=self.trial_id,
                    material=material,
                    factor=factor,
                )
        if self.mix_time_ticks < 1:
            raise ValidationError("a trial needs a positive mixing window", trial_id=self.trial_id)
        if not 0.0 <= self.moisture_ref_percent <= 100.0:
            raise ValidationError("trial moisture reference must be a percentage", trial_id=self.trial_id)
        if self.schema_version != TRIAL_SCHEMA_VERSION:
            raise ValidationError(
                "unsupported trial schema version",
                trial_id=self.trial_id,
                schema_version=self.schema_version,
                supported=TRIAL_SCHEMA_VERSION,
            )
        self.factors = MappingProxyType(dict(self.factors))

    @property
    def grade_code(self) -> str:
        return self.grade.code

    @property
    def latest_receipt(self) -> LabReceipt | None:
        return self.receipts[-1] if self.receipts else None

    @property
    def eligible(self) -> bool:
        return self.status == STATUS_ELIGIBLE

    def attach_receipt(self, receipt: LabReceipt) -> None:
        """Append a receipt and derive the immutable status visible at this time."""

        if self.status == STATUS_PROMOTED:
            raise ValidationError(
                "a receipt cannot rewrite a trial that produced an immutable production recipe",
                trial_id=self.trial_id,
            )
        if any(existing.receipt_id == receipt.receipt_id for existing in self.receipts):
            raise ValidationError(
                "laboratory receipt was attached twice",
                trial_id=self.trial_id,
                receipt_id=receipt.receipt_id,
            )
        self.receipts.append(receipt)
        if receipt.passed:
            self.status = STATUS_ELIGIBLE
        elif receipt.disposition == FAILURE_RETRY:
            self.status = STATUS_RETRYING
        elif receipt.disposition == FAILURE_RETEST:
            self.status = STATUS_AWAITING_RETEST
        else:  # pragma: no cover - LabReceipt validates this branch
            raise ValidationError("unknown receipt disposition", disposition=receipt.disposition)

    def mark_retry(self, successor_trial_id: str) -> None:
        if self.status != STATUS_RETRYING:
            raise ValidationError(
                "only a failed receipt configured for retry can open a successor",
                trial_id=self.trial_id,
                status=self.status,
            )
        self.successor_trial_id = successor_trial_id

    def mark_promoted(
        self, *, recipe_name: str, recipe_version: int, tick: int, promoted_by: str
    ) -> None:
        if self.status != STATUS_ELIGIBLE:
            raise ValidationError(
                "only a trial with a passing laboratory receipt may become a production recipe",
                trial_id=self.trial_id,
                status=self.status,
            )
        self.status = STATUS_PROMOTED
        self.promoted_recipe_name = recipe_name
        self.promoted_recipe_version = recipe_version
        self.promotion_tick = tick
        self.promoted_by = promoted_by

    def provenance(self) -> dict[str, Any] | None:
        if self.promoted_recipe_name is None or self.promoted_recipe_version is None:
            return None
        receipt = self.latest_receipt
        return {
            "schema_version": TRIAL_SCHEMA_VERSION,
            "kind": TRIAL_KIND,
            "trial_id": self.trial_id,
            "revision": self.revision,
            "grade": self.grade.code,
            "receipt_id": None if receipt is None else receipt.receipt_id,
            "report_ref": None if receipt is None else receipt.report_ref,
            "content_hash": None if receipt is None else receipt.content_hash,
            "promotion_tick": self.promotion_tick,
            "promoted_by": self.promoted_by,
        }

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": TRIAL_KIND,
            "trial_id": self.trial_id,
            "revision": self.revision,
            "grade": self.grade.describe(),
            "factors": dict(self.factors),
            "mix_time_ticks": self.mix_time_ticks,
            "moisture_ref_percent": self.moisture_ref_percent,
            "prepared_by": self.prepared_by,
            "intended_recipe_name": self.intended_recipe_name,
            "approved_by": self.approved_by,
            "status": self.status,
            "receipts": [receipt.describe() for receipt in self.receipts],
            "successor_trial_id": self.successor_trial_id,
            "promoted_recipe_name": self.promoted_recipe_name,
            "promoted_recipe_version": self.promoted_recipe_version,
            "promotion_tick": self.promotion_tick,
            "promoted_by": self.promoted_by,
            "created_tick": self.created_tick,
            "provenance": self.provenance(),
        }

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trial_id": self.trial_id,
            "revision": self.revision,
            "grade": self.grade.describe(),
            "factors": dict(self.factors),
            "mix_time_ticks": self.mix_time_ticks,
            "moisture_ref_percent": self.moisture_ref_percent,
            "prepared_by": self.prepared_by,
            "intended_recipe_name": self.intended_recipe_name,
            "approved_by": self.approved_by,
            "status": self.status,
            "receipts": [receipt.describe() for receipt in self.receipts],
            "successor_trial_id": self.successor_trial_id,
            "promoted_recipe_name": self.promoted_recipe_name,
            "promoted_recipe_version": self.promoted_recipe_version,
            "promotion_tick": self.promotion_tick,
            "promoted_by": self.promoted_by,
            "created_tick": self.created_tick,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "TrialMix":
        raw_schema = document.get("schema_version", TRIAL_SCHEMA_VERSION)
        schema_version = int(raw_schema)
        if schema_version != TRIAL_SCHEMA_VERSION:
            raise ValidationError(
                "unsupported trial schema version",
                schema_version=schema_version,
                supported=TRIAL_SCHEMA_VERSION,
            )
        grade_document = document.get("grade")
        grade = (
            grade_for(str(grade_document["code"]))
            if isinstance(grade_document, Mapping)
            else grade_for(str(document["grade"]))
        )
        receipts = [
            LabReceipt.from_document(receipt)
            for receipt in document.get("receipts", ())
            if isinstance(receipt, Mapping)
        ]
        return cls(
            trial_id=str(document["trial_id"]),
            revision=int(document["revision"]),
            grade=grade,
            factors={
                str(material): float(value)
                for material, value in dict(document["factors"]).items()
            },
            mix_time_ticks=int(document["mix_time_ticks"]),
            moisture_ref_percent=float(document["moisture_ref_percent"]),
            prepared_by=str(document["prepared_by"]),
            intended_recipe_name=_optional_text(document, "intended_recipe_name"),
            approved_by=_optional_text(document, "approved_by"),
            status=str(document.get("status", STATUS_OPEN)),
            receipts=receipts,
            successor_trial_id=_optional_text(document, "successor_trial_id"),
            promoted_recipe_name=_optional_text(document, "promoted_recipe_name"),
            promoted_recipe_version=_optional_int(document, "promoted_recipe_version"),
            promotion_tick=_optional_int(document, "promotion_tick"),
            promoted_by=_optional_text(document, "promoted_by"),
            created_tick=int(document.get("created_tick", 0)),
            schema_version=schema_version,
        )


def _optional_text(document: Mapping[str, Any], key: str) -> str | None:
    value = document.get(key)
    return None if value is None else str(value)


def _optional_int(document: Mapping[str, Any], key: str) -> int | None:
    value = document.get(key)
    return None if value is None else int(value)
