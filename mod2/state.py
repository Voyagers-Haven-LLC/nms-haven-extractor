"""Shared runtime state for Extractor 2.0.

One thread-safe snapshot object: the capture layer writes, the local API and
sync layer read. Everything served to the browser comes from snapshots of
this — the HTTP threads never touch game memory (EXTRACTOR_2_0.md D4).

Includes the readiness gate (D16): `ready` is computed, never asserted — the
mod may not claim Ready unless every required hook bound, all expected mods
loaded, and deps are present. (June 16 2026: the mod said "ready" with its
warp hook dead. That must be impossible here.)
"""

import threading
from datetime import datetime, timezone

VERSION = "2.0.0-dev"


class ExtractorState:
    def __init__(self):
        self._lock = threading.Lock()
        self.version = VERSION
        # Health (populated by capture/ at load; by the simulator in dev)
        self.mods_expected = 1
        self.mods_loaded = 0
        self.hooks_expected = 4
        self.hooks_bound = 0
        self.deps_ok = True
        self.deps_detail = {}
        self.game_running = False
        # Identity
        self.username = None
        self.linked = False
        # Live capture
        self.current_system = None      # dict of coords+props, or None
        self.current_planets = []       # list of captured-planet dicts
        self.capturing = False
        self.current_staged = False
        # Batch / sync
        self.batch_count = 0
        self.last_capture_at = None
        self.last_sync_at = None

    # -- writers ------------------------------------------------------------

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def begin_system(self, system: dict):
        with self._lock:
            self.current_system = dict(system)
            self.current_planets = []
            self.capturing = True
            self.current_staged = False

    def add_planet(self, planet: dict):
        with self._lock:
            self.current_planets.append(dict(planet))
            self.last_capture_at = datetime.now(timezone.utc).isoformat()

    def finish_system(self, staged: bool):
        with self._lock:
            self.capturing = False
            self.current_staged = staged

    # -- readers ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """The readiness gate. Computed, never asserted."""
        with self._lock:
            return (self.mods_loaded >= self.mods_expected
                    and self.hooks_bound >= self.hooks_expected
                    and self.hooks_expected > 0
                    and self.deps_ok)

    def status_snapshot(self) -> dict:
        ready = self.ready  # takes the lock itself
        with self._lock:
            return {
                'app': 'haven-extractor',
                'version': self.version,
                'ready': ready,
                'mods_loaded': self.mods_loaded,
                'mods_expected': self.mods_expected,
                'hooks_bound': self.hooks_bound,
                'hooks_expected': self.hooks_expected,
                'deps_ok': self.deps_ok,
                'deps_detail': dict(self.deps_detail),
                'game_running': self.game_running,
                'identity': {'username': self.username, 'linked': self.linked},
                'batch_count': self.batch_count,
                'last_capture_at': self.last_capture_at,
                'last_sync_at': self.last_sync_at,
            }

    def current_snapshot(self) -> dict:
        with self._lock:
            return {
                'capturing': self.capturing,
                'staged': self.current_staged,
                'system': dict(self.current_system) if self.current_system else None,
                'planets': [dict(p) for p in self.current_planets],
            }
