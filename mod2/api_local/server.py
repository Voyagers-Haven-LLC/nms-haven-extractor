"""Local API inside the extractor (EXTRACTOR_2_0.md D4-D5).

Serves the live plane to a browser on the same PC:
    GET  /status          — health/identity snapshot (also the probe target)
    GET  /current-system  — the live capture panel
    GET  /events          — SSE stream of the curated event feed
    POST /session         — receive a pairing token from the website page;
                            we redeem it against Haven with our key (or get
                            provisioned a key) and persist to the env file.

Hard rules (verified against pyMHF internals 2026-08-07):
  * Ports 6770 (pyMHF executor) and 9020 (log socket) are TAKEN. We bind
    8770, auto-incrementing through 8779.
  * The bind is DEFENSIVE — a failure logs and disables the API; it must
    never propagate (a bind error at module scope kills the whole mod:
    site-packages/pymhf/CRITICAL_ERROR.txt is a real crash artifact).
  * Handlers only read ExtractorState/EventBus snapshots — NEVER game
    memory — and stay short (one GIL shared with game-thread hooks).

CORS: permissive during dev; tighten allowed origins to havenmap.online at
release. Chrome 142+ gates public-site->localhost behind the Local Network
Access permission; the preflight response carries the PNA header.
"""

import json
import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger('haven_extractor.local_api')

PORT_RANGE = list(range(8770, 8780))


class LocalApi:
    def __init__(self, state, events, on_pair=None):
        """on_pair(token) -> dict — redeems the token upstream (sync client),
        persists a provisioned key, and returns the handshake result."""
        self.state = state
        self.events = events
        self.on_pair = on_pair
        self.port = None
        self._server = None

    def start(self, preferred_port: int = None):
        ports = list(PORT_RANGE)
        if preferred_port and preferred_port in ports:
            ports.remove(preferred_port)
            ports.insert(0, preferred_port)

        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            # ---- plumbing -------------------------------------------------
            def log_message(self, fmt, *args):  # keep the game console silent
                pass

            def _cors(self):
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                # Chrome Local Network Access preflight
                self.send_header('Access-Control-Allow-Private-Network', 'true')

            def _json(self, data, status=200):
                body = json.dumps(data).encode('utf-8')
                self.send_response(status)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.send_header('Content-Length', '0')
                self.end_headers()

            # ---- routes ---------------------------------------------------
            def do_GET(self):
                path = self.path.split('?', 1)[0]
                try:
                    if path == '/status':
                        self._json(api.state.status_snapshot())
                    elif path == '/current-system':
                        self._json(api.state.current_snapshot())
                    elif path == '/events':
                        self._serve_sse()
                    else:
                        self._json({'detail': 'not found'}, 404)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass
                except Exception as e:
                    logger.warning(f"local api GET {path} failed: {e}")
                    try:
                        self._json({'detail': 'internal error'}, 500)
                    except Exception:
                        pass

            def do_POST(self):
                path = self.path.split('?', 1)[0]
                try:
                    length = int(self.headers.get('Content-Length') or 0)
                    raw = self.rfile.read(length) if length else b'{}'
                    try:
                        body = json.loads(raw.decode('utf-8') or '{}')
                    except json.JSONDecodeError:
                        return self._json({'detail': 'invalid JSON'}, 400)

                    if path == '/session':
                        token = (body.get('token') or '').strip()
                        if not token:
                            return self._json({'detail': 'token required'}, 400)
                        if not api.on_pair:
                            return self._json({'detail': 'pairing not available'}, 503)
                        try:
                            result = api.on_pair(token)
                            return self._json(result)
                        except Exception as e:
                            detail = getattr(e, 'detail', None) or str(e)
                            status = getattr(e, 'status', None) or 502
                            return self._json({'detail': detail}, status)
                    return self._json({'detail': 'not found'}, 404)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass
                except Exception as e:
                    logger.warning(f"local api POST {path} failed: {e}")
                    try:
                        self._json({'detail': 'internal error'}, 500)
                    except Exception:
                        pass

            # ---- SSE ------------------------------------------------------
            def _serve_sse(self):
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'keep-alive')
                self.end_headers()

                q = api.events.subscribe()
                try:
                    # Replay recent history so a fresh page isn't blank.
                    for event in api.events.recent(100):
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode('utf-8'))
                    self.wfile.flush()
                    while True:
                        try:
                            event = q.get(timeout=15)
                            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode('utf-8'))
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
                    pass
                finally:
                    api.events.unsubscribe(q)

        for port in ports:
            try:
                self._server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
                self._server.daemon_threads = True
                self.port = port
                break
            except OSError as e:
                logger.warning(f"local api port {port} unavailable: {e}")
                continue

        if not self._server:
            logger.error("local api: no port available in 8770-8779 — web console disabled")
            self.events.emit('HEALTH', 'Local API could not start (ports 8770-8779 busy) — '
                                       'the website terminal/current-system pages will be offline.',
                             level='error')
            return None

        threading.Thread(target=self._server.serve_forever,
                         name='HavenLocalApi', daemon=True).start()
        logger.info(f"local api listening on 127.0.0.1:{self.port}")
        return self.port

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
