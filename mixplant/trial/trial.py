"""One trial pan (试配盘): a frozen candidate recipe plus its laboratory trail."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from ..errors import ValidationError
from .receipt import LabReceipt

OPEN = "open"
SUSPENDED = "suspended"
PROMOTED = "promoted"
RETURNED = "returned"
STATUSES = (OPEN, SUSPENDED, PROMOTED, RETURNED)
SEALED = (PROMOTED, RETURNED)


@dataclass(frozen=True, slots=True)
class TrialBatch:
    """The candidate snapshot a laboratory verdict is hung on.

    The pan is marked separately from any production batch: it lives in its
    own identity space and never enters the batching line.  Once sealed it is
    immutable history.
    """

    trial_id: str
    name: str
    grade_code: str
    factors: Mapping[str, float]
    mix_time_ticks: int
    moisture_ref_percent: float
    declared_by: str
    declared_tick: int
    status: str = OPEN
    receipts: tuple[LabReceipt, ...] = ()
    disposition: str | None = None
    promotion: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValidationError("a trial pan needs an identity")
        if not self.name:
            raise ValidationError("a trial pan needs a recipe name", trial=self.trial_id)
        if not self.grade_code:
            raise ValidationError("a trial pan needs a grade", trial=self.trial_id)
        if not self.factors:
            raise ValidationError("a trial pan needs material factors", trial=self.trial_id)
        if self.mix_time_ticks < 1:
            raise ValidationError("a trial pan needs a positive mixing window", trial=self.trial_id)
        if not self.declared_by:
            raise ValidationError("a trial pan must name who declared it", trial=self.trial_id)
        if self.declared_tick < 0:
            raise ValidationError("trial tick must not be negative", trial=self.trial_id)
        if self.status not in STATUSES:
            raise ValidationError("unknown trial status", status=self.status, known=list(STATUSES))
        object.__setattr__(self, "factors", MappingProxyType(dict(self.factors)))

    @property
    def sealed(self) -> bool:
        """A promoted or returned pan is history and may not be rewritten."""

        return self.status in SEALED

    def effective_receipt(self) -> LabReceipt | None:
        """Return the newest receipt the pan's fate is read from.

        Late receipts are backfill: they stay on the file but are never the
        basis a decision was made on.
        """

        for receipt in reversed(self.receipts):
            if not receipt.late:
                return receipt
        return None

    def promotable(self) -> bool:
        """A pan may be promoted while it is workable and its receipt passed."""

        receipt = self.effective_receipt()
        return not self.sealed and receipt is not None and receipt.qualified

    def attach(self, receipt: LabReceipt) -> "TrialBatch":
        """Hang one more receipt on the pan."""

        return replace(self, receipts=self.receipts + (receipt,))

    def transition(self, status: str, *, disposition: str | None = None) -> "TrialBatch":
        """Move the pan to another status, recording the disposition in force."""

        return replace(self, status=status, disposition=disposition)

    def seal_promoted(
        self,
        *,
        version: int,
        receipt_id: str,
        confirmation_id: str,
        issuer: str,
    ) -> "TrialBatch":
        """Seal the pan as promoted into one formal recipe version."""

        return replace(
            self,
            status=PROMOTED,
            disposition=None,
            promotion={
                "name": self.name,
                "version": version,
                "receipt_id": receipt_id,
                "confirmation_id": confirmation_id,
                "issuer": issuer,
            },
        )

    def describe(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "name": self.name,
            "grade_code": self.grade_code,
            "factors": dict(self.factors),
            "mix_time_ticks": self.mix_time_ticks,
            "moisture_ref_percent": self.moisture_ref_percent,
            "declared_by": self.declared_by,
            "declared_tick": self.declared_tick,
            "status": self.status,
            "disposition": self.disposition,
            "receipts": [receipt.describe() for receipt in self.receipts],
            "promotion": None if self.promotion is None else dict(self.promotion),
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "TrialBatch":
        """Rebuild a pan from a stored payload."""

        promotion = document.get("promotion")
        return cls(
            trial_id=str(document["trial_id"]),
            name=str(document["name"]),
            grade_code=str(document["grade_code"]),
            factors={str(material): float(value) for material, value in dict(document["factors"]).items()},
            mix_time_ticks=int(document["mix_time_ticks"]),
            moisture_ref_percent=float(document["moisture_ref_percent"]),
            declared_by=str(document["declared_by"]),
            declared_tick=int(document["declared_tick"]),
            status=str(document.get("status", OPEN)),
            receipts=tuple(LabReceipt.from_document(item) for item in document.get("receipts", ())),
            disposition=None if document.get("disposition") is None else str(document["disposition"]),
            promotion=None if promotion is None else dict(promotion),
        )
