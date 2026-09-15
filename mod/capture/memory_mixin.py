"""Value coercion for typed struct reads.

This file used to be the mod's raw-memory layer: read a pointer at an address,
step a fixed byte stride, pull a string out of the result. 2.1.1 removed the last
two callers of that machinery — the hand-rolled walk over
``cGcPlanetData.ExtraResourceHints`` and a 0x38 offset into game_state that was
scraping the system name out of a notification string. Both were replaced by
reads through NMSpy's generated structs, which is the whole point of the 2.1.0
rebase: a struct that moves produces a loud failure instead of plausible garbage.
``tests/test_dynamic_array.py`` is the proof for the hints array.

Removed with them: ``_read_int32`` / ``_read_bytes`` / ``_read_uint64`` /
``_read_uint32`` / ``_read_string`` (no callers left) and ``_safe_enum``, which
only served 1.x fallbacks that wrote raw enum names into fields whose vocabulary
is the option catalog.

What remains is one coercion helper. It is NOT a memory read — it takes a value
you already hold and returns a plain int without letting an exception escape.
Typed integer fields already come back as plain ints, so its remaining value is
purely the exception guard around the galaxy/coordinate resolution path, where a
single bad read must fall through to the next candidate rather than kill the
capture. That path is deliberately left alone; retiring this last helper means
touching the galaxy voter, which earns its own pass.
"""

import logging

logger = logging.getLogger("haven_extractor2")


class MemoryMixin:
    """The one surviving value-coercion helper (see the module docstring)."""

    def _safe_int(self, val, default: int = 0) -> int:
        """Safely convert value to int."""
        try:
            if val is None:
                return default
            if hasattr(val, 'value'):
                return int(val.value)
            return int(val)
        except Exception:
            return default
