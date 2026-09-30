"""Topic names and the origin to topic mapping."""

from __future__ import annotations

from ..errors import ValidationError

AGG = "agg"
CEMENT = "cement"
WATER = "water"
MIX = "mix"
CHUTE = "chute"
PUMP = "pump"
SAMPLER = "sampler"
RECIPE = "recipe"
TRIAL = "trial"
MOIST = "moist"
STORE = "store"
ALARM = "alarm"

TOPICS: tuple[str, ...] = (
    AGG,
    CEMENT,
    WATER,
    MIX,
    CHUTE,
    PUMP,
    SAMPLER,
    RECIPE,
    TRIAL,
    MOIST,
    STORE,
    ALARM,
)

_ORIGIN_TOPICS = {
    "agg": AGG,
    "cement": CEMENT,
    "water": WATER,
    "mix": MIX,
    "chute": CHUTE,
    "pump": PUMP,
    "sampler": SAMPLER,
    "recipe": RECIPE,
    "trial": TRIAL,
    "moist": MOIST,
    "store": STORE,
    "console": STORE,
}


def topic_for(origin: str) -> str:
    """Return the topic that belongs to ``origin``."""

    topic = _ORIGIN_TOPICS.get(origin)
    if topic is None:
        raise ValidationError("origin has no topic", origin=origin)
    return topic
