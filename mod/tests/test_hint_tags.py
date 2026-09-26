"""2.1.1: the discovery page's tags reach the capture.

Run:  dist\\python\\python.exe mod\\tests\\test_hint_tags.py

The game fills cGcPlanetData.ExtraResourceHints AFTER the capture hook reads it,
so Ancient Bones / Vile Brood were never captured (live probe 2026-09-25:
Urston IV had UI_BONES_HINT, Xidiusa UI_BUGS_HINT, the capture had neither).
The export refresh now re-reads them through _apply_hint_tags. The sentinel
tags come from the sentinel word's id family through _apply_sentinel_tags.
Inputs below are the exact values the probe read from the game.
"""

# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    from pathlib import Path

    mod = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(mod))
    try:
        from pymhf import Mod  # noqa: F401
        import nmspy.data.exported_types  # noqa: F401
        from capture.hooks_mixin import CaptureHooksMixin
    except Exception as e:
        print(f"SKIP: framework not importable here ({e.__class__.__name__}: {e})")
        return 0

    class Rig(CaptureHooksMixin):
        def __init__(self, live=None):
            self.live = live or {}

        def _translate_live(self, text_id):
            return self.live.get(text_id)

    fails = []

    def check(name, cond):
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    rig = Rig()

    # 1. The probe's real hint lists.
    urston = {}
    rig._apply_hint_tags(urston, ["UI_BONES_HINT", "PLANT_LUSH"], log_name="Urston IV")
    check("Urston IV: Ancient Bones", urston.get('ancient_bones') == 1)
    check("Urston IV: PLANT_LUSH is the plant, Star Bulb", urston.get('plant_resource') == "Star Bulb")
    check("Urston IV: marked as the game's plant", urston.get('plant_source') == "game")
    check("Urston IV: nothing else set", set(urston) == {'ancient_bones', 'plant_resource', 'plant_source'})

    liythio = {}
    rig._apply_hint_tags(liythio, ["PLANT_RADIO"], log_name="Liythio Gamma")
    check("Liythio Gamma: Gamma Root, no tags", liythio == {'plant_resource': 'Gamma Root', 'plant_source': 'game'})

    xidiusa = {}
    rig._apply_hint_tags(xidiusa, ["UI_BUGS_HINT"], log_name="Xidiusa", complete=True)
    check("Xidiusa: Vile Brood", xidiusa.get('vile_brood') == 1)
    check("Xidiusa (Swamp): the game lists no plant, so none is invented", 'plant_resource' not in xidiusa)
    check("Xidiusa: the complete read records 'the game lists none'", xidiusa.get('plant_source') == 'none')
    early = {}
    rig._apply_hint_tags(early, [])                      # the capture hook's too-early read
    check("an early empty read claims nothing about the plant", early == {})
    dead = {}
    rig._apply_hint_tags(dead, [], complete=True)        # Umondale Alpha: complete, no hints at all
    check("a complete empty read -> plant_source none, no tags", dead == {'plant_source': 'none'})

    scrap = {}
    rig._apply_hint_tags(scrap, ["UI_SCRAP_HINT"])
    check("UI_SCRAP_HINT: Salvageable Scrap", scrap.get('salvageable_scrap') == 1)

    # 2. An id the tables don't know is matched by the game's own words.
    rig2 = Rig(live={"UI_NEW_STORM_HINT": "Storm Crystals"})
    storm = {}
    rig2._apply_hint_tags(storm, ["UI_NEW_STORM_HINT"], log_name="x")
    check("unknown id matched by the live translation", storm.get('storm_crystals') == 1)

    # 3. Negative controls: empty (too early) never clears a real tag; junk sets nothing.
    kept = {'ancient_bones': 1}
    rig._apply_hint_tags(kept, [])
    check("an empty early read keeps a real tag", kept.get('ancient_bones') == 1)
    junk = {}
    rig._apply_hint_tags(junk, ["UI_SOMETHING_ELSE", "PLANT_NOTREAL"], log_name="junk")
    check("unknown ids, incl. an unknown PLANT_ id, set nothing", junk == {})

    # 4. Sentinels: the probe's words at difficulty index 2.
    for raw, hi, ag in (("SENTINEL_DEFAULT1", 1, 0),     # Attentive (Urston IV)
                        ("SENTINEL_DEFAULT4", 1, 0),     # Require Orthodoxy (Xidiusa)
                        ("SENTINEL_RARE8", 0, 0),        # Remote (Liythio Gamma)
                        ("SENTINEL_AGGRESSIVE3", 0, 1),
                        ("SENTINEL_HIGH2", 0, 1),
                        ("SENTINEL_CORRUPT1", 0, 0),
                        ("SENTINEL_LOW4", 0, 0)):
        c = {}
        rig._apply_sentinel_tags(c, raw)
        check(f"{raw}: high={hi} aggressive={ag}",
              c.get('high_sentinel_activity') == hi and c.get('aggressive_sentinel_activity') == ag)
    for raw in ("None", "", "Attentive"):
        c = {}
        rig._apply_sentinel_tags(c, raw)
        check(f"{raw!r}: not a sentinel id, no tags written", c == {})

    # 5. The payload ships them and the terminal line names them.
    from payload.extraction_core import build_planet_entry
    cap = {'planet_name': 'Urston IV', 'biome': 'Lush', 'ancient_bones': 1,
           'high_sentinel_activity': 1, 'aggressive_sentinel_activity': 0}
    entry = build_planet_entry(cap, 0, translate_resource=lambda x: x,
                               hidden_substance_names=(), hidden_substance_ids=())
    check("payload carries ancient_bones", entry.get('ancient_bones') == 1)
    check("payload carries high_sentinel_activity", entry.get('high_sentinel_activity') == 1)
    check("a 0 tag is not shipped", 'aggressive_sentinel_activity' not in entry)
    check("no plant captured -> no plant shipped", 'plant_resource' not in entry and 'plant_source' not in entry)
    cap['plant_source'] = 'none'
    entry = build_planet_entry(cap, 0, translate_resource=lambda x: x,
                               hidden_substance_names=(), hidden_substance_ids=())
    check("'game lists none' ships as plant_source=none with no plant",
          'plant_resource' not in entry and entry.get('plant_source') == 'none')
    cap['plant_resource'] = 'Star Bulb'
    entry = build_planet_entry(cap, 0, translate_resource=lambda x: x,
                               hidden_substance_names=(), hidden_substance_ids=())
    check("the game's plant ships marked plant_source=game",
          entry.get('plant_resource') == 'Star Bulb' and entry.get('plant_source') == 'game')
    src = (mod / "payload" / "payload_mixin.py").read_text(encoding="utf-8")
    check("terminal line names High Sentinels", "'High Sentinels'" in src)

    print(f"\n{'FAILED' if fails else 'PASSED'}: {len(fails)} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
