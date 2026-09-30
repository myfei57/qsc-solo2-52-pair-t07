"""Read model behind the trial-to-formal chain the console renders."""

from __future__ import annotations

from typing import Any

from ..trial.policy import describe_policy


def trial_view(service: Any) -> dict[str, Any]:
    """Build the trial pans and the promotion chain for the console."""

    promotions = []
    for trial in service.registry.promotions():
        promotion = dict(trial.promotion or {})
        promotions.append(
            {
                "name": promotion.get("name"),
                "version": promotion.get("version"),
                "trial_id": trial.trial_id,
                "grade_code": trial.grade_code,
                "receipt_id": promotion.get("receipt_id"),
                "confirmation_id": promotion.get("confirmation_id"),
                "issuer": promotion.get("issuer"),
            }
        )
    return {
        "trials": service.registry.describe(),
        "promotions": promotions,
        "policy": describe_policy(),
        "status": service.status(),
    }


def provenance(service: Any, name: str, version: int) -> dict[str, Any]:
    """Trace one formal recipe version back to the trial it came from."""

    trial = service.registry.promotion_of(name, version)
    return {
        "name": name,
        "version": version,
        "trial": None if trial is None else trial.describe(),
    }
