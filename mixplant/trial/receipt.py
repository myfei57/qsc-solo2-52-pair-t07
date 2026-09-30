"""The laboratory receipt (回执) that is hung on one trial pan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..errors import ValidationError

QUALIFIED = "qualified"
UNQUALIFIED = "unqualified"
VERDICTS = (QUALIFIED, UNQUALIFIED)


@dataclass(frozen=True, slots=True)
class LabReceipt:
    """One laboratory verdict, filed against exactly one trial pan.

    A receipt filed after its pan was sealed is ``late``: it stays on the
    record for the audit trail but never rewrites the pan or the recipe.
    """

    receipt_id: str
    trial_id: str
    verdict: str
    issued_by: str
    tick: int
    strength_mpa: float | None = None
    slump_mm: int | None = None
    late: bool = False

    def __post_init__(self) -> None:
        if not self.receipt_id:
            raise ValidationError("a receipt needs an identity")
        if not self.trial_id:
            raise ValidationError("a receipt must name its trial pan", receipt=self.receipt_id)
        if self.verdict not in VERDICTS:
            raise ValidationError("unknown receipt verdict", verdict=self.verdict, known=list(VERDICTS))
        if not self.issued_by:
            raise ValidationError("a receipt must name the laboratory issuer", receipt=self.receipt_id)
        if self.tick < 0:
            raise ValidationError("receipt tick must not be negative", receipt=self.receipt_id)

    @property
    def qualified(self) -> bool:
        return self.verdict == QUALIFIED

    def describe(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "trial_id": self.trial_id,
            "verdict": self.verdict,
            "issued_by": self.issued_by,
            "tick": self.tick,
            "strength_mpa": self.strength_mpa,
            "slump_mm": self.slump_mm,
            "late": self.late,
        }

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "LabReceipt":
        """Rebuild a receipt from a stored payload."""

        strength = document.get("strength_mpa")
        slump = document.get("slump_mm")
        return cls(
            receipt_id=str(document["receipt_id"]),
            trial_id=str(document["trial_id"]),
            verdict=str(document["verdict"]),
            issued_by=str(document["issued_by"]),
            tick=int(document["tick"]),
            strength_mpa=None if strength is None else float(strength),
            slump_mm=None if slump is None else int(slump),
            late=bool(document.get("late", False)),
        )


def verdict_of(raw: str) -> str:
    """Parse a command verdict or refuse it."""

    verdict = str(raw).strip().lower()
    if verdict not in VERDICTS:
        raise ValidationError("unknown receipt verdict", verdict=str(raw), known=list(VERDICTS))
    return verdict
