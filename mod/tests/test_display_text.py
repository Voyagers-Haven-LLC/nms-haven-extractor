"""2.3.0: display words keep every printable character (accents, Cyrillic, CJK); game keys stay ASCII.

Needs the pinned framework (hooks_mixin imports pymhf/nmspy):
    dist\\python\\python.exe mod\\tests\\test_display_text.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    from pathlib import Path
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))
    try:
        from capture.hooks_mixin import _display_text, _ascii_id
    except Exception as e:
        print(f"SKIP: framework not importable here ({e.__class__.__name__}: {e})")
        raise SystemExit(0)

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    ok &= check("German umlaut kept ('Gewöhnlich' no longer 'Gewhnlich')", _display_text("Gewöhnlich") == "Gewöhnlich")
    ok &= check("'Küstenstürme' kept", _display_text(" Küstenstürme ") == "Küstenstürme")
    ok &= check("Cyrillic kept", _display_text("Обильная") == "Обильная")
    ok &= check("Japanese kept", _display_text("豊富") == "豊富")
    ok &= check("control characters dropped", _display_text("Few\x00\x01\x02") == "Few")
    ok &= check("None -> ''", _display_text(None) == "")
    ok &= check("an id passes through unchanged", _display_text("RARITY_HIGH3") == "RARITY_HIGH3")
    ok &= check("game keys stay ASCII-only", _ascii_id("UI_BONES_HINT\x00é") == "UI_BONES_HINT")
    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
