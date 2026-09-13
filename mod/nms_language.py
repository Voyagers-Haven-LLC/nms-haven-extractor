"""
nms_language.py - Parse NMS language data from game PAK/MBIN files

Extracts text ID -> display string mappings from NMS language MBIN files
stored inside HGPAK (.pak) archives. Used as a disk-based fallback for
adjective resolution when the in-memory Translate hook hasn't cached a value.

Requires: hgpaktool (pip install hgpaktool)

Usage:
    builder = AdjectiveCacheBuilder(Path.home() / "Documents" / "Haven-Extractor")
    mappings = builder.build_cache()
    # mappings = {"RARITY_HIGH3": "Ample", "WEATHER_COLD7": "Ice Storms", ...}

v1.4.0 - Initial implementation (PSARC reader)
v1.4.1 - Switched to HGPAKtool for HGPAK format support (Worlds Part II+)
"""

import struct
import json
import re
import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Try importing hgpaktool - auto-install if missing, gracefully degrade if that fails
try:
    from hgpaktool import HGPAKFile
    HAS_HGPAKTOOL = True
except ImportError:
    HAS_HGPAKTOOL = False
    logger.info("[LANG] hgpaktool not found - attempting auto-install...")
    try:
        import subprocess
        # sys.executable is NMS.exe inside pyMHF — find embedded Python relative to this file
        # nms_language.py is in mod/, python.exe is in python/ (sibling folder)
        _embedded_python = Path(__file__).resolve().parent.parent / "python" / "python.exe"
        if not _embedded_python.exists():
            raise FileNotFoundError(f"Embedded Python not found at {_embedded_python}")
        subprocess.check_call(
            [str(_embedded_python), '-m', 'pip', 'install', 'hgpaktool'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120
        )
        from hgpaktool import HGPAKFile
        HAS_HGPAKTOOL = True
        logger.info("[LANG] hgpaktool auto-installed successfully")
    except Exception as e:
        logger.warning(f"[LANG] hgpaktool auto-install failed: {e}")
        logger.warning("[LANG] PAK parsing unavailable - run: pip install hgpaktool")


# =========================================================================
# Language MBIN Parser
# Parses NMS language MBIN files to extract text ID -> string mappings
# =========================================================================

MBIN_MAGIC = 0xCCCCCCCC
MBIN_PC_MAGIC = 0xDDDDDDDD

# cTkLocalisationEntry layout (from exported_types.py):
# Id:              cTkFixedString0x20  at offset 0x00 (32 bytes)
# BrazilianPort:   VariableSizeString  at offset 0x20 (16 bytes: ptr8 + count4 + pad4)
# Dutch:           VariableSizeString  at offset 0x30
# English:         VariableSizeString  at offset 0x40
# ... (17 languages total)
# USEnglish:       VariableSizeString  at offset 0x120
# Total entry size: 0x130 (304 bytes)

ENTRY_SIZE = 0x130
ID_OFFSET = 0x00
ID_SIZE = 0x20  # cTkFixedString0x20
ENGLISH_OFFSET = 0x40  # VariableSizeString for English

# Adjective text ID prefixes we care about
ADJECTIVE_PREFIXES = (
    'RARITY_', 'SENTINEL_', 'WEATHER_',
    'UI_BIOME_', 'BIOME_', 'UI_PLANET_',
    'UI_SENTINEL_', 'UI_WEATHER_', 'UI_FLORA_',
    'UI_FAUNA_', 'UI_RARITY_',
)


class LanguageMBINParser:
    """Parse NMS language MBIN files to extract text ID -> English string mappings.

    NMS language MBINs contain cTkLocalisationTable with a cTkDynamicArray
    of cTkLocalisationEntry items. Each entry has a 32-byte text ID and
    VariableSizeString fields for each language.

    VariableSizeString in MBIN files stores strings as relative offsets
    (not absolute pointers like in memory). The layout is:
    - 8 bytes: relative offset to string data (from field position)
    - 4 bytes: string length
    - 4 bytes: padding/flags
    """

    @staticmethod
    def parse(data: bytes, filter_prefixes: tuple = ADJECTIVE_PREFIXES) -> Dict[str, str]:
        """Parse a language MBIN and return {text_id: english_text} dict."""
        result = {}

        if len(data) < 0x60:
            return result

        # Verify MBIN magic
        magic = struct.unpack_from('<I', data, 0)[0]
        if magic not in (MBIN_MAGIC, MBIN_PC_MAGIC):
            logger.debug(f"Invalid MBIN magic: 0x{magic:08X}")
            return result

        # Data section starts after 96-byte header
        data_start = 0x60

        # Try structured parsing first
        result = LanguageMBINParser._parse_structured(data, data_start, filter_prefixes)

        # If structured parsing found nothing, try pattern scanning as fallback
        if not result:
            result = LanguageMBINParser._scan_for_ids(data, filter_prefixes)

        return result

    @staticmethod
    def _parse_structured(data: bytes, data_start: int, filter_prefixes: tuple) -> Dict[str, str]:
        """Try to parse using the known cTkLocalisationTable structure.

        cTkDynamicArray header at data_start:
        - 8 bytes: relative pointer to array data (offset from this field)
        - 4 bytes: array count
        - 4 bytes: padding
        """
        result = {}

        if len(data) < data_start + 16:
            return result

        # Read cTkDynamicArray header
        array_rel_offset = struct.unpack_from('<q', data, data_start)[0]  # signed 64-bit
        array_count = struct.unpack_from('<I', data, data_start + 8)[0]

        # Resolve the relative offset to absolute position in file
        array_start = data_start + array_rel_offset

        # Sanity check
        if array_count == 0 or array_count > 100000 or array_start < 0 or array_start >= len(data):
            logger.debug(f"Structured parse: invalid array (count={array_count}, start=0x{array_start:X})")
            return result

        logger.debug(f"Structured parse: {array_count} entries starting at 0x{array_start:X}")

        for i in range(array_count):
            entry_offset = array_start + i * ENTRY_SIZE
            if entry_offset + ENTRY_SIZE > len(data):
                break

            # Read text ID (32-byte null-terminated string)
            id_bytes = data[entry_offset + ID_OFFSET:entry_offset + ID_OFFSET + ID_SIZE]
            null_pos = id_bytes.find(b'\x00')
            if null_pos >= 0:
                id_bytes = id_bytes[:null_pos]
            text_id = id_bytes.decode('ascii', errors='ignore').strip()

            if not text_id:
                continue

            # Filter by prefix
            if filter_prefixes and not any(text_id.startswith(p) for p in filter_prefixes):
                continue

            # Read English VariableSizeString at offset 0x40 within the entry
            eng_field_offset = entry_offset + ENGLISH_OFFSET

            if eng_field_offset + 16 > len(data):
                continue

            # VariableSizeString: relative offset (8 bytes) + length (4 bytes) + pad (4 bytes)
            str_rel_offset = struct.unpack_from('<q', data, eng_field_offset)[0]
            str_length = struct.unpack_from('<I', data, eng_field_offset + 8)[0]

            if str_length == 0 or str_length > 1024:
                continue

            # Resolve relative offset (from the field position)
            str_abs_offset = eng_field_offset + str_rel_offset

            if str_abs_offset < 0 or str_abs_offset + str_length > len(data):
                continue

            try:
                english_text = data[str_abs_offset:str_abs_offset + str_length].decode('utf-8', errors='ignore')
                # Strip null terminators
                english_text = english_text.rstrip('\x00').strip()
                if english_text and len(english_text) >= 1:
                    result[text_id] = english_text
            except Exception:
                continue

        return result

    @staticmethod
    def _scan_for_ids(data: bytes, filter_prefixes: tuple) -> Dict[str, str]:
        """Fallback: scan for known text ID patterns in raw binary data.

        Less reliable than structured parsing but works as a safety net.
        Looks for ASCII strings matching our prefixes and tries to find
        associated English text nearby.
        """
        result = {}

        for prefix in filter_prefixes:
            prefix_bytes = prefix.encode('ascii')
            pos = 0
            while True:
                idx = data.find(prefix_bytes, pos)
                if idx < 0:
                    break

                # Extract text ID (null-terminated, max 32 bytes)
                end = data.find(b'\x00', idx, idx + 32)
                if end < 0:
                    end = idx + 32
                text_id = data[idx:end].decode('ascii', errors='ignore').strip()

                if text_id and len(text_id) >= 4:
                    # Try to find English text at the known offset (0x40 from entry start)
                    # The entry start should be aligned to ENTRY_SIZE boundaries
                    # relative to the array start, but we don't know the array start
                    # Try reading at offset 0x40 from the ID position
                    eng_offset = idx + ENGLISH_OFFSET
                    if eng_offset + 16 < len(data):
                        str_rel = struct.unpack_from('<q', data, eng_offset)[0]
                        str_len = struct.unpack_from('<I', data, eng_offset + 8)[0]
                        str_abs = eng_offset + str_rel

                        if 0 < str_len < 256 and 0 <= str_abs < len(data) - str_len:
                            try:
                                candidate = data[str_abs:str_abs + str_len].decode('utf-8', errors='ignore').rstrip('\x00').strip()
                                if (candidate and len(candidate) >= 2 and
                                    all(c.isprintable() for c in candidate) and
                                    not any(candidate.startswith(p) for p in filter_prefixes)):
                                    result[text_id] = candidate
                            except Exception:
                                pass

                pos = idx + len(prefix_bytes)

        return result


# =========================================================================
# NMS Installation Auto-Detection
# =========================================================================

def find_nms_install_path() -> Optional[Path]:
    """Auto-detect NMS installation path by checking common locations and Steam config."""
    # Common direct paths
    candidates = [
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\No Man's Sky"),
        Path(r"C:\Program Files\Steam\steamapps\common\No Man's Sky"),
        Path(r"D:\Steam\steamapps\common\No Man's Sky"),
        Path(r"D:\SteamLibrary\steamapps\common\No Man's Sky"),
        Path(r"E:\Steam\steamapps\common\No Man's Sky"),
        Path(r"E:\SteamLibrary\steamapps\common\No Man's Sky"),
    ]

    # Try to find additional Steam library paths from libraryfolders.vdf
    steam_dirs = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]

    for steam_dir in steam_dirs:
        vdf_path = steam_dir / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            try:
                with open(vdf_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                paths = re.findall(r'"path"\s+"([^"]+)"', content)
                for p in paths:
                    candidate = Path(p.replace('\\\\', '\\')) / "steamapps" / "common" / "No Man's Sky"
                    if candidate not in candidates:
                        candidates.append(candidate)
            except Exception:
                pass

    for path in candidates:
        if (path / "GAMEDATA" / "PCBANKS").exists():
            return path

    return None


# =========================================================================
# Adjective Cache Builder
# Orchestrates PAK extraction + MBIN parsing + cache management
# =========================================================================

class AdjectiveCacheBuilder:
    """Build and cache adjective mappings from NMS game files.

    Uses HGPAKtool to read NMS .pak archives (HGPAK format, Worlds Part II+)
    and the LanguageMBINParser to extract text ID -> English string mappings.
    """

    CACHE_FILENAME = "adjective_cache.json"
    CACHE_VERSION = 2  # Bumped from 1 to force rebuild with HGPAKtool

    def __init__(self, cache_dir: Path, nms_path: Path = None):
        self.cache_dir = cache_dir
        self.nms_path = nms_path or find_nms_install_path()

    @property
    def cache_path(self) -> Path:
        return self.cache_dir / self.CACHE_FILENAME

    @property
    def pcbanks_path(self) -> Optional[Path]:
        if self.nms_path:
            return self.nms_path / "GAMEDATA" / "PCBANKS"
        return None

    def load_cache(self) -> Optional[Dict[str, str]]:
        """Load cached mappings if valid and not stale."""
        if not self.cache_path.exists():
            return None

        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if data.get('version') != self.CACHE_VERSION:
                logger.info("[CACHE] Cache version mismatch, needs rebuild")
                return None

            mappings = data.get('mappings', {})
            if not mappings:
                return None

            # Check if game PAKs have been updated since cache was built
            cached_timestamp = data.get('pak_timestamp', 0)
            current_timestamp = self._get_pak_timestamp()
            if current_timestamp and current_timestamp > cached_timestamp:
                logger.info("[CACHE] Game files updated since cache build, needs rebuild")
                return None

            return mappings

        except Exception as e:
            logger.debug(f"[CACHE] Failed to load cache: {e}")
            return None

    def build_cache(self) -> Dict[str, str]:
        """Build adjective mappings by parsing game PAK files using HGPAKtool."""
        if not HAS_HGPAKTOOL:
            logger.warning("[CACHE] hgpaktool not installed - run: pip install hgpaktool")
            return {}

        if not self.pcbanks_path or not self.pcbanks_path.exists():
            logger.warning("[CACHE] NMS PCBANKS path not found")
            return {}

        all_mappings = {}
        pak_files = sorted(self.pcbanks_path.glob("*.pak"))

        logger.info(f"[CACHE] Scanning {len(pak_files)} PAK files for language MBINs...")

        # Filter pattern: all English language MBIN files
        lang_filter = "*ENGLISH*.MBIN"

        for pak_path in pak_files:
            try:
                with HGPAKFile(str(pak_path)) as hgpak:
                    for filename, data in hgpak.extract(filters=[lang_filter]):
                        # Skip US English variants (near-identical to English)
                        if "usenglish" in filename.lower():
                            continue

                        mappings = LanguageMBINParser.parse(data)
                        if mappings:
                            all_mappings.update(mappings)
                            logger.info(f"[CACHE] {filename}: {len(mappings)} adjective entries")

            except Exception as e:
                logger.debug(f"[CACHE] Error scanning {pak_path.name}: {e}")

        # Save cache
        if all_mappings:
            self._save_cache(all_mappings)

        logger.info(f"[CACHE] Built adjective cache: {len(all_mappings)} total entries")
        return all_mappings

    def _save_cache(self, mappings: Dict[str, str]):
        """Save mappings to JSON cache file."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

            cache_data = {
                'version': self.CACHE_VERSION,
                'build_time': datetime.now().isoformat(),
                'game_path': str(self.nms_path) if self.nms_path else None,
                'pak_timestamp': self._get_pak_timestamp() or 0,
                'entry_count': len(mappings),
                'mappings': mappings,
            }

            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)

            logger.info(f"[CACHE] Saved {len(mappings)} entries to {self.cache_path}")

        except Exception as e:
            logger.warning(f"[CACHE] Failed to save cache: {e}")

    def _get_pak_timestamp(self) -> Optional[float]:
        """Get the latest modification time of any PAK file."""
        if not self.pcbanks_path or not self.pcbanks_path.exists():
            return None

        try:
            pak_files = list(self.pcbanks_path.glob("*.pak"))
            if pak_files:
                return max(f.stat().st_mtime for f in pak_files)
        except Exception:
            pass
        return None
