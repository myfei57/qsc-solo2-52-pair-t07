"""What an unqualified laboratory receipt does to a trial pan.

The disposition is decided by the grade (标号) alone, so the same failure
always takes the same path regardless of who is on shift.
"""

from __future__ import annotations

RETRY = "retry"  # 退回重试配: seal the pan, a fresh trial must be declared
SUSPEND = "suspend"  # 挂起等复检: hold the pan until a re-inspection receipt arrives

DISPOSITIONS = (RETRY, SUSPEND)

# High strength grades leave no margin for a suspect mix design, so a failed
# receipt sends the formula back to a fresh trial pan.  Lower grades usually
# fail on sampling or curing noise, so the same pan waits for re-inspection.
GRADE_POLICY: dict[str, str] = {
    "C20": SUSPEND,
    "C30": SUSPEND,
    "C40": RETRY,
}

DEFAULT_DISPOSITION = RETRY


def disposition_for(grade_code: str) -> str:
    """Return the disposition an unqualified receipt forces for ``grade_code``."""

    return GRADE_POLICY.get(grade_code, DEFAULT_DISPOSITION)


def describe_policy() -> dict[str, str]:
    """Describe the table the console renders."""

    return {**GRADE_POLICY, "default": DEFAULT_DISPOSITION}
