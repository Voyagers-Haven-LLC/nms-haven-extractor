"""Upload sanity gate (payload/sanity.py) — pure, headless.
Run: py mod2/tests/test_sanity_gate.py
"""
import sys
from pathlib import Path

MOD2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MOD2))

from payload.sanity import payload_sanity_problems, payload_soft_warnings  # noqa: E402


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    return cond


def good():
    return {
        "system_name": "Abarof-Dulin", "glyph_code": "03E9F3545C3E",
        "star_color": "Yellow", "economy_type": "Trading", "economy_strength": "T2",
        "conflict_level": "Low", "dominant_lifeform": "Gek", "galaxy_name": "Euclid",
        "planets": [
            {"name": "Planet A", "biome": "Lush", "weather": "Balmy", "sentinel": "Limited"},
            {"name": "Planet B", "biome": "Barren", "weather": "Dusty", "sentinel": "Minimal", "is_moon": True},
        ],
    }


ok = True

ok &= check("a normal capture passes", payload_sanity_problems(good()) == [])

p = good(); p["economy_type"] = "None"; p["economy_strength"] = "None"; p["conflict_level"] = "None"; p["dominant_lifeform"] = "None"
ok &= check("honest absence ('None' on all four) passes — uninhabited systems are real", payload_sanity_problems(p) == [])

p = good(); p["star_color"] = "Unknown"
ok &= check("plain 'Unknown' (unresolved read) passes", payload_sanity_problems(p) == [])

p = good()
for k in ("economy_type", "economy_strength", "conflict_level", "dominant_lifeform"):
    p.pop(k)
p["no_trade_data"] = True
ok &= check("no_trade_data payload with the four fields omitted passes", payload_sanity_problems(p) == [])

p = good(); p["star_color"] = "Unknown(1065353472)"
r = payload_sanity_problems(p)
ok &= check("raw enum in star_color is REFUSED", any("star_color=Unknown(1065353472)" in x for x in r))

p = good(); p["dominant_lifeform"] = "Unknown(9)"
ok &= check("raw enum in lifeform is REFUSED", any("dominant_lifeform" in x for x in payload_sanity_problems(p)))

p = good(); p["conflict_level"] = "Extreme"
ok &= check("unknown label (not in the display table) is REFUSED", any("conflict_level" in x for x in payload_sanity_problems(p)))

p = good(); p["system_name"] = "   "
ok &= check("empty system name is REFUSED", "empty system name" in payload_sanity_problems(p))

p = good(); p["glyph_code"] = "000000000000"
ok &= check("null glyph is REFUSED", any("null glyph" in x for x in payload_sanity_problems(p)))

p = good(); p["glyph_code"] = "12345"
ok &= check("short glyph is REFUSED", any("invalid glyph" in x for x in payload_sanity_problems(p)))

p = good(); p["planets"] = []
ok &= check("zero bodies is REFUSED", any("0 bodies" in x for x in payload_sanity_problems(p)))

p = good(); p["planets"] = [dict(good()["planets"][0]) for _ in range(9)]
ok &= check("9 bodies is REFUSED (max 8)", any("9 bodies" in x for x in payload_sanity_problems(p)))

p = good(); p["planets"][0]["biome"] = "Unknown(200)"
ok &= check("raw enum in a planet BIOME is REFUSED (planet layout moved)",
            any("planets[0].biome=Unknown(200)" in x for x in payload_sanity_problems(p)))

p = good(); p["planets"][1]["sentinel"] = "Unknown(1065353472)"
ok &= check("raw enum in a soft planet field does NOT refuse ...", payload_sanity_problems(p) == [])
ok &= check("... but is reported as a soft warning",
            payload_soft_warnings(p) == ["planets[1].sentinel=Unknown(1065353472)"])

ok &= check("non-dict payload is refused", payload_sanity_problems(None) == ["payload is not a dict"])

# Negative control: the pre-2.1.0 drain had no such check at all — a payload that
# is nothing but raw enums would have been staged. Prove the gate sees it.
p = good()
for k in ("star_color", "economy_type", "economy_strength", "conflict_level", "dominant_lifeform"):
    p[k] = "Unknown(305419896)"
ok &= check("negative control: the Cosmos-style all-garbage system is refused on five counts",
            len([x for x in payload_sanity_problems(p) if "raw enum" in x]) == 5)

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
raise SystemExit(0 if ok else 1)
