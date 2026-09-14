"""Local API CORS: only havenmap.online (+ HAVEN_ALLOWED_ORIGINS) may read
responses in a browser. Real HTTP against a LocalApi on a free 877x port.
Run: py mod/tests/test_local_api_cors.py
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import sys
    import urllib.request
    from pathlib import Path

    MOD2 = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD2))

    from api_local.server import LocalApi, parse_origins, DEFAULT_ALLOWED_ORIGINS  # noqa: E402
    from state import ExtractorState  # noqa: E402
    from telemetry.events import EventBus  # noqa: E402

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    ok &= check("parse_origins: prod always present, extras trimmed, trailing slash dropped",
                parse_origins(" http://100.70.191.5:5173/, http://localhost:5173 ,, ")
                == frozenset(DEFAULT_ALLOWED_ORIGINS) | {"http://100.70.191.5:5173", "http://localhost:5173"})
    ok &= check("parse_origins(None) == prod only", parse_origins(None) == frozenset(DEFAULT_ALLOWED_ORIGINS))

    api = LocalApi(ExtractorState(), EventBus(), allowed_origins="http://localhost:5173")
    port = api.start(8779)
    if not port:
        print("SKIP: no free port in 8770-8779")
        raise SystemExit(0)
    try:
        def get(origin=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/status",
                                         headers={"Origin": origin} if origin else {})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.headers.get("Access-Control-Allow-Origin"), resp.headers.get("Vary")

        s, acao, vary = get("https://havenmap.online")
        ok &= check("prod origin echoed back + Vary: Origin", s == 200 and acao == "https://havenmap.online" and vary == "Origin")
        s, acao, _ = get("http://localhost:5173")
        ok &= check("HAVEN_ALLOWED_ORIGINS entry allowed", s == 200 and acao == "http://localhost:5173")
        s, acao, _ = get("https://evil.example")
        ok &= check("foreign origin: 200 but NO Allow-Origin header (browser blocks the read)", s == 200 and acao is None)
        s, acao, _ = get("https://havenmap.online.evil.example")
        ok &= check("prefix-lookalike origin refused", s == 200 and acao is None)
        s, acao, _ = get(None)
        ok &= check("no Origin (curl, tests): served, no CORS header", s == 200 and acao is None)

        req = urllib.request.Request(f"http://127.0.0.1:{port}/status", method="OPTIONS",
                                     headers={"Origin": "https://evil.example"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok &= check("preflight from a foreign origin carries no Allow-Origin",
                        resp.status == 204 and resp.headers.get("Access-Control-Allow-Origin") is None)
        # negative control: the pre-2.1.1 header was a wildcard
        ok &= check("negative control: '*' is not what we send any more", acao != "*")
    finally:
        api.stop()

    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
