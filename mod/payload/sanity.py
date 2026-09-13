"""Upload sanity gate (2.1.0) — the loud-failure half of dropping hand offsets.

A struct read that goes wrong does not raise. It returns *plausible* garbage:
an out-of-range enum rendered as ``Unknown(1065353472)``, a planet count of
200, an empty name. Before 2.1.0 that garbage was uploaded to Haven as real
system data (the Cosmos break did exactly this until captures were held).

``payload_sanity_problems`` is a PURE function over the frozen upload payload.
It answers one question: does this look like a system the game could have
produced? If not, the drain step refuses to stage it and tells the player to
update, instead of poisoning the database.

Rules (kept deliberately narrow so real, rare systems never trip it):
  * system name non-empty, glyph = 12 hex, not the null glyph;
  * 1..MAX_BODIES bodies (planets + moons — the game caps a system at 6, the
    positions arrays hold 8; anything else is a misread);
  * the five system labels, WHEN PRESENT, are either an honest absence
    ("None"/"Unknown") or one of the known display labels;
  * no system-level string of the form ``Unknown(<raw>)`` — that is exactly
    the fingerprint of an enum read from the wrong offset;
  * no planet whose *biome* is ``Unknown(<raw>)`` — biome is read straight
    from the planet struct on every capture, so a raw value there means the
    planet layout itself moved.

Planet-level ``Unknown(<raw>)`` in the softer fields (sentinel, weather...)
is reported separately (``payload_soft_warnings``) so it shows in the terminal
without blocking a whole system on one flaky per-difficulty read; the prod
normaliser (plan A2) owns cleaning those historically.
"""

import re
from typing import Any, Dict, Iterator, List, Tuple

from capture.offsets import (  # display label tables (no offsets live there any more)
    ALIEN_RACES, CONFLICT_LEVELS, STAR_TYPES, TRADING_CLASSES, WEALTH_CLASSES,
)

MAX_BODIES = 8
NULL_GLYPH = "000000000000"

RAW_ENUM_RE = re.compile(r"^Unknown\(-?\d+\)$")
GLYPH_RE = re.compile(r"^[0-9A-Fa-f]{12}$")

ABSENCE = {"None", "Unknown", "", None}

SYSTEM_LABEL_TABLES = (
    ("star_color", STAR_TYPES),
    ("economy_type", TRADING_CLASSES),
    ("economy_strength", WEALTH_CLASSES),
    ("conflict_level", CONFLICT_LEVELS),
    ("dominant_lifeform", ALIEN_RACES),
)

# Planet fields whose raw-enum garbage means the planet struct layout moved.
PLANET_HARD_FIELDS = ("biome",)


def _strings(obj: Any, path: str) -> Iterator[Tuple[str, str]]:
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _strings(v, f"{path}[{i}]")


def payload_sanity_problems(payload: Dict[str, Any], max_bodies: int = MAX_BODIES) -> List[str]:
    """Reasons this payload must NOT be uploaded. Empty list == plausible."""
    problems: List[str] = []
    if not isinstance(payload, dict):
        return ["payload is not a dict"]

    name = str(payload.get("system_name") or "").strip()
    if not name:
        problems.append("empty system name")

    glyph = str(payload.get("glyph_code") or "")
    if not GLYPH_RE.match(glyph):
        problems.append(f"invalid glyph {glyph!r}")
    elif glyph == NULL_GLYPH:
        problems.append("null glyph (coordinate resolution failed)")

    bodies = payload.get("planets")
    if not isinstance(bodies, list):
        problems.append("planets is not a list")
        bodies = []
    elif not (1 <= len(bodies) <= max_bodies):
        problems.append(f"{len(bodies)} bodies (expected 1..{max_bodies})")

    for key, table in SYSTEM_LABEL_TABLES:
        if key not in payload:
            continue  # omitted == no_trade_data path, legitimate
        value = payload.get(key)
        if value in ABSENCE:
            continue
        if not isinstance(value, str):
            problems.append(f"{key}={value!r} is not a string")
        elif RAW_ENUM_RE.match(value):
            problems.append(f"{key}={value} (raw enum out of range)")
        elif value not in set(table.values()):
            problems.append(f"{key}={value!r} is not a known label")

    # any other system-level string carrying a raw enum
    for k, v in payload.items():
        if k == "planets" or not isinstance(v, str):
            continue
        if RAW_ENUM_RE.match(v) and k not in dict(SYSTEM_LABEL_TABLES):
            problems.append(f"{k}={v} (raw enum out of range)")

    for i, body in enumerate(bodies):
        if not isinstance(body, dict):
            problems.append(f"planets[{i}] is not a dict")
            continue
        for field in PLANET_HARD_FIELDS:
            v = body.get(field)
            if isinstance(v, str) and RAW_ENUM_RE.match(v):
                problems.append(f"planets[{i}].{field}={v} (raw enum out of range)")

    return problems


def payload_soft_warnings(payload: Dict[str, Any]) -> List[str]:
    """Planet-level raw-enum values that do NOT block the upload but deserve a
    visible warning (e.g. sentinel level read from the wrong difficulty slot)."""
    warnings: List[str] = []
    for i, body in enumerate(payload.get("planets") or []):
        if not isinstance(body, dict):
            continue
        for path, value in _strings(body, f"planets[{i}]"):
            field = path.rsplit(".", 1)[-1]
            if field in PLANET_HARD_FIELDS:
                continue
            if RAW_ENUM_RE.match(value):
                warnings.append(f"{path}={value}")
    return warnings
