"""Event bus + curated feed for Extractor 2.0 (EXTRACTOR_2_0.md §6).

State-vs-events: hot repeating data belongs in ExtractorState (served as
panels that update in place); this bus carries one event per CHANGE.
Categories: SESSION, CAPTURE, SYNC, ACCOUNT, HEALTH, UPDATE.

Thread-safe: hooks emit from game threads, the local API reads from its own
threads. Identical consecutive errors coalesce into one event with a counter
(the error-storm fix — a broken hook fires ~60/sec).
"""

import queue
import threading
from collections import deque
from datetime import datetime, timezone

CATEGORIES = ('SESSION', 'CAPTURE', 'SYNC', 'ACCOUNT', 'HEALTH', 'UPDATE')


class EventBus:
    def __init__(self, maxlen: int = 2000):
        self._events = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._subscribers = []          # list[queue.Queue]
        self._last_key = None           # coalescing state
        self._last_event = None

    def emit(self, category: str, message: str, level: str = 'info',
             detail: dict = None, coalesce_key: str = None) -> dict:
        """Append an event. If coalesce_key matches the previous event's key,
        bump its counter instead of appending a new line."""
        category = category.upper()
        if category not in CATEGORIES:
            category = 'SESSION'
        with self._lock:
            if coalesce_key and coalesce_key == self._last_key and self._last_event:
                self._last_event['count'] = self._last_event.get('count', 1) + 1
                self._last_event['ts'] = datetime.now(timezone.utc).isoformat()
                event = dict(self._last_event)
            else:
                event = {
                    'ts': datetime.now(timezone.utc).isoformat(),
                    'category': category,
                    'level': level,
                    'message': message,
                    'count': 1,
                }
                if detail:
                    event['detail'] = detail
                self._events.append(event)
                self._last_event = event
                self._last_key = coalesce_key
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(dict(event))
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)
        return event

    def recent(self, n: int = 200):
        with self._lock:
            return list(self._events)[-n:]

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
