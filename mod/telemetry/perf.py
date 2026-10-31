"""2.3.0: how much game-thread time the mod costs, written to the log once a minute.

    [PERF] 60s | creature_roles 412 calls 38ms (max 1.2ms) | publish 9 calls 21ms ... |
           live_translate 31 calls 4ms | threads 7

The 2.1.x freeze reports (kingager, 2026-10-20) could not be diagnosed from a player's
log because nothing in it said how often our hooks ran or how long they took. Every
decorated hook in haven_extractor2.py records its wall time here; the live Translate
call records every time the mod asks the game for a word. Counters only - no game
memory is touched from the reporter thread. A single step over 100 ms is logged on
its own line as it happens, so a stall can be matched to what the player was doing.
"""

import logging
import threading
import time

logger = logging.getLogger("haven_extractor2")

INTERVAL = 60.0
SLOW = 0.100

_lock = threading.Lock()
_stats = {}          # name -> [calls, total_seconds, max_seconds]


def record(name: str, seconds: float) -> None:
    with _lock:
        s = _stats.get(name)
        if s is None:
            _stats[name] = [1, seconds, seconds]
        else:
            s[0] += 1
            s[1] += seconds
            if seconds > s[2]:
                s[2] = seconds
    if seconds > SLOW:
        logger.warning(f"[PERF] SLOW {name} {seconds * 1000:.0f}ms on thread {threading.current_thread().name}")


def snapshot(reset: bool = True) -> dict:
    with _lock:
        out = {k: list(v) for k, v in _stats.items()}
        if reset:
            _stats.clear()
    return out


def summary_line(stats: dict, seconds: float) -> str:
    parts = []
    for name, (calls, total, mx) in sorted(stats.items(), key=lambda kv: -kv[1][1]):
        parts.append(f"{name} {calls} calls {total * 1000:.0f}ms (max {mx * 1000:.1f}ms)")
    body = " | ".join(parts) if parts else "no hook activity"
    return f"[PERF] {seconds:.0f}s | {body} | threads {threading.active_count()}"


def start_reporter(stop: threading.Event, interval: float = INTERVAL) -> threading.Thread:
    """One daemon thread per mod instance; it ends when the instance's stop event is set."""
    def run():
        while not stop.wait(interval):
            try:
                logger.info(summary_line(snapshot(), interval))
            except Exception:
                pass
    t = threading.Thread(target=run, name="HavenPerf", daemon=True)
    t.start()
    return t
