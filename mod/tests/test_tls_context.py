"""2.3.1: HTTPS verification trusts certifi's bundle as well as the Windows store.

    py mod/tests/test_tls_context.py          (any Python with certifi; the embedded one has it)
"""
# pyMHF imports every .py one level under mod/ INSIDE THE GAME. This file is a
# script: everything lives in main() so importing it does nothing at all.


def main():
    import ssl
    import sys
    from pathlib import Path
    MOD = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(MOD))
    from sync import tls

    def check(label, cond):
        print(("PASS " if cond else "FAIL ") + label)
        return cond

    ok = True
    ctx = tls.context()
    ok &= check("one context per process", ctx is tls.context())
    ok &= check("hostname checking and certificate verification stay on",
                ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED)
    subjects = set()
    for cert in ctx.get_ca_certs():
        for rdn in cert.get('subject', ()):
            for key, value in rdn:
                if key == 'commonName':
                    subjects.add(value)
    ok &= check(f"the trust store holds the current Let's Encrypt roots ({len(subjects)} CAs loaded)",
                {'ISRG Root X1', 'ISRG Root X2'} <= subjects)
    try:
        import certifi  # noqa: F401
        has_certifi = True
    except ImportError:
        has_certifi = False
    ok &= check("certifi is importable here (the embedded Python ships it)", has_certifi)

    class Err(Exception):
        def __init__(self, reason):
            super().__init__(reason)
            self.reason = reason

    msg = tls.explain(Err("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1006)"))
    ok &= check("an 'expired' failure tells the player to check the PC clock", "date or time" in msg and "certificate has expired" in msg)
    msg = tls.explain(Err("[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"))
    ok &= check("a missing-issuer failure points at HTTPS scanning / a proxy", "antivirus" in msg)
    ok &= check("other reasons pass through untouched", tls.explain(Err("timed out")) == "timed out")
    print()
    print("ALL PASS" if ok else "FAILURES PRESENT")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
