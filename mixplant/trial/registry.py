"""The in-memory book of trial pans, rebuilt from the stream on restart."""

from __future__ import annotations

from typing import Any

from ..errors import DuplicateError, NotFoundError
from .receipt import LabReceipt
from .trial import PROMOTED, TrialBatch


class TrialRegistry:
    """Keeps trial pans by identity and the promotion index by recipe version."""

    __slots__ = ("_trials", "_order")

    def __init__(self) -> None:
        self._trials: dict[str, TrialBatch] = {}
        self._order: list[str] = []

    def declare(self, trial: TrialBatch) -> TrialBatch:
        """Register a fresh pan; the same identity may not repeat."""

        if trial.trial_id in self._trials:
            raise DuplicateError("trial identity was reused", trial_id=trial.trial_id)
        self._trials[trial.trial_id] = trial
        self._order.append(trial.trial_id)
        return trial

    def require(self, trial_id: str) -> TrialBatch:
        """Return the pan or refuse the lookup."""

        trial = self._trials.get(trial_id)
        if trial is None:
            raise NotFoundError("trial pan was never declared", trial_id=trial_id)
        return trial

    def replace(self, trial: TrialBatch) -> TrialBatch:
        """Swap one pan for its next state."""

        self.require(trial.trial_id)
        self._trials[trial.trial_id] = trial
        return trial

    def attach(self, trial_id: str, receipt: LabReceipt) -> TrialBatch:
        """Hang a receipt on its pan."""

        return self.replace(self.require(trial_id).attach(receipt))

    def transition(self, trial_id: str, status: str, *, disposition: str | None = None) -> TrialBatch:
        """Move a pan to another status."""

        return self.replace(self.require(trial_id).transition(status, disposition=disposition))

    def seal_promoted(
        self,
        trial_id: str,
        *,
        version: int,
        receipt_id: str,
        confirmation_id: str,
        issuer: str,
    ) -> TrialBatch:
        """Seal a pan as promoted into one formal recipe version."""

        return self.replace(
            self.require(trial_id).seal_promoted(
                version=version,
                receipt_id=receipt_id,
                confirmation_id=confirmation_id,
                issuer=issuer,
            )
        )

    def all(self) -> tuple[TrialBatch, ...]:
        """Return every pan in declaration order."""

        return tuple(self._trials[trial_id] for trial_id in self._order)

    def promotions(self) -> tuple[TrialBatch, ...]:
        """Return every pan that was promoted into the formal book."""

        return tuple(trial for trial in self.all() if trial.status == PROMOTED)

    def promotion_of(self, name: str, version: int) -> TrialBatch | None:
        """Return the pan a formal recipe version was promoted from."""

        for trial in self.promotions():
            promotion = trial.promotion or {}
            if promotion.get("name") == name and promotion.get("version") == version:
                return trial
        return None

    def describe(self) -> list[dict[str, Any]]:
        return [trial.describe() for trial in self.all()]
