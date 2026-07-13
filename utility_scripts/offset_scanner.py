"""
Memory Offset Scanner for NMS Haven Extractor

This tool scans memory around known struct addresses to help identify
where specific values are located. Use this to:

1. Verify offsets on Fractal413 (NMS 4.13) debug version
2. Find shifted offsets on current live NMS version
3. Discover new fields not yet mapped

The scanner looks for known patterns (enum values, string patterns)
and reports their offsets relative to the struct base address.

Usage as pyMHF mod:
1. Load this mod with NMS
2. Enter a solar system
3. Check the logs/output files for offset analysis
"""

import json
import logging
import ctypes
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any

from pymhf import Mod
from pymhf.core.memutils import map_struct, get_addressof
import nmspy.data.types as nms
from nmspy.decorators import on_state_change
from nmspy.common import gameData

logger = logging.getLogger(__name__)

# Known enum values to search for
KNOWN_BIOMES = {0, 1, 2, 3, 4, 5, 6, 7, 12, 13}  # Common biome values
KNOWN_SIZES = {0, 1, 2, 3, 4}  # Large, Medium, Small, Moon, Giant
KNOWN_TRADING = {0, 1, 2, 3, 4, 5, 6}  # Economy types
KNOWN_WEALTH = {0, 1, 2, 3}  # Poor, Average, Wealthy, Pirate
KNOWN_CONFLICT = {0, 1, 2, 3}  # Low, Default, High, Pirate
KNOWN_RACES = {0, 1, 2, 3, 4, 5, 6}  # Gek, Vykeen, Korvax, etc.
KNOWN_STARS = {0, 1, 2, 3}  # Yellow, Red, Green, Blue

# Common resource IDs (partial strings to search)
KNOWN_RESOURCES = [
    b"FUEL", b"LAND", b"OXYGEN", b"CARBON", b"FERRITE", b"SODIUM",
    b"COBALT", b"COPPER", b"SILVER", b"GOLD", b"PLATINUM", b"EMERIL",
    b"CADMIUM", b"INDIUM", b"ACTIVATED", b"ASTEROID", b"YELLOW",
]


