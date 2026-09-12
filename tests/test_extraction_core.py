"""
Smoke test for the Haven Extractor data-handling layer (extraction_core.py).

This runs OUTSIDE the game (pymhf/nmspy are unavailable in CI / a plain shell), which
is exactly why the data-transform logic was extracted into extraction_core.py. It
proves the properties that the galaxy + batch fixes depend on:

  * galaxy resolution never silently becomes Euclid, and recovers a real galaxy from
    any readable source (the "always Euclid" fix);
  * each system's payload is built only from its OWN captured data (the batch
    data-loss fix) - 3 systems in a batch keep distinct star/economy/lifeform/galaxy/
    planets with zero bleed;
  * a frozen system is immutable - mutating the live accumulators after freeze does
    NOT change the already-frozen payload (this is what makes a batched system immune
    to the next warp recycling game memory);
  * the planet field mapping and system-name precedence match the prior behaviour.

Run:  python tests/test_extraction_core.py
Exit code 0 == all pass.
"""

import os
import sys

# Import extraction_core from the SHIPPING tree (mod2/payload) without importing
# the pymhf-bound mod. mod/ is the retired 1.x tree and must not be what this
# suite guards.
_MOD_DIR = os.path.join(os.path.dirname(__file__), "..", "mod2", "payload")
sys.path.insert(0, os.path.abspath(_MOD_DIR))

from extraction_core import (  # noqa: E402
    decide_galaxy, build_planet_entry, build_planet_list,
    build_system_payload, galaxy_is_known, select_captures,
)

_failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(name)


# Test doubles: identity resource translation + empty plant tables. The real tables
# live in haven_extractor.py (pymhf-bound); they are carried verbatim into
# build_planet_entry, so the mapping/structure is what we verify here.
def _identity_translate(x):
    return x


_PLANT_BIOME = {"Lush": "Star Bulb", "Frozen": "Frost Crystal"}
_PLANT_SUBTYPE = {"Swamp": "Faecium"}
_HIDDEN_NAMES = {"Rusted Metal Hidden"}
_HIDDEN_IDS = {"HIDDEN1"}


def _planet_builder(captured, index):
    return build_planet_entry(
        captured, index,
        translate_resource=_identity_translate,
        biome_plant_resource=_PLANT_BIOME,
        biome_subtype_plant_override=_PLANT_SUBTYPE,
        hidden_substance_names=_HIDDEN_NAMES,
        hidden_substance_ids=_HIDDEN_IDS,
    )


def _captured_planet(name, biome="Lush", size="Large", is_moon=False, flora="Bountiful",
                     flora_raw=3, common="Ferrite Dust", **extra):
    d = {
        "planet_name": name, "biome": biome, "biome_subtype": "Standard",
        "weather": "Clear", "sentinel": "Minimal", "flora": flora, "fauna": "Regular",
        "flora_raw": flora_raw, "common_resource": common, "uncommon_resource": "Sodium",
        "rare_resource": "Gold", "is_moon": is_moon, "planet_size": size,
    }
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
def test_decide_galaxy():
    print("decide_galaxy:")
    # All sources positively read 0 -> genuine Euclid.
    idx, _ = decide_galaxy([(0, "location"), (0, "planet"), (0, "sysdata")])
    check("all-zero -> Euclid(0)", idx == 0, f"got {idx}")

    # The historical bug shape: scratch reads 0, but an authoritative source reads the
    # real galaxy -> we must recover the non-zero galaxy, NOT report Euclid.
    idx, src = decide_galaxy([(255, "location"), (None, "planet"), (0, "sysdata")])
    check("nonzero recovered over zero/none -> 255", idx == 255, f"got {idx} via {src}")

    # No source readable -> UNKNOWN (never fabricate Euclid).
    idx, src = decide_galaxy([(None, "location"), (None, "planet"), (None, "sysdata")])
    check("all-unreadable -> UNKNOWN(None)", idx is None and src == "unknown", f"got {idx}/{src}")

    # Out-of-range values dropped; the only valid one wins.
    idx, _ = decide_galaxy([(300, "location"), (-5, "planet"), (42, "sysdata")])
    check("out-of-range dropped -> 42", idx == 42, f"got {idx}")

    # Agreement beats a lone disagreeing source.
    idx, _ = decide_galaxy([(5, "location"), (5, "planet"), (9, "sysdata")])
    check("majority agreement -> 5", idx == 5, f"got {idx}")

    # bool must NOT be accepted as an int galaxy index.
    idx, src = decide_galaxy([(True, "location"), (None, "planet")])
    check("bool rejected -> UNKNOWN", idx is None, f"got {idx}/{src}")


