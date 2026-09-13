"""AUTO-TRANSPLANTED from mod/haven_extractor.py v1.10.6 per the verified
live-code manifest (haven-ui/docs/EXTRACTOR_2_0.md §7). Source line ranges
noted per block. Bodies are verbatim; [2.0] marks the few deliberate edits.
"""

import json
import logging
import time
import ctypes
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Set, List, Dict
from ctypes import c_uint64, c_int64, c_void_p, pointer, sizeof

from pymhf.core.memutils import map_struct, get_addressof
import pymhf.core._internal as _internal
import nmspy.data.types as nms
import nmspy.data.exported_types as nmse
from nmspy.common import gameData
import nmspy.data.basic_types as basic

logger = logging.getLogger("haven_extractor2")


class MemoryMixin:
    """Raw memory read primitives (effective definitions only — the shadowed 1.x copies are not carried)."""

    # ---- source lines 1394-1404: _read_int32 ----
    def _read_int32(self, base_addr: int, offset: int) -> int:
        """Read a 32-bit integer from memory at base + offset."""
        try:
            addr = base_addr + offset
            # Use ctypes to read from process memory
            value = ctypes.c_int32()
            ctypes.memmove(ctypes.addressof(value), addr, 4)
            return value.value
        except Exception as e:
            logger.debug(f"Failed to read int32 at 0x{base_addr:X}+0x{offset:X}: {e}")
            return 0

    # ---- source lines 2456-2495: _read_bytes/_read_uint64/_read_uint32/_read_string ----
    def _read_bytes(self, base_addr: int, offset: int, size: int) -> bytes:
        """Read raw bytes from memory."""
        try:
            import ctypes
            addr = base_addr + offset
            buffer = (ctypes.c_char * size)()
            ctypes.memmove(buffer, addr, size)
            return bytes(buffer)
        except Exception:
            return None

    def _read_uint64(self, base_addr: int, offset: int) -> int:
        """Read uint64 from memory."""
        try:
            import ctypes
            addr = base_addr + offset
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint64)).contents.value
        except Exception:
            return 0

    def _read_uint32(self, base_addr: int, offset: int) -> int:
        """Read uint32 from memory."""
        try:
            import ctypes
            addr = base_addr + offset
            return ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint32)).contents.value
        except Exception:
            return 0

    def _read_string(self, base_addr: int, offset: int, max_len: int = 128) -> str:
        """Read null-terminated string from memory."""
        try:
            raw = self._read_bytes(base_addr, offset, max_len)
            if raw:
                null_pos = raw.find(b'\x00')
                if null_pos > 0:
                    return raw[:null_pos].decode('utf-8', errors='ignore')
            return ""
        except Exception:
            return ""

    # ---- source lines 3984-3998: _safe_enum (effective def) ----
    def _safe_enum(self, val, default: str = "Unknown") -> str:
        """Safely convert enum to string, with normalization."""
        try:
            if val is None:
                return default
            if hasattr(val, 'name'):
                name = val.name
            elif hasattr(val, 'value'):
                name = str(val.value)
            else:
                name = str(val)
            # Normalize: strip trailing underscores (None_ -> None)
            return name.rstrip('_') if name else default
        except Exception:
            return default

    # ---- source lines 4052-4061: _safe_int ----
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
