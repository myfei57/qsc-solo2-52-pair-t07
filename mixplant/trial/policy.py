"""Grade-specific rules for a failed laboratory receipt."""

from __future__ import annotations

from ..errors import ValidationError

FAILURE_RETRY = "retry"
FAILURE_RETEST = "retest"

# The response to a failed trial is a fixed quality decision per concrete
# grade, not a choice made by the operator entering the laboratory receipt.
FAILURE_DISPOSITION_BY_GRADE: dict[str, str] = {
    "C20": FAILURE_RETRY,
    "C30": FAILURE_RETRY,
    "C40": FAILURE_RETEST,
}


def disposition_for_failure(grade_code: str) -> str:
    """Return the fixed failure disposition configured for ``grade_code``."""

    disposition = FAILURE_DISPOSITION_BY_GRADE.get(grade_code)
    if disposition is None:
        raise ValidationError(
            "no failed-receipt disposition is configured for this grade", grade=grade_code
        )
    return disposition
