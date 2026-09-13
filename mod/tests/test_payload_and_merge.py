"""Tests for bug 7 (payload ships captured fields) and bug 6 (ingest merge guard).
Run: py mod/tests/test_payload_and_merge.py"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    from pathlib import Path

    MOD2 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD2))
    # haven-ui is a sibling repo in the LLC tree (C:\Master-Haven-LLC\haven-ui); the
    # merge-guard half of this test imports its db.merge_system_data.
    HAVEN_UI_BACKEND = MOD2.parents[1] / "haven-ui" / "backend"
    sys.path.insert(0, str(HAVEN_UI_BACKEND))

    from payload.extraction_core import build_planet_entry  # noqa: E402


    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond


    ok = True

    # --- Bug 7: captured fields reach the payload --------------------------------
    captured = {
        'planet_name': 'Testworld', 'biome': 'Lush', 'biome_subtype': 'Standard',
        'weather_display': 'Refreshing Breeze', 'weather': 'Clear',
        'sentinel_display': 'Observant', 'flora_display': 'Abundant',
        'fauna_display': 'Copious', 'common_resource': '', 'uncommon_resource': '',
        'rare_resource': '', 'is_moon': False, 'planet_size': 'Medium',
        'is_weather_extreme': True, 'storm_frequency': 'Occasional',
        'planet_description': 'Overgrown Paradise', 'has_rings': True,
    }
    entry = build_planet_entry(
        captured, 0, translate_resource=lambda v: v,
        hidden_substance_names=set(), hidden_substance_ids=set(),
        biome_plant_resource={}, biome_subtype_plant_override={},
    )
    ok &= check("weather ships the display adjective ('Refreshing Breeze')",
                entry["weather"] == "Refreshing Breeze")
    ok &= check("extreme_weather=True shipped", entry.get("extreme_weather") is True)
    ok &= check("storm_frequency shipped", entry.get("storm_frequency") == "Occasional")
    ok &= check("description shipped", entry.get("description") == "Overgrown Paradise")
    ok &= check("has_rings shipped", entry.get("has_rings") is True)

    # Negative control: unobserved fields must NOT be fabricated
    bare = dict(captured)
    for k in ("is_weather_extreme", "storm_frequency", "planet_description"):
        bare[k] = "" if k != "is_weather_extreme" else False
    bare["has_rings"] = None
    entry2 = build_planet_entry(
        bare, 1, translate_resource=lambda v: v,
        hidden_substance_names=set(), hidden_substance_ids=set(),
        biome_plant_resource={}, biome_subtype_plant_override={},
    )
    ok &= check("negative control: unobserved fields absent from payload (not fabricated)",
                "extreme_weather" not in entry2 and "storm_frequency" not in entry2
                and "description" not in entry2 and "has_rings" not in entry2)

    # --- Bug 6: merge guard -------------------------------------------------------
    if not HAVEN_UI_BACKEND.exists():
        print(f"SKIP merge-guard half: haven-ui not checked out at {HAVEN_UI_BACKEND}")
        print()
        print("ALL PASS" if ok else "FAILURES PRESENT")
        raise SystemExit(0 if ok else 1)
    from db import merge_system_data  # noqa: E402

    manual_pending = {'star_color': 'Red', 'conflict_level': 'High',
                      'economy_type': 'Trading', 'dominant_lifeform': 'Gek', 'name': 'Manual Sys'}
    extractor_bad = {'star_color': 'Unknown', 'conflict_level': None,
                     'economy_type': 'Unknown(12)', 'dominant_lifeform': '', 'source': 'haven_extractor'}
    merged = merge_system_data(manual_pending, extractor_bad)
    ok &= check("placeholder extractor values do NOT clobber concrete manual values",
                merged['star_color'] == 'Red' and merged['conflict_level'] == 'High'
                and merged['economy_type'] == 'Trading' and merged['dominant_lifeform'] == 'Gek')

    extractor_good = {'star_color': 'Blue', 'conflict_level': 'Medium', 'source': 'haven_extractor'}
    merged2 = merge_system_data(manual_pending, extractor_good)
    ok &= check("concrete extractor values still update the pending submission",
                merged2['star_color'] == 'Blue' and merged2['conflict_level'] == 'Medium')

    # Negative control: reproduce the old clobber by bypassing the guard semantics
    old_merged = dict(manual_pending)
    for f in ('star_color', 'conflict_level', 'economy_type', 'dominant_lifeform'):
        if f in extractor_bad:
            old_merged[f] = extractor_bad[f]   # old unconditional overwrite
    ok &= check("negative control: old unconditional merge destroys all four manual values (bug reproduced)",
                old_merged['star_color'] == 'Unknown' and old_merged['conflict_level'] is None)

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
