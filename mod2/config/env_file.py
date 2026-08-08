"""Root-level env file for Extractor 2.0 (EXTRACTOR_2_0.md D9).

Lives NEXT TO the mod folder (the updater only ever replaces the mod folder,
so this file survives every update untouched). Replaces the old layered
haven_config.json / Documents config.json — and with it the first-hit-wins
loading footgun.

Format: plain KEY=VALUE lines, '#' comments. Keys:
    HAVEN_API_URL   (default https://havenmap.online)
    HAVEN_API_KEY   (vh_live_... — provisioned by the pairing handshake)
    HAVEN_LOCAL_PORT (optional; default 8770, auto-increments to 8779)
"""

import os
import tempfile

DEFAULTS = {
    'HAVEN_API_URL': 'https://havenmap.online',
    'HAVEN_API_KEY': '',
    'HAVEN_LOCAL_PORT': '8770',
}


def load_env(path: str) -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key, value = key.strip(), value.strip()
                if key:
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return cfg


def save_env(path: str, cfg: dict) -> bool:
    """Atomic-ish write: temp file in the same directory, then replace."""
    lines = [
        "# Haven Extractor configuration — survives updates (do not move into mod/)",
        "# The API key is YOUR credential; regenerate any time by re-linking on havenmap.online",
    ]
    merged = dict(DEFAULTS)
    merged.update({k: str(v) for k, v in cfg.items() if v is not None})
    for key, value in merged.items():
        lines.append(f"{key}={value}")
    data = "\n".join(lines) + "\n"
    try:
        d = os.path.dirname(os.path.abspath(path)) or '.'
        fd, tmp = tempfile.mkstemp(prefix='.haven_env_', dir=d)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False