# ---------------------------------------------------------------------------
def test_planet_mapping():
    print("build_planet_entry:")
    p = _planet_builder(_captured_planet("Aurora", biome="Lush", flora="Bountiful", flora_raw=3), 0)
    check("flora_level mapped from captured 'flora'", p["flora_level"] == "Bountiful")
    check("planet_name preserved", p["planet_name"] == "Aurora")
    check("plant_resource derived from biome (flora>0)", p.get("plant_resource") == "Star Bulb")

    # No flora -> no plant resource.
    p2 = _planet_builder(_captured_planet("Dead1", biome="Lush", flora="None", flora_raw=0), 1)
    check("no plant_resource when flora_raw==0", "plant_resource" not in p2)

    # Subtype override beats biome default.
    p3 = _planet_builder(_captured_planet("Swampy", biome="Lush", biome_subtype="Swamp", flora_raw=2), 2)
    p3b = build_planet_entry(
        {"planet_name": "Swampy", "biome": "Lush", "biome_subtype": "Swamp",
         "flora": "Average", "flora_raw": 2, "is_moon": False, "planet_size": "Large"},
        2, translate_resource=_identity_translate, biome_plant_resource=_PLANT_BIOME,
        biome_subtype_plant_override=_PLANT_SUBTYPE, hidden_substance_names=_HIDDEN_NAMES,
        hidden_substance_ids=_HIDDEN_IDS,
    )
    check("subtype plant override wins", p3b.get("plant_resource") == "Faecium")

    # Special flags carried only when truthy.
    p4 = _planet_builder(_captured_planet("Boney", ancient_bones=1), 0)
    check("ancient_bones flag carried", p4.get("ancient_bones") == 1)
    p5 = _planet_builder(_captured_planet("Plain"), 0)
    check("absent flag not present", "ancient_bones" not in p5)

    # Hidden-substance fix.
    p6 = _planet_builder(_captured_planet("Hide", common="HIDDEN1"), 0)
    check("hidden substance id -> 'Rusted Metal'", p6["common_resource"] == "Rusted Metal")


# ---------------------------------------------------------------------------
def _system(snapshot, coords, captured_planets, procgen):
    planets = build_planet_list(captured_planets, planet_builder=_planet_builder)
    return build_system_payload(
        snapshot=snapshot, coords=coords, planets=planets,
        extractor_version="1.10.0-test", procedural_name=procgen,
        has_captured=bool(captured_planets),
        now_iso="2026-05-25T00:00:00", now_ts=1700000000,
    )


def test_single_system_payload():
    print("build_system_payload (single):")
    snap = {"system_name": "Live Game-State Name", "star_color": "Red",
            "economy_type": "Trading", "economy_strength": "Wealthy",
            "conflict_level": "Low", "dominant_lifeform": "Gek", "system_seed": 1234,
            "_planets_count": 2, "_prime_planets": 2}
    coords = {"system_name": "Live Game-State Name", "glyph_code": "10AB12CD34EF",
              "galaxy_name": "Odyalutai", "galaxy_index": 255, "region_name": "Test Region",
              "voxel_x": 1, "voxel_y": 2, "voxel_z": 3, "solar_system_index": 7}
    captured = {
        "P1": _captured_planet("P1", biome="Lush"),
        "M1": _captured_planet("M1", biome="Frozen", size="Small", is_moon=True),
    }
    sysd = _system(snap, coords, captured, procgen="Odd Proc Name")

    check("star_color from snapshot", sysd["star_color"] == "Red")
    check("economy from snapshot", sysd["economy_type"] == "Trading")
    check("lifeform from snapshot", sysd["dominant_lifeform"] == "Gek")
    check("galaxy from coords", sysd["galaxy_name"] == "Odyalutai" and sysd["galaxy_index"] == 255)
    check("glyph from coords", sysd["glyph_code"] == "10AB12CD34EF")
    check("planet_count == bodies (incl moon)", sysd["planet_count"] == 2, f"got {sysd['planet_count']}")
    check("private snapshot keys stripped", "_planets_count" not in sysd and "_prime_planets" not in sysd)
    moons = [p for p in sysd["planets"] if p["is_moon"]]
    planets = [p for p in sysd["planets"] if not p["is_moon"]]
    check("moon split: 1 planet + 1 moon", len(planets) == 1 and len(moons) == 1)
    check("game-mode/discord NOT added by core (added at upload)", "discord_username" not in sysd)


