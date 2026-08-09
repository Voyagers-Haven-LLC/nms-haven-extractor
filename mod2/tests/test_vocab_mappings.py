"""Exhaustive vocabulary tests: every enum value the game can produce must map to a
string haven-ui's optionCatalog.json accepts. Run: py -m pytest mod2/tests -v
(or plain: py mod2/tests/test_vocab_mappings.py)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capture.offsets import (  # noqa: E402
    TRADING_CLASSES, WEALTH_CLASSES, CONFLICT_LEVELS, ALIEN_RACES, STAR_TYPES,
)

CATALOG = json.load(open(
    r"C:\Master-Haven\Haven-UI\src\data\optionCatalog.json", encoding="utf-8"))


def catalog_values(key):
    entries = CATALOG[key]
    return {e["value"] if isinstance(e, dict) else e for e in entries}


# (mapping, catalog key, expected enum size) — enum sizes from nmspy exported game structs
CASES = [
    (TRADING_CLASSES, "economy_types", 7),      # cGcTradingClass 0-6
    (WEALTH_CLASSES, "economy_levels", 4),      # cGcWealthClass 0-3
    (CONFLICT_LEVELS, "conflict_levels", 4),    # cGcPlayerConflictData 0-3
    (ALIEN_RACES, "dominant_lifeforms", 9),     # cGcAlienRace 0-8
    (STAR_TYPES, "star_types", 5),              # cGcGalaxyStarTypes 0-4
]


def run():
    failures = []
    for mapping, key, size in CASES:
        allowed = catalog_values(key)
        for i in range(size):
            if i not in mapping:
                failures.append(f"{key}: enum value {i} unmapped")
            elif mapping[i] not in allowed:
                failures.append(f"{key}: enum {i} -> {mapping[i]!r} NOT in catalog {sorted(allowed)}")
    if failures:
        print("FAIL")
        for f in failures:
            print(" ", f)
        return 1
    total = sum(size for _, _, size in CASES)
    print(f"PASS: all {total} enum values map to catalog-valid vocabulary")
    return 0


def test_vocab_mappings():
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
