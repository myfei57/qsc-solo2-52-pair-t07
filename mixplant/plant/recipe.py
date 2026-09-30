"""Read model behind the recipe page."""

from __future__ import annotations

from typing import Any

from ..recipe.grade import catalogue
from ..recipe.list import describe_book


def recipe_view(service: Any, trial_service: Any = None) -> dict[str, Any]:
    """Build the recipe view for the console."""

    view = {
        "book": describe_book(service.book),
        "grades": catalogue(),
        "mapping": None if service.mapping_or_none() is None else service.mapping_or_none().describe(),
        "status": service.status(),
    }
    if trial_service is not None:
        view["trials"] = trial_service.status()
    return view
