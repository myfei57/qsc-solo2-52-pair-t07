"""In-memory registry reconstructed from durable trial records."""

from __future__ import annotations

from typing import Any

from ..errors import DuplicateError, NotFoundError, ValidationError
from .model import TrialMix


class TrialRegistry:
    """Indexes every small-pan trial and enforces stable identifiers."""

    __slots__ = ("_trials", "_revision_by_base")

    def __init__(self) -> None:
        self._trials: dict[str, TrialMix] = {}
        self._revision_by_base: dict[str, int] = {}

    @staticmethod
    def base_id(trial_id: str) -> str:
        marker = "-r"
        if marker in trial_id:
            return trial_id.rsplit(marker, 1)[0]
        return trial_id

    @classmethod
    def revision_id(cls, base_trial_id: str, revision: int) -> str:
        if revision < 1:
            raise ValidationError("trial revision starts at one", revision=revision)
        return base_trial_id if revision == 1 else f"{cls.base_id(base_trial_id)}-r{revision}"

    def add(self, trial: TrialMix) -> TrialMix:
        if trial.trial_id in self._trials:
            raise DuplicateError("trial was registered twice", trial_id=trial.trial_id)
        self._trials[trial.trial_id] = trial
        base = self.base_id(trial.trial_id)
        self._revision_by_base[base] = max(self._revision_by_base.get(base, 0), trial.revision)
        return trial

    def adopt(self, trial: TrialMix) -> TrialMix:
        existing = self._trials.get(trial.trial_id)
        if existing is not None:
            return existing
        return self.add(trial)

    def get(self, trial_id: str) -> TrialMix:
        trial = self._trials.get(trial_id)
        if trial is None:
            raise NotFoundError("trial was never registered", trial_id=trial_id)
        return trial

    def next_revision(self, base_trial_id: str) -> int:
        base = self.base_id(base_trial_id)
        return self._revision_by_base.get(base, 0) + 1

    def all(self) -> tuple[TrialMix, ...]:
        return tuple(self._trials.values())

    def describe(self) -> list[dict[str, Any]]:
        return [trial.describe() for trial in self._trials.values()]
