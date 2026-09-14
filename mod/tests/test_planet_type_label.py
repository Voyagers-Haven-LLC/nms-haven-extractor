"""Planet-type label: the descriptor the game shows ("Viridescent Planet"), composed
from the language template ("Viridescent %PLANETCLASS%") and the class word.
Run: py mod/tests/test_planet_type_label.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    from pathlib import Path
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))
    from payload.extraction_core import planet_type_label, build_planet_entry
    import nms_language as nl

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    ok &= check("template + class -> 'Viridescent Planet'",
                planet_type_label('Viridescent %PLANETCLASS%', 'Planet') == 'Viridescent Planet')
    ok &= check("template + moon class -> 'Paradise Moon'",
                planet_type_label('Paradise %PLANETCLASS%', 'Moon') == 'Paradise Moon')
    ok &= check("no class word: falls back on is_moon",
                planet_type_label('Desert %PLANETCLASS%', '', is_moon=True) == 'Desert Moon'
                and planet_type_label('Desert %PLANETCLASS%', '', is_moon=False) == 'Desert Planet')
    ok &= check("unresolved class id ('PLANETCLASS1') falls back too",
                planet_type_label('Bountiful %PLANETCLASS%', 'PLANETCLASS1') == 'Bountiful Planet')
    ok &= check("special pools have no placeholder and ship as-is",
                planet_type_label('Infested Paradise', 'Planet') == 'Infested Paradise'
                and planet_type_label('Planetary Anomaly', 'Planet') == 'Planetary Anomaly'
                and planet_type_label('Gas Giant', 'Planet') == 'Gas Giant')
    ok &= check("an UNRESOLVED descriptor id never ships as a label",
                planet_type_label('LUSH4', 'Planet') == '' and planet_type_label('UI_PARADISE_PLANET', 'Planet') == ''
                and planet_type_label('INFESTEDLUSH1', 'Planet') == '')
    ok &= check("empty / None -> ''", planet_type_label('', 'Planet') == '' and planet_type_label(None) == ''
                and planet_type_label('None', 'Planet') == '')
    ok &= check("markup stripped, whitespace collapsed",
                planet_type_label('<STELLAR>Shattered<> %PLANETCLASS%', 'Planet') == 'Shattered Planet')

    # the payload carries it as `description` (what the site renders as the type)
    e = build_planet_entry({'planet_name': 'X', 'biome': 'Lush', 'planet_description': 'Viridescent Planet', 'is_moon': False},
                           0, translate_resource=lambda v: v, hidden_substance_names=set(), hidden_substance_ids=set(),
                           biome_plant_resource={}, biome_subtype_plant_override={})
    ok &= check("payload ships the label as description", e.get('description') == 'Viridescent Planet')
    e2 = build_planet_entry({'planet_name': 'X', 'biome': 'Lush', 'planet_description': '', 'is_moon': False},
                            0, translate_resource=lambda v: v, hidden_substance_names=set(), hidden_substance_ids=set(),
                            biome_plant_resource={}, biome_subtype_plant_override={})
    ok &= check("no descriptor -> no description key (not fabricated)", 'description' not in e2)

    # cache builder: the descriptor scanner recognises the template by VALUE and the class ids by shape
    cache_cls = [getattr(nl, n) for n in dir(nl) if isinstance(getattr(nl, n), type) and hasattr(getattr(nl, n), 'CACHE_VERSION')]
    ok &= check("cache version bumped so old caches rebuild", bool(cache_cls) and cache_cls[0].CACHE_VERSION >= 3)
    ok &= check("unprefixed pools recognised (INFESTEDLUSH1 / GASGIANT1), not display text",
                bool(nl.UNPREFIXED_POOL_ID_RE.match('INFESTEDLUSH1')) and bool(nl.UNPREFIXED_POOL_ID_RE.match('GASGIANT1'))
                and not nl.UNPREFIXED_POOL_ID_RE.match('Paradise Planet'))
    ok &= check("descriptor placeholder constant", nl.DESCRIPTOR_PLACEHOLDER == '%PLANETCLASS%')
    ok &= check("PLANETCLASS ids recognised", bool(nl.PLANETCLASS_ID_RE.match('PLANETCLASS2')) and not nl.PLANETCLASS_ID_RE.match('PLANET'))

    # resolver shape rule (language mixin), imported without the game via a stub-free regex check
    import re
    src = (MOD / 'language' / 'language_mixin.py').read_text(encoding='utf-8')
    m = re.search(r"DESCRIPTOR_ID_RE = re\.compile\(r'(.+?)'\)", src)
    ok &= check("resolver has a descriptor id rule", m is not None)
    if m:
        rx = re.compile(m.group(1))
        ok &= check("rule matches LUSH4 / INFESTEDLUSH1 / UI_PARADISE_PLANET / PLANETCLASS1",
                    all(rx.match(x) for x in ('LUSH4', 'INFESTEDLUSH1', 'UI_PARADISE_PLANET', 'PLANETCLASS1')))
        ok &= check("rule does NOT match display text", not any(rx.match(x) for x in ('Paradise Planet', 'Lush', 'Planet', 'High')))
    ok &= check("resolver checks the cache BEFORE the id-shape heuristic",
                src.index('if text_id in self._adjective_file_cache') < src.index('DESCRIPTOR_ID_RE.match(text_id)):\n            return text_id'))

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
