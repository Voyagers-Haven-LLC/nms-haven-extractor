"""2.3.0: capture_readiness decides whether APPVIEW freezes now or re-reads a few more times.

    py mod/tests/test_capture_readiness.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    from pathlib import Path
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))
    from payload.extraction_core import capture_readiness

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    def body(name, **over):
        b = {"planet_name": name, "flora_display": "Abundant", "fauna_display": "Copious",
             "sentinel_display": "Low", "weather_display": "Humid", "planet_description": "Lush Planet",
             "plant_source": "none"}
        b.update(over)
        return b

    ok = True
    full = {"A": body("A"), "B": body("B"), "C": body("C", plant_source="game", plant_resource="Star Bulb")}
    done, why = capture_readiness(full, {"_planets_count": 3})
    ok &= check(f"3 named, worded, plant line read -> complete ({why})", done)
    done, why = capture_readiness(full, {"_planets_count": None})
    ok &= check("body count unread -> not yet", not done and "body count" in why)
    done, why = capture_readiness({"A": body("A"), "B": body("B")}, {"_planets_count": 3})
    ok &= check("2 of 3 named -> not yet", not done and "2/3" in why)
    done, why = capture_readiness({"A": body("A"), "B": body("B"), "_unnamed_2": body("")}, {"_planets_count": 3})
    ok &= check("a nameless phantom does not count as a body", not done and "2/3" in why)
    done, why = capture_readiness({"A": body("A", flora_display="RARITY_HIGH3")}, {"_planets_count": 1})
    ok &= check("an unresolved id is not a word", not done and "flora_display" in why)
    done, why = capture_readiness({"A": body("A", weather_display="")}, {"_planets_count": 1})
    ok &= check("an empty weather word -> not yet", not done and "weather_display" in why)
    done, why = capture_readiness({"A": body("A", plant_source=None)}, {"_planets_count": 1})
    ok &= check("plant line not read -> not yet", not done and "plant line" in why)
    done, why = capture_readiness({"A": body("A", flora_display="Massenhaft", weather_display="Feuchte Hitze")}, {"_planets_count": 1})
    ok &= check("German words are words (player language is fine)", done)
    done, why = capture_readiness({"A": body("A"), "B": body("B"), "C": body("C"), "D": body("D")}, {"_planets_count": 3})
    ok &= check("more named than the count -> still complete (count is only a cap)", done)
    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