class OffsetScannerMod(Mod):
    __author__ = "Voyagers Haven"
    __version__ = "1.1.0"
    __description__ = "Memory offset scanner for reverse engineering NMS structs"

    def __init__(self):
        super().__init__()
        self._output_dir = Path.home() / "Documents" / "Haven-Extractor" / "offset_scans"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._scanned = False
        self._cached_solar_system = None  # Cache from Generate hook

        logger.info("=" * 60)
        logger.info("OFFSET SCANNER MOD v1.1.0 - For Reverse Engineering NMS Structs")
        logger.info(f"Output: {self._output_dir}")
        logger.info("Enter a solar system to begin scanning...")
        logger.info("=" * 60)

    @nms.cGcSolarSystem.Generate.after
    def on_system_generate(self, this, lbUseSettingsFile, lSeed):
        """Capture solar system pointer from Generate hook - same as extractor."""
        logger.info("=" * 40)
        logger.info("OFFSET SCANNER: System Generate detected")
        logger.info(f"  this pointer: {this}")
        logger.info("=" * 40)

        try:
            addr = get_addressof(this)
            self._cached_solar_system = map_struct(addr, nms.cGcSolarSystem)
            logger.info(f"  Cached solar system @ 0x{addr:X}")
        except Exception as e:
            logger.error(f"  Failed to cache solar system: {e}")

    def _read_bytes(self, addr: int, size: int) -> bytes:
        """Read raw bytes from memory."""
        try:
            buffer = ctypes.create_string_buffer(size)
            ctypes.memmove(buffer, addr, size)
            return buffer.raw
        except:
            return b'\x00' * size

    def _read_uint32(self, addr: int) -> int:
        """Read 32-bit unsigned int."""
        try:
            data = self._read_bytes(addr, 4)
            return struct.unpack('<I', data)[0]
        except:
            return 0xDEADBEEF

    def _read_int32(self, addr: int) -> int:
        """Read 32-bit signed int."""
        try:
            data = self._read_bytes(addr, 4)
            return struct.unpack('<i', data)[0]
        except:
            return -999999

    def _scan_for_enum_values(self, base_addr: int, scan_range: int,
                               valid_values: set, field_name: str) -> List[Dict]:
        """Scan memory range for valid enum values."""
        results = []
        for offset in range(0, scan_range, 4):  # Enums are typically 4-byte aligned
            val = self._read_uint32(base_addr + offset)
            if val in valid_values:
                results.append({
                    "field": field_name,
                    "offset": f"0x{offset:04X}",
                    "offset_dec": offset,
                    "value": val,
                    "address": f"0x{base_addr + offset:X}",
                })
        return results

    def _scan_for_strings(self, base_addr: int, scan_range: int,
                          patterns: List[bytes]) -> List[Dict]:
        """Scan for string patterns in memory."""
        results = []
        data = self._read_bytes(base_addr, scan_range)

        for pattern in patterns:
            idx = 0
            while True:
                pos = data.find(pattern, idx)
                if pos == -1:
                    break
                # Read the full string (up to 32 bytes)
                string_start = pos
                string_data = data[string_start:string_start + 32]
                null_pos = string_data.find(b'\x00')
                if null_pos > 0:
                    string_data = string_data[:null_pos]
                try:
                    string_val = string_data.decode('utf-8', errors='ignore')
                except:
                    string_val = "<decode error>"

                results.append({
                    "pattern": pattern.decode('utf-8', errors='ignore'),
                    "offset": f"0x{pos:04X}",
                    "offset_dec": pos,
                    "full_string": string_val,
                    "address": f"0x{base_addr + pos:X}",
                })
                idx = pos + 1

        return results

    def _scan_for_int_range(self, base_addr: int, scan_range: int,
                            min_val: int, max_val: int, field_name: str) -> List[Dict]:
        """Scan for integers within a specific range."""
        results = []
        for offset in range(0, scan_range, 4):
            val = self._read_int32(base_addr + offset)
            if min_val <= val <= max_val:
                results.append({
                    "field": field_name,
                    "offset": f"0x{offset:04X}",
                    "offset_dec": offset,
                    "value": val,
                    "address": f"0x{base_addr + offset:X}",
                })
        return results

    def _dump_memory_region(self, base_addr: int, size: int,
                            output_file: Path, label: str):
        """Dump a memory region to file for offline analysis."""
        data = self._read_bytes(base_addr, size)

        # Write raw binary
        bin_file = output_file.with_suffix('.bin')
        with open(bin_file, 'wb') as f:
            f.write(data)

        # Write hex dump
        hex_file = output_file.with_suffix('.hex')
        with open(hex_file, 'w') as f:
            f.write(f"Memory dump: {label}\n")
            f.write(f"Base address: 0x{base_addr:X}\n")
            f.write(f"Size: {size} bytes (0x{size:X})\n")
            f.write("=" * 80 + "\n\n")

            for offset in range(0, size, 16):
                # Hex values
                hex_str = ""
                ascii_str = ""
                for i in range(16):
                    if offset + i < size:
                        byte = data[offset + i]
                        hex_str += f"{byte:02X} "
                        ascii_str += chr(byte) if 32 <= byte < 127 else '.'
                    else:
                        hex_str += "   "
                        ascii_str += " "

                f.write(f"0x{offset:04X}: {hex_str} | {ascii_str}\n")

        logger.info(f"Dumped {size} bytes to {bin_file} and {hex_file}")

    @on_state_change("APPVIEW")
    def on_appview(self):
        """Run offset scan when entering game view."""
        if self._scanned:
            return

        logger.info("=" * 60)
        logger.info("STARTING OFFSET SCAN")
        logger.info("=" * 60)

        try:
            self._run_full_scan()
            self._scanned = True
        except Exception as e:
            logger.error(f"Scan failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _run_full_scan(self):
        """Perform comprehensive offset scanning."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        results = {
            "scan_time": datetime.now().isoformat(),
            "solar_system": {},
            "planet_gen_inputs": [],
            "offset_candidates": {},
            "string_matches": [],
        }

        # Use cached solar system from Generate hook (same as extractor)
        if not self._cached_solar_system:
            logger.error("No cached solar system! Generate hook may not have fired.")
            return

        solar_system = self._cached_solar_system
        solar_system_addr = get_addressof(solar_system)
        sys_data = solar_system.mSolarSystemData
        sys_data_addr = get_addressof(sys_data)

        logger.info(f"SolarSystem @ 0x{solar_system_addr:X}")
        logger.info(f"SolarSystemData @ 0x{sys_data_addr:X}")

        results["solar_system"]["address"] = f"0x{solar_system_addr:X}"
        results["solar_system"]["sys_data_address"] = f"0x{sys_data_addr:X}"

        # =================================================================
        # DUMP MEMORY REGIONS
        # =================================================================
        logger.info("\n--- Dumping Memory Regions ---")

        # Dump GcSolarSystemData (large struct, ~0x2500 bytes to be safe)
        dump_file = self._output_dir / f"sys_data_{timestamp}"
        self._dump_memory_region(sys_data_addr, 0x2500, dump_file, "GcSolarSystemData")

        # =================================================================
        # SCAN FOR PLANET COUNT (should be 1-6)
        # =================================================================
        logger.info("\n--- Scanning for Planet Count (1-6) ---")
        planet_counts = self._scan_for_int_range(sys_data_addr, 0x2500, 1, 6, "PlanetCount")
        results["offset_candidates"]["planet_count"] = planet_counts[:20]  # Limit results
        for r in planet_counts[:10]:
            logger.info(f"  Possible planet count at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR TRADING CLASS (0-6)
        # =================================================================
        logger.info("\n--- Scanning for Trading Class (0-6) ---")
        trading = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_TRADING, "TradingClass")
        results["offset_candidates"]["trading_class"] = trading[:20]
        for r in trading[:10]:
            logger.info(f"  Possible trading class at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR WEALTH CLASS (0-3)
        # =================================================================
        logger.info("\n--- Scanning for Wealth Class (0-3) ---")
        wealth = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_WEALTH, "WealthClass")
        results["offset_candidates"]["wealth_class"] = wealth[:20]
        for r in wealth[:10]:
            logger.info(f"  Possible wealth class at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR CONFLICT LEVEL (0-3)
        # =================================================================
        logger.info("\n--- Scanning for Conflict Level (0-3) ---")
        conflict = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_CONFLICT, "ConflictLevel")
        results["offset_candidates"]["conflict_level"] = conflict[:20]
        for r in conflict[:10]:
            logger.info(f"  Possible conflict level at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR ALIEN RACE (0-6)
        # =================================================================
        logger.info("\n--- Scanning for Alien Race (0-6) ---")
        race = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_RACES, "AlienRace")
        results["offset_candidates"]["alien_race"] = race[:20]
        for r in race[:10]:
            logger.info(f"  Possible race at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR STAR TYPE (0-3)
        # =================================================================
        logger.info("\n--- Scanning for Star Type (0-3) ---")
        star = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_STARS, "StarType")
        results["offset_candidates"]["star_type"] = star[:20]
        for r in star[:10]:
            logger.info(f"  Possible star type at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR BIOME VALUES (0-13)
        # =================================================================
        logger.info("\n--- Scanning for Biome Values (0-13) ---")
        biomes = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_BIOMES, "BiomeType")
        results["offset_candidates"]["biome_type"] = biomes[:30]
        for r in biomes[:15]:
            logger.info(f"  Possible biome at {r['offset']}: {r['value']}")

        # =================================================================
        # SCAN FOR PLANET SIZE (0-4, especially 3=Moon)
        # =================================================================
        logger.info("\n--- Scanning for Planet Size (0-4) ---")
        sizes = self._scan_for_enum_values(sys_data_addr, 0x2500, KNOWN_SIZES, "PlanetSize")
        results["offset_candidates"]["planet_size"] = sizes[:30]
        for r in sizes[:15]:
            logger.info(f"  Possible size at {r['offset']}: {r['value']} {'[MOON]' if r['value'] == 3 else ''}")

        # =================================================================
        # SCAN FOR RESOURCE STRINGS
        # =================================================================
        logger.info("\n--- Scanning for Resource Strings ---")
        strings = self._scan_for_strings(sys_data_addr, 0x2500, KNOWN_RESOURCES)
        results["string_matches"] = strings
        for r in strings[:15]:
            logger.info(f"  Found '{r['full_string']}' at {r['offset']}")

        # =================================================================
        # CROSS-REFERENCE WITH NMS.PY VALUES
        # =================================================================
        logger.info("\n--- Cross-referencing with NMS.py ---")

        try:
            # Get NMS.py values for comparison
            nmspy_data = {}
            if hasattr(sys_data, 'Planets'):
                nmspy_data['Planets'] = int(sys_data.Planets) if hasattr(sys_data.Planets, '__int__') else str(sys_data.Planets)
            if hasattr(sys_data, 'PrimePlanets'):
                nmspy_data['PrimePlanets'] = int(sys_data.PrimePlanets) if hasattr(sys_data.PrimePlanets, '__int__') else str(sys_data.PrimePlanets)
            if hasattr(sys_data, 'StarType'):
                nmspy_data['StarType'] = str(sys_data.StarType)
            if hasattr(sys_data, 'TradingData'):
                td = sys_data.TradingData
                if hasattr(td, 'TradingClass'):
                    nmspy_data['TradingClass'] = str(td.TradingClass)
                if hasattr(td, 'WealthClass'):
                    nmspy_data['WealthClass'] = str(td.WealthClass)
            if hasattr(sys_data, 'ConflictData'):
                nmspy_data['ConflictData'] = str(sys_data.ConflictData)
            if hasattr(sys_data, 'InhabitingRace'):
                nmspy_data['InhabitingRace'] = str(sys_data.InhabitingRace)

            results["nmspy_values"] = nmspy_data
            logger.info(f"  NMS.py values: {nmspy_data}")
        except Exception as e:
            logger.warning(f"  NMS.py cross-reference failed: {e}")

        # =================================================================
        # ANALYZE OFFSET CLUSTERS
        # =================================================================
        logger.info("\n--- Analyzing Offset Clusters ---")

        # Look for clusters of valid values (trading + wealth + conflict should be near each other)
        cluster_analysis = self._find_offset_clusters(results["offset_candidates"])
        results["cluster_analysis"] = cluster_analysis

        for cluster in cluster_analysis[:5]:
            logger.info(f"  Cluster at ~0x{cluster['center_offset']:04X}: {cluster['fields']}")

        # =================================================================
        # SAVE RESULTS
        # =================================================================
        output_file = self._output_dir / f"offset_scan_{timestamp}.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2, default=str)

        logger.info("\n" + "=" * 60)
        logger.info(f"SCAN COMPLETE - Results saved to: {output_file}")
        logger.info("Check the .hex file for manual analysis")
        logger.info("=" * 60)

    def _find_offset_clusters(self, candidates: Dict) -> List[Dict]:
        """Find clusters of related offsets."""
        # Collect all offsets
        all_offsets = []
        for field, matches in candidates.items():
            for m in matches:
                all_offsets.append({
                    "field": field,
                    "offset": m["offset_dec"],
                    "value": m["value"]
                })

        # Sort by offset
        all_offsets.sort(key=lambda x: x["offset"])

        # Find clusters (offsets within 0x20 of each other)
        clusters = []
        i = 0
        while i < len(all_offsets):
            cluster_start = all_offsets[i]["offset"]
            cluster_fields = [all_offsets[i]]

            j = i + 1
            while j < len(all_offsets) and all_offsets[j]["offset"] - cluster_start < 0x20:
                cluster_fields.append(all_offsets[j])
                j += 1

            if len(cluster_fields) >= 2:
                unique_fields = set(f["field"] for f in cluster_fields)
                if len(unique_fields) >= 2:  # Multiple different fields
                    clusters.append({
                        "center_offset": cluster_start,
                        "fields": list(unique_fields),
                        "matches": cluster_fields,
                    })

            i = j if j > i else i + 1

        # Sort by number of unique fields (most interesting first)
        clusters.sort(key=lambda x: len(x["fields"]), reverse=True)
        return clusters
