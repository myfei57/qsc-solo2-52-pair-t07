"""Routes for the trial pans and the trial-to-formal chain."""

from __future__ import annotations

from typing import Any

from ..errors import ValidationError
from .router import Request, Response, Router

COMMANDS = (
    ("POST", "/api/trial/declare", "trial.declare"),
    ("POST", "/api/trial/receipt", "trial.receipt"),
    ("POST", "/api/trial/confirm", "trial.confirm"),
    ("POST", "/api/trial/promote", "trial.promote"),
)


def register(router: Router, runtime: Any, pages: Any) -> None:
    """Register the trial routes."""

    router.add("GET", "/api/trials", lambda _: Response.json(runtime.controller.trial_view()))
    router.add("GET", "/api/trials/provenance", _provenance(runtime))
    for method, path, command in COMMANDS:
        router.add(method, path, _command(runtime, command))


def _command(runtime: Any, command: str):
    def handler(request: Request) -> Response:
        return Response.json(runtime.dispatch(command, dict(request.body)))

    return handler


def _provenance(runtime: Any):
    def handler(request: Request) -> Response:
        name = request.query.get("name", "")
        if not name:
            raise ValidationError("provenance query needs a recipe name", field="name")
        raw = request.query.get("version", "")
        try:
            version = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("version must be an integer", version=str(raw)) from exc
        return Response.json(runtime.controller.trial_provenance(name, version))

    return handler
