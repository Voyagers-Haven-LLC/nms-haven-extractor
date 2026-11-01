"""2.3.1: the TLS context every HTTPS call the mod and launcher make goes through.

Python on Windows verifies servers against the Windows certificate store only. A PC whose
store never picked up the current Let's Encrypt root (automatic root updates off, a
"debloated" Windows, a machine that has not seen a modern site in Edge) still validates
havenmap.online through the OLD cross-signed path, whose root expired - and reports
"[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired" (hypersky75, 2026-10-31) even
though the site's certificate is current. The embedded Python ships certifi, a current
root bundle; it was never used. The context here trusts BOTH: the Windows store and
certifi's bundle, so a stale store cannot break the link.

The second thing that produces the same message is a wrong PC clock; explain() says so.
"""
import ssl

_CONTEXT = None


def context() -> ssl.SSLContext:
    """The default context (system store, hostname check, modern protocols) plus certifi's
    bundle. Built once per process. Never raises: without certifi it is the plain default."""
    global _CONTEXT
    if _CONTEXT is None:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(cafile=certifi.where())
        except Exception:
            pass
        _CONTEXT = ctx
    return _CONTEXT


def explain(err) -> str:
    """A player-readable reason for a failed HTTPS call, with the fix when it is a known one."""
    text = str(getattr(err, 'reason', None) or err)
    low = text.lower()
    if 'certificate has expired' in low or 'not yet valid' in low:
        return (f"{text} - the site's certificate is current, so this PC's date or time is "
                f"probably wrong (check the Windows clock, including the year) or Windows is "
                f"missing a root certificate; this version carries its own, so try again after updating")
    if 'unable to get local issuer' in low or 'self signed certificate' in low:
        return (f"{text} - something between this PC and havenmap.online is rewriting the "
                f"connection (antivirus HTTPS scanning, a proxy); exclude the extractor from it")
    return text
