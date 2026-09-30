"""Routes for the recipe page and the recipe commands."""

from __future__ import annotations

from typing import Any

from .response import inject_snapshot
from .router import Request, Response, Router

COMMANDS = (
    ("POST", "/api/recipe/select", "recipe.select"),
    ("POST", "/api/trial/open", "trial.open"),
    ("POST", "/api/trial/complete", "trial.complete"),
    ("POST", "/api/trial/receipt", "trial.receipt"),
    ("POST", "/api/trial/receipts", "trial.receipts"),
    ("POST", "/api/trial/promote", "trial.promote"),
)


def register(router: Router, runtime: Any, pages: Any) -> None:
    """Register the recipe routes."""

    router.add(
        "GET",
        "/pages/recipe",
        lambda _: Response.html(inject_snapshot(pages.load("recipe"), runtime.controller.recipe_view())),
    )
    router.add("GET", "/api/recipes", lambda _: Response.json(runtime.controller.recipe_view()))
    router.add("GET", "/api/trials", lambda _: Response.json(runtime.trials.status()))
    router.add(
        "GET",
        "/api/trials/{trial_id}/chain",
        lambda request: Response.json(runtime.trials.chain(request.params["trial_id"])),
    )
    for method, path, command in COMMANDS:
        router.add(method, path, _command(runtime, command))


def _command(runtime: Any, command: str):
    def handler(request: Request) -> Response:
        return Response.json(runtime.dispatch(command, dict(request.body)))

    return handler
