"""Haven sync client for Extractor 2.0 (EXTRACTOR_2_0.md §4-5).

The machine leg: stage captures, heartbeat, poll/ack commands, redeem
pairing tokens. Stdlib-only (urllib) so the embedded Python needs nothing
new — and TLS verification is ON (fixing the 1.x check_hostname=False
ngrok leftover on every endpoint).
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger('haven_extractor.sync')

USER_AGENT_VERSION = '2.1.0-dev'  # stamped by build_release.py alongside __version__


class SyncError(Exception):
    def __init__(self, message, status=None, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class HavenSyncClient:
    def __init__(self, api_url: str, api_key: str = ''):
        self.api_url = (api_url or '').rstrip('/')
        self.api_key = api_key or ''

    # -- transport ----------------------------------------------------------

    def _call(self, method: str, path: str, body: dict = None,
              timeout: float = 15.0, use_key: bool = True) -> dict:
        url = f"{self.api_url}{path}"
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', f'HavenExtractor/{USER_AGENT_VERSION}')
        if use_key and self.api_key:
            req.add_header('X-API-Key', self.api_key)
        try:
            # No custom SSL context: certifi-backed default verification.
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8') or '{}')
        except urllib.error.HTTPError as e:
            detail = None
            try:
                detail = json.loads(e.read().decode('utf-8')).get('detail')
            except Exception:
                pass
            raise SyncError(detail or f"HTTP {e.code}", status=e.code, detail=detail)
        except urllib.error.URLError as e:
            raise SyncError(f"Cannot reach Haven: {e.reason}")
        except Exception as e:
            raise SyncError(str(e))

    # -- machine-leg endpoints ----------------------------------------------

    def handshake(self, token: str) -> dict:
        """Redeem a pairing token. On a fresh install (no key) the server
        provisions one — we adopt it immediately."""
        result = self._call('POST', '/api/extractor/handshake', {'token': token},
                            use_key=bool(self.api_key))
        if result.get('key'):
            self.api_key = result['key']
        return result

    def stage(self, payload: dict, timeout: float = 20.0) -> dict:
        return self._call('POST', '/api/extractor/stage', payload, timeout=timeout)

    def heartbeat(self, health: dict) -> dict:
        return self._call('POST', '/api/extractor/heartbeat', health, timeout=10.0)

    def poll_commands(self) -> list:
        return self._call('GET', '/api/extractor/commands', timeout=10.0).get('commands', [])

    def ack_commands(self, ids: list) -> dict:
        return self._call('POST', '/api/extractor/commands/ack', {'ids': ids}, timeout=10.0)
