"""2.3.0: system identity is galaxy + glyph, and a system name must be printable.

Pure half runs anywhere:   py mod/tests/test_identity_and_names.py
Mod half (drain + journal) needs the pinned framework: dist\\python\\python.exe mod\\tests\\test_identity_and_names.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import json
    import os
    import sys
    import tempfile
    import threading
    from pathlib import Path
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))
    from payload.extraction_core import (validated_system_name, choose_system_name, system_key,
                                         build_system_payload)
    from payload.sanity import payload_sanity_problems

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    # --- names ------------------------------------------------------------------------
    ok &= check("a lone control character is not a name", validated_system_name("\x0e") == "")
    ok &= check("'\\x17 Everion' is rejected whole, not stripped to 'Everion'",
                validated_system_name("\x17 Everion") == "")
    ok &= check("a real name passes and is stripped", validated_system_name(" Osbrek-Uyevk ") == "Osbrek-Uyevk")
    ok &= check("accented letters are printable (kept)", validated_system_name("Sternhöhle") == "Sternhöhle")
    ok &= check("non-strings are not names", validated_system_name(6) == "" and validated_system_name(None) == "")

    ok &= check("junk coords name -> snapshot name", choose_system_name("\x06", "Real Name", "Osbrek", "AB") == "Real Name")
    ok &= check("junk coords + empty snapshot -> procedural", choose_system_name("\x06", "", "Osbrek-Uyevk", "AB") == "Osbrek-Uyevk")
    ok &= check("System_ placeholder loses to a real snapshot name",
                choose_system_name("System_0123", "Real", "", "0123") == "Real")
    ok &= check("nothing valid -> System_<glyph>", choose_system_name("\x06", "", "", "0048F808F6D2") == "System_0048F808F6D2")
    ok &= check("renamed system (coords) wins over everything",
                choose_system_name("Haven Prime", "Osbrek", "Osbrek", "AB") == "Haven Prime")

    coords = {"system_name": "\x06", "glyph_code": "006702A63BCC", "galaxy_name": "Budullangr", "galaxy_index": 6}
    payload = build_system_payload(snapshot={"system_name": ""}, coords=coords, planets=[{"planet_name": "P"}],
                                   extractor_version="t", procedural_name="Osbrek-Uyevk",
                                   now_iso="now", now_ts=0)
    ok &= check("payload: control-character name falls back to the procedural name",
                payload["system_name"] == "Osbrek-Uyevk" and not payload.get("custom_name_applied"))
    payload2 = build_system_payload(snapshot={"system_name": ""}, coords=dict(coords, system_name="My Home"),
                                    planets=[{"planet_name": "P"}], extractor_version="t",
                                    procedural_name="Osbrek-Uyevk", now_iso="now", now_ts=0)
    ok &= check("payload: a real renamed system keeps its name and is marked custom",
                payload2["system_name"] == "My Home" and payload2.get("custom_name_applied") is True
                and "Procedural name: Osbrek-Uyevk" in payload2.get("description", ""))

    good = {"system_name": "Abarof-Dulin", "glyph_code": "03E9F3545C3E", "planets": [{"planet_name": "P", "biome": "Lush"}]}
    ok &= check("sanity: a printable name passes", not payload_sanity_problems(good))
    ok &= check("sanity: a control-character name is REFUSED",
                any("non-printable" in p for p in payload_sanity_problems(dict(good, system_name="\x0e"))))
    ok &= check("sanity: an accented name passes", not payload_sanity_problems(dict(good, system_name="Gewöhnlich")))

    # --- identity ---------------------------------------------------------------------
    euclid = {"glyph_code": "0048f808f6d2", "galaxy_index": 0, "galaxy_name": "Euclid"}
    hilbert = {"glyph_code": "0048F808F6D2", "galaxy_index": 1, "galaxy_name": "Hilbert Dimension"}
    ok &= check("Euclid (index 0) is a valid galaxy, not 'unknown'", system_key(euclid) == "index:0|0048F808F6D2")
    ok &= check("same glyph in another galaxy is a different system", system_key(euclid) != system_key(hilbert))
    ok &= check("galaxy_unknown keys as unknown", system_key(dict(euclid, galaxy_unknown=True)).startswith("unknown|"))
    ok &= check("no index -> keyed by galaxy name, case-folded",
                system_key({"glyph_code": "AB", "galaxy_name": "Calypso"}) == system_key({"glyph_code": "ab", "galaxy_name": "calypso"}))

    # --- the mod: drain + journal (needs the pinned framework) ------------------------
    os.environ.setdefault("PYTEST_VERSION", "1")
    os.environ.setdefault("HAVEN_LOCAL_PORT_TEST", "1")
    os.environ["HAVEN_API_URL"] = "http://127.0.0.1:9"
    try:
        import pymhf  # noqa: F401
        import nmspy  # noqa: F401
        import logging
        logging.disable(logging.CRITICAL)
        import haven_extractor2 as h
    except Exception as e:
        print(f"SKIP mod half: framework not importable here ({e.__class__.__name__})")
        print("ALL PASS" if ok else "FAILURES PRESENT")
        raise SystemExit(0 if ok else 1)

    tmp = Path(tempfile.mkdtemp())
    a = h.HavenExtractor2()
    try:
        a._shutdown_background()            # no uploader/heartbeat against the real world
        for t in threading.enumerate():     # the uploader is inside a 2 s queue.get; let it exit
            if t.name.startswith("Haven") and t is not threading.current_thread():
                t.join(4)
        import queue as _queue
        a._upload_q = _queue.Queue()        # a fresh queue: nothing from the real journal in it
        a._journal_path = tmp / "staged_queue_v2.jsonl"
        a._framework_ok = True
        a._batch_systems[:] = [dict(good, galaxy_name="Euclid", galaxy_index=0),
                               dict(good, galaxy_name="Hilbert Dimension", galaxy_index=1)]
        a._enqueued_keys.clear()
        a._drain_batch_to_sync()
        ok &= check("drain: the same glyph in two galaxies stages BOTH (was: second skipped as a duplicate)",
                    a._upload_q.qsize() == 2 and len(a._enqueued_keys) == 2)
        rows = [json.loads(l) for l in a._journal_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        ok &= check("journal holds both", len(rows) == 2)
        a._journal_remove(system_key(rows[0]))
        rows2 = [json.loads(l) for l in a._journal_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        ok &= check("journal remove by key drops only that galaxy's row",
                    len(rows2) == 1 and rows2[0]["galaxy_index"] == 1)
        ok &= check("journal rewrite left no temp file behind",
                    [p.name for p in tmp.iterdir()] == ["staged_queue_v2.jsonl"])
        # a junk-named entry is refused, keyed by galaxy|glyph
        a._batch_systems[:] = [dict(good, system_name="\x0e", galaxy_name="Euclid", galaxy_index=0, glyph_code="0001F6391900")]
        a._drain_batch_to_sync()
        ok &= check("drain: a control-character name is refused (not staged)",
                    "index:0|0001F6391900" in a._refused_glyphs and a._upload_q.qsize() == 2)
    finally:
        a._shutdown_background()
    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