def test_system_name_precedence():
    print("system name precedence:")
    base_coords = {"glyph_code": "ABC", "galaxy_name": "Euclid", "galaxy_index": 0,
                   "voxel_x": 0, "voxel_y": 0, "voxel_z": 1, "solar_system_index": 1}

    # 1) Manual/actual name in coords wins.
    c1 = dict(base_coords, system_name="Player Renamed")
    s1 = _system({"system_name": "GameState"}, c1, {}, procgen="Proc")
    check("manual coords name wins", s1["system_name"] == "Player Renamed")
    check("procgen stashed in description when name overridden",
          "Proc" in (s1.get("description") or ""))

    # 2) coords has only a 'System_' placeholder -> fall back to game-state (snapshot) name.
    c2 = dict(base_coords, system_name="System_ABC")
    s2 = _system({"system_name": "GameState Name"}, c2, {}, procgen="Proc2")
    check("game-state name used over System_ placeholder", s2["system_name"] == "GameState Name")

    # 3) Neither manual nor game-state -> procedural name.
    c3 = dict(base_coords, system_name="System_ABC")
    s3 = _system({"system_name": ""}, c3, {}, procgen="Procedural Final")
    check("procgen used when nothing else", s3["system_name"] == "Procedural Final")


def test_batch_independence():
    print("3-system batch independence (the data-loss regression):")
    # Three systems with DISTINCT system-level props + galaxies + planets. Building each
    # from its own (snapshot, coords, captured) must yield three independent payloads.
    sysA = _system(
        {"star_color": "Red", "economy_type": "Trading", "dominant_lifeform": "Gek"},
        {"glyph_code": "AAA", "galaxy_name": "Euclid", "galaxy_index": 0, "system_name": "Alpha",
         "voxel_x": 1, "voxel_y": 1, "voxel_z": 1, "solar_system_index": 1},
        {"a1": _captured_planet("Alpha I", biome="Lush")}, procgen="Alpha")
    sysB = _system(
        {"star_color": "Blue", "economy_type": "Mining", "dominant_lifeform": "Korvax"},
        {"glyph_code": "BBB", "galaxy_name": "Hilbert Dimension", "galaxy_index": 1, "system_name": "Beta",
         "voxel_x": 2, "voxel_y": 2, "voxel_z": 2, "solar_system_index": 2},
        {"b1": _captured_planet("Beta I", biome="Frozen"),
         "b2": _captured_planet("Beta moon", size="Small", is_moon=True)}, procgen="Beta")
    sysC = _system(
        {"star_color": "Purple", "economy_type": "HighTech", "dominant_lifeform": "Vy'keen"},
        {"glyph_code": "CCC", "galaxy_name": "Odyalutai", "galaxy_index": 255, "system_name": "Gamma",
         "voxel_x": 3, "voxel_y": 3, "voxel_z": 3, "solar_system_index": 3},
        {"c1": _captured_planet("Gamma I")}, procgen="Gamma")

    batch = [sysA, sysB, sysC]
    check("A keeps own galaxy/star/econ/life",
          sysA["galaxy_name"] == "Euclid" and sysA["star_color"] == "Red"
          and sysA["economy_type"] == "Trading" and sysA["dominant_lifeform"] == "Gek")
    check("B keeps own galaxy/star/econ/life",
          sysB["galaxy_name"] == "Hilbert Dimension" and sysB["star_color"] == "Blue"
          and sysB["economy_type"] == "Mining" and sysB["dominant_lifeform"] == "Korvax")
    check("C keeps own galaxy/star/econ/life",
          sysC["galaxy_name"] == "Odyalutai" and sysC["star_color"] == "Purple"
          and sysC["economy_type"] == "HighTech" and sysC["dominant_lifeform"] == "Vy'keen")
    check("distinct glyphs preserved",
          {s["glyph_code"] for s in batch} == {"AAA", "BBB", "CCC"})
    check("B has its 2 bodies, A/C have 1 each",
          sysB["planet_count"] == 2 and sysA["planet_count"] == 1 and sysC["planet_count"] == 1)
    check("no galaxy bleed across systems",
          [s["galaxy_index"] for s in batch] == [0, 1, 255])


