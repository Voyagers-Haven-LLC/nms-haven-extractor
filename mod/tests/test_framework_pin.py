"""Framework pin (nmspy_pin.py) + the launcher's upgrade decision — pure, headless.
Run: py mod2/tests/test_framework_pin.py
"""
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

# pyproject.toml must carry the SAME pins (the dev/CI side of the same truth)
pyproject = (MOD2.parent / "pyproject.toml").read_text(encoding="utf-8")
ok &= check("pyproject pins nmspy to the same build", f'"nmspy=={nmspy_pin.NMSPY_PIN}"' in pyproject)
ok &= check("pyproject pins pymhf to the same version", f'"pymhf=={nmspy_pin.PYMHF_PIN}"' in pyproject)

# The launcher must read the pin AFTER the mod update and before importing pymhf.
launcher = (MOD2 / "launcher.py").read_text(encoding="utf-8")
i_update = launcher.index("apply_update(latest[\"patch_zip_url\"]")
i_fw = launcher.index("ensure_framework()", i_update)
i_run = launcher.index("from pymhf import run", i_fw)
ok &= check("launcher order: mod update -> ensure_framework -> pymhf import/run", i_update < i_fw < i_run)
ok &= check("launcher installs with the running interpreter (the embedded python)",
            'sys.executable, "-m", "pip", "install"' in launcher)

# build_release stamps the sync client's UA version too (every 2.0.x release said 2.0.0-dev)
br = (MOD2.parent / "build_release.py").read_text(encoding="utf-8")
ok &= check("build_release stamps USER_AGENT_VERSION", "USER_AGENT_VERSION" in br)
ok &= check("build_release refuses a Full zip with a mismatched embedded nmspy", "Refusing to build a Full zip" in br)

print()
print("ALL PASS" if ok else "FAILURES PRESENT")
raise SystemExit(0 if ok else 1)
