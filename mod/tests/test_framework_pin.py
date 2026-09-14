"""Framework pin (nmspy_pin.py) + the launcher's upgrade decision — pure, headless.
Run: py mod/tests/test_framework_pin.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import re
    import sys
    from pathlib import Path

    MOD2 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD2))

    import nmspy_pin  # noqa: E402


    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond


    ok = True
    pins = {"nmspy": "178994.0", "pymhf": "0.2.4"}

    ok &= check("pin looks like an NMS build id (digits.digits)", re.fullmatch(r"\d{6}\.\d+", nmspy_pin.NMSPY_PIN) is not None)
    ok &= check("PINS mirrors the two constants",
                nmspy_pin.PINS == {"nmspy": nmspy_pin.NMSPY_PIN, "pymhf": nmspy_pin.PYMHF_PIN})

    ok &= check("exact match -> nothing to install",
                nmspy_pin.framework_needs({"nmspy": "178994.0", "pymhf": "0.2.4"}, pins) == [])
    ok &= check("older nmspy -> upgrade to the pin",
                nmspy_pin.framework_needs({"nmspy": "169922.0", "pymhf": "0.2.4"}, pins) == ["nmspy==178994.0"])
    ok &= check("NEWER nmspy is also a mismatch (untested layout is untested)",
                nmspy_pin.framework_needs({"nmspy": "180001.0", "pymhf": "0.2.4"}, pins) == ["nmspy==178994.0"])
    ok &= check("missing dist -> install",
                nmspy_pin.framework_needs({"nmspy": None, "pymhf": None}, pins) == ["nmspy==178994.0", "pymhf==0.2.4"])

    status_ok, detail = nmspy_pin.framework_status()
    ok &= check("framework_status returns the detail keys the state/health surface uses",
                {"nmspy", "nmspy_pin", "pymhf", "pymhf_pin", "framework_ok", "framework_needs"} <= set(detail))
    ok &= check("framework_status ok flag agrees with needs list",
                status_ok == (detail["framework_needs"] == []))

    # In-game self-upgrade helpers: one pip command shape for launcher AND mod,
    # both pins in ONE transaction (169922 + pymhf 0.2.4 is a broken pairing).
    cmd = nmspy_pin.pip_upgrade_command(r"C:\x\python.exe", ["nmspy==178994.0", "pymhf==0.2.4"])
    ok &= check("pip command: python -m pip install --upgrade <both pins>, non-interactive",
                cmd[:4] == [r"C:\x\python.exe", "-m", "pip", "install"] and "--upgrade" in cmd
                and "--no-input" in cmd and cmd[-2:] == ["nmspy==178994.0", "pymhf==0.2.4"])
    exe = nmspy_pin.embedded_python_exe()
    ok &= check("embedded_python_exe returns None or an existing python.exe (never guesses)",
                exe is None or (exe.name.lower() == "python.exe" and exe.is_file()))
    if exe is not None:
        # The interpreter it names must be the one that owns the installed nmspy —
        # pip-installing the pin anywhere else would be exactly the wrong machine.
        import subprocess
        probe = subprocess.run([str(exe), "-c", "import nmspy, os; print(os.path.dirname(nmspy.__file__))"],
                               capture_output=True, text=True, env={**__import__('os').environ, "PYTEST_VERSION": "1"})
        try:
            import nmspy as _n
            same = probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == Path(_n.__file__).resolve().parent
        except Exception:
            same = False
        ok &= check(f"that interpreter ({exe}) owns THIS nmspy install", same)

    # The launcher must read the pin AFTER the mod update and before importing pymhf.
    launcher = (MOD2 / "launcher.py").read_text(encoding="utf-8")
    i_update = launcher.index("apply_update(latest[\"patch_zip_url\"]")
    i_fw = launcher.index("ensure_framework()", i_update)
    i_run = launcher.index("from pymhf import run", i_fw)
    ok &= check("launcher order: mod update -> ensure_framework -> pymhf import/run", i_update < i_fw < i_run)
    ok &= check("launcher installs with the running interpreter (the embedded python) via the shared helper",
                'pip_upgrade_command(sys.executable, needs)' in launcher)

    # build_release stamps the sync client's UA version too (every 2.0.x release said 2.0.0-dev)
    br = (MOD2.parent / "dist" / "build_release.py").read_text(encoding="utf-8")
    ok &= check("build_release stamps USER_AGENT_VERSION", "USER_AGENT_VERSION" in br)
    ok &= check("build_release refuses a Full zip with a mismatched embedded nmspy", "Refusing to build a Full zip" in br)

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