def test_freeze_isolation():
    print("freeze isolation (immune to later warp recycling memory):")
    # Simulate the live accumulators for system A.
    snapshot = {"star_color": "Red", "economy_type": "Trading", "dominant_lifeform": "Gek"}
    coords = {"glyph_code": "AAA", "galaxy_name": "Euclid", "galaxy_index": 0,
              "system_name": "Alpha", "voxel_x": 1, "voxel_y": 1, "voxel_z": 1,
              "solar_system_index": 1}
    captured = {"a1": _captured_planet("Alpha I", biome="Lush")}

    frozen = _system(snapshot, coords, captured, procgen="Alpha")

    # Now the player warps to system B: the SAME live dicts get mutated/overwritten.
    snapshot["star_color"] = "Blue"
    snapshot["economy_type"] = "Mining"
    snapshot["dominant_lifeform"] = "Korvax"
    coords["galaxy_name"] = "Hilbert Dimension"
    coords["galaxy_index"] = 1
    coords["glyph_code"] = "BBB"
    captured.clear()
    captured["b1"] = _captured_planet("Beta I", biome="Frozen")

    check("frozen star_color unchanged after 'warp'", frozen["star_color"] == "Red")
    check("frozen economy unchanged", frozen["economy_type"] == "Trading")
    check("frozen lifeform unchanged", frozen["dominant_lifeform"] == "Gek")
    check("frozen galaxy unchanged", frozen["galaxy_name"] == "Euclid" and frozen["galaxy_index"] == 0)
    check("frozen glyph unchanged", frozen["glyph_code"] == "AAA")
    check("frozen planet unchanged", frozen["planets"][0]["planet_name"] == "Alpha I"
          and frozen["planets"][0]["biome"] == "Lush")


def _phantom(slot):
    # A nameless capture as stored by the hook's '_unnamed_N' fallback: no planet_name,
    # default Lush/Small data. These are the phantoms that became 'Planet_N' in the blob.
    return {"planet_name": "", "biome": "Lush", "biome_subtype": "Standard",
            "weather": "Clear", "sentinel": "Minimal", "flora": "None", "fauna": "None",
            "flora_raw": 0, "common_resource": "Ferrite Dust", "uncommon_resource": "Sodium",
            "rare_resource": "Gold", "is_moon": False, "planet_size": "Small"}


def test_phantom_filter():
    print("phantom capture filter (the Odusto 2026-06-17 bug):")
    # Odusto shape: 3 real named planets captured first, then 3 nameless phantom fires.
    captured = {
        "Reolus XIII": _captured_planet("Reolus XIII", biome="Lush", size="Small"),
        "Umerisc Tau": _captured_planet("Umerisc Tau", biome="Scorched", size="Medium"),
        "Caeanoi Sigma": _captured_planet("Caeanoi Sigma", biome="Dead", size="Medium"),
        "_unnamed_3": _phantom(3),
        "_unnamed_4": _phantom(4),
        "_unnamed_5": _phantom(5),
    }
    chosen = select_captures(captured, count_hint=3)
    check("phantoms dropped: 3 real bodies kept", len(chosen) == 3, f"got {len(chosen)}")
    names = [k for k, _ in chosen]
    check("kept exactly the named planets",
          names == ["Reolus XIII", "Umerisc Tau", "Caeanoi Sigma"], f"got {names}")

    planets = build_planet_list(captured, planet_builder=_planet_builder, count_hint=3)
    check("built list has no 'Planet_N' placeholder",
          all(not p["planet_name"].startswith("Planet_") for p in planets),
          f"got {[p['planet_name'] for p in planets]}")

    # count_hint is an UPPER cap only — it must NOT pad a short named list with phantoms.
    one_named = {"Real I": _captured_planet("Real I"), "_unnamed_1": _phantom(1), "_unnamed_2": _phantom(2)}
    chosen2 = select_captures(one_named, count_hint=3)
    check("count_hint does not pad with phantoms", len(chosen2) == 1, f"got {len(chosen2)}")

    # An over-long NAMED list is trimmed by the cap (e.g. a stray adjacent-system fire).
    five_named = {f"N{i}": _captured_planet(f"N{i}") for i in range(5)}
    check("count_hint trims over-long named list", len(select_captures(five_named, count_hint=3)) == 3)

    # Fully degraded read (nothing got a name) -> keep the unnamed so it's never planetless.
    all_phantom = {"_unnamed_0": _phantom(0), "_unnamed_1": _phantom(1)}
    check("all-unnamed degraded fallback kept (never zero planets)",
          len(select_captures(all_phantom)) == 2, f"got {len(select_captures(all_phantom))}")

    # No hint -> still drop phantoms when named exist.
    check("phantoms dropped even with no count_hint",
          len(select_captures(captured)) == 3, f"got {len(select_captures(captured))}")


