"""2.2.1: the game's text ids ride along with the words (payload/extraction_core.build_planet_entry).

Haven keys on the id (the same in every language); the words are the player's language.
    dist/python/python.exe mod/tests/test_text_ids.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from payload.extraction_core import build_planet_entry

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    kw = dict(translate_resource=lambda v: v, hidden_substance_names=(), hidden_substance_ids=())
    german = {
        "planet_name": "Kileyto IV", "biome": "Swamp",
        "flora_display": "Massenhaft", "fauna_display": "Viel",
        "sentinel_display": "Erwarten Gehorsam", "weather_display": "Feuchte Hitze",
        "text_ids": {"flora": "RARITY_HIGH4", "fauna": "RARITY_HIGH6", "sentinel": "SENTINEL_DEFAULT3",
                     "weather": "WEATHER_HUMID2", "description": "SWAMP2", "planet_class": "",
                     "junk": None},
    }
    ok = True
    p = build_planet_entry(german, 1, **kw)
    ok &= check("words stay in the player's language", p["flora_level"] == "Massenhaft"
                and p["sentinel_level"] == "Erwarten Gehorsam")
    ok &= check("text ids ship with the planet", p.get("text_ids", {}).get("flora") == "RARITY_HIGH4"
                and p["text_ids"]["sentinel"] == "SENTINEL_DEFAULT3" and p["text_ids"]["description"] == "SWAMP2")
    ok &= check("empty / non-text ids are dropped", "planet_class" not in p["text_ids"] and "junk" not in p["text_ids"])
    bare = build_planet_entry({"planet_name": "Old Capture", "flora_display": "Bountiful"}, 0, **kw)
    ok &= check("negative control: a capture without ids sends no text_ids key", "text_ids" not in bare)

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