def test_display_adjective_preference():
    print("display-adjective preference (the Odusto 2026-06-17 adjective bug):")
    # The hook stores the generic enum tier in flora/fauna/sentinel/weather; the live
    # PlanetInfo refresh stores the EXACT in-game adjective in *_display. The builder must
    # emit *_display (Umerisc Tau: Full/Abundant/Observant/Heated Atmosphere), not the enum.
    cap = _captured_planet("Umerisc Tau", biome="Scorched", flora="Bountiful", flora_raw=3)
    cap.update({"fauna": "Copious", "sentinel": "Limited", "weather": "Scorched",
                "flora_display": "Full", "fauna_display": "Abundant",
                "sentinel_display": "Observant", "weather_display": "Heated Atmosphere"})
    p = _planet_builder(cap, 0)
    check("flora uses display 'Full' (not enum 'Bountiful')", p["flora_level"] == "Full", f"got {p['flora_level']}")
    check("fauna uses display 'Abundant'", p["fauna_level"] == "Abundant", f"got {p['fauna_level']}")
    check("sentinel uses display 'Observant'", p["sentinel_level"] == "Observant", f"got {p['sentinel_level']}")
    check("weather uses display 'Heated Atmosphere'", p["weather"] == "Heated Atmosphere", f"got {p['weather']}")

    # Fallback: when no *_display was captured (left the system before PlanetInfo populated),
    # keep the enum tier rather than emitting blank.
    cap2 = _captured_planet("EnumOnly", flora="Bountiful", flora_raw=3)
    cap2.update({"fauna": "Copious", "sentinel": "Limited", "weather": "Humid"})
    p2 = _planet_builder(cap2, 1)
    check("flora falls back to enum when no display", p2["flora_level"] == "Bountiful", f"got {p2['flora_level']}")
    check("weather falls back to enum when no display", p2["weather"] == "Humid", f"got {p2['weather']}")

    # clean_weather (passed by the caller) is applied to whichever weather value wins.
    cap3 = dict(cap2)
    cap3["weather_display"] = "raw_weather 6"
    p3 = build_planet_entry(
        cap3, 2, translate_resource=_identity_translate, biome_plant_resource=_PLANT_BIOME,
        biome_subtype_plant_override=_PLANT_SUBTYPE, hidden_substance_names=_HIDDEN_NAMES,
        hidden_substance_ids=_HIDDEN_IDS, clean_weather=lambda w: w.upper(),
    )
    check("clean_weather applied to chosen weather", p3["weather"] == "RAW_WEATHER 6", f"got {p3['weather']}")


def test_galaxy_is_known():
    print("galaxy_is_known (export hard-stop guard):")
    check("known galaxy", galaxy_is_known({"galaxy_name": "Odyalutai"}) is True)
    check("None galaxy_name -> unknown", galaxy_is_known({"galaxy_name": None}) is False)
    check("galaxy_unknown flag -> unknown",
          galaxy_is_known({"galaxy_name": "Euclid", "galaxy_unknown": True}) is False)
    check("missing galaxy -> unknown", galaxy_is_known({}) is False)


def main():
    print("=" * 64)
    print("Haven Extractor data-handling smoke test (extraction_core)")
    print("=" * 64)
    test_decide_galaxy()
    test_planet_mapping()
    test_single_system_payload()
    test_system_name_precedence()
    test_batch_independence()
    test_freeze_isolation()
    test_phantom_filter()
    test_display_adjective_preference()
    test_galaxy_is_known()
    print("=" * 64)
    if _failures:
        print(f"RESULT: {len(_failures)} FAILED -> {_failures}")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
