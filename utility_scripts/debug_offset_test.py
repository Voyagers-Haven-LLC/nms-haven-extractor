"""
Debug Offset Test for Fractal413 (NMS 4.13)

This script tests the direct memory offsets against the debug version
to verify they're correct before using on the live game.

Run this as a pyMHF mod with the Fractal413 debug version.

Usage:
1. Start NMS 4.13 debug via RunAsDate (set date before Sept 28, 2023)
2. Load this mod with pyMHF
3. Enter any solar system
4. Check the output log for offset verification results
"""

import json
import logging
import ctypes
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from pymhf import Mod
from pymhf.core.memutils import map_struct, get_addressof
import nmspy.data.types as nms
from nmspy.decorators import on_state_change
from nmspy.common import gameData

logger = logging.getLogger(__name__)

# =============================================================================
# OFFSET CONSTANTS TO TEST (from MBINCompiler / NMS 4.13 PDB)
# =============================================================================

class SolarSystemDataOffsets:
    PLANETS_COUNT = 0x2264
    PRIME_PLANETS = 0x2268
    STAR_CLASS = 0x224C
    STAR_TYPE = 0x2270
    TRADING_DATA = 0x2240
    CONFLICT_DATA = 0x2250
    INHABITING_RACE = 0x2254
    SEED = 0x21A0
    PLANET_GEN_INPUTS = 0x1EA0

class TradingDataOffsets:
    TRADING_CLASS = 0x0
    WEALTH_CLASS = 0x4

class ConflictDataOffsets:
    CONFLICT_LEVEL = 0x0

class PlanetGenInputOffsets:
    STRUCT_SIZE = 0x54
    COMMON_SUBSTANCE = 0x00
    RARE_SUBSTANCE = 0x10
    SEED = 0x20
    BIOME = 0x30
    BIOME_SUBTYPE = 0x34
    PLANET_CLASS = 0x38
    PLANET_INDEX = 0x3C
    PLANET_SIZE = 0x40
    REALITY_INDEX = 0x44
    STAR_TYPE = 0x48

# Enum mappings
BIOME_TYPES = {
    0: "Lush", 1: "Toxic", 2: "Scorched", 3: "Radioactive", 4: "Frozen",
    5: "Barren", 6: "Dead", 7: "Weird", 8: "Red", 9: "Green", 10: "Blue",
    11: "Test", 12: "Swamp", 13: "Lava", 14: "Waterworld", 15: "GasGiant", 16: "All"
}

PLANET_SIZES = {0: "Large", 1: "Medium", 2: "Small", 3: "Moon", 4: "Giant"}
TRADING_CLASSES = {0: "Mining", 1: "HighTech", 2: "Trading", 3: "Manufacturing", 4: "Fusion", 5: "Scientific", 6: "PowerGeneration"}
WEALTH_CLASSES = {0: "Poor", 1: "Average", 2: "Wealthy", 3: "Pirate"}
CONFLICT_LEVELS = {0: "Low", 1: "Default", 2: "High", 3: "Pirate"}
ALIEN_RACES = {0: "Traders", 1: "Warriors", 2: "Explorers", 3: "Robots", 4: "Atlas", 5: "Diplomats", 6: "None"}
STAR_TYPES = {0: "Yellow", 1: "Red", 2: "Green", 3: "Blue"}


class DebugOffsetTestMod(Mod):
    __author__ = "Voyagers Haven"
    __version__ = "1.0.0"
    __description__ = "Debug offset verification for Fractal413"

    def __init__(self):
        super().__init__()
        self._output_dir = Path.home() / "Documents" / "Haven-Extractor" / "debug_tests"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._tested = False

        logger.info("=" * 60)
        logger.info("DEBUG OFFSET TEST MOD - Fractal413 (NMS 4.13)")
        logger.info(f"Output: {self._output_dir}")
        logger.info("Enter a solar system to run offset verification...")
        logger.info("=" * 60)

    def _read_uint32(self, addr: int, offset: int) -> int:
        try:
            value = ctypes.c_uint32()
            ctypes.memmove(ctypes.addressof(value), addr + offset, 4)
            return value.value
        except:
            return 0xDEADBEEF

    def _read_int32(self, addr: int, offset: int) -> int:
        try:
            value = ctypes.c_int32()
            ctypes.memmove(ctypes.addressof(value), addr + offset, 4)
            return value.value
        except:
            return -999999

    def _read_string(self, addr: int, offset: int, max_len: int = 16) -> str:
        try:
            buffer = ctypes.create_string_buffer(max_len)
            ctypes.memmove(buffer, addr + offset, max_len)
            raw = buffer.raw
            null_pos = raw.find(b'\x00')
            if null_pos >= 0:
                raw = raw[:null_pos]
            return raw.decode('utf-8', errors='ignore').strip()
        except:
            return "<READ_ERROR>"

    def _safe_enum(self, val) -> str:
        try:
            if val is None:
                return "None"
            if hasattr(val, 'name'):
                return val.name
            if hasattr(val, 'value'):
                return str(val.value)
            return str(val)
        except:
            return "Error"

    def _safe_int(self, val) -> int:
        try:
            if val is None:
                return -1
            if hasattr(val, 'value'):
                return int(val.value)
            return int(val)
        except:
            return -1

    @on_state_change("APPVIEW")
    def on_appview(self):
        """Run offset test when entering game view."""
        if self._tested:
            return

        logger.info("=" * 60)
        logger.info("RUNNING OFFSET VERIFICATION TEST")
        logger.info("=" * 60)

        try:
            self._run_offset_test()
            self._tested = True
        except Exception as e:
            logger.error(f"Test failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _run_offset_test(self):
        """Run comprehensive offset verification."""
        results = {
            "test_time": datetime.now().isoformat(),
            "game_version": "4.13 (Fractal413 Debug)",
            "system_data": {},
            "planet_data": [],
            "offset_verification": [],
        }

        # Get solar system
        simulation = gameData.simulation
        if not simulation:
            logger.error("No simulation available!")
            return

        ptr = simulation.mpSolarSystem
        if not ptr:
            logger.error("No solar system pointer!")
            return

        solar_system = map_struct(get_addressof(ptr), nms.cGcSolarSystem)
        sys_data = solar_system.mSolarSystemData
        sys_data_addr = get_addressof(sys_data)

        logger.info(f"SolarSystem address: 0x{get_addressof(ptr):X}")
        logger.info(f"SolarSystemData address: 0x{sys_data_addr:X}")

        # =================================================================
        # TEST SYSTEM DATA OFFSETS
        # =================================================================
        logger.info("\n" + "=" * 40)
        logger.info("SYSTEM DATA OFFSET TESTS")
        logger.info("=" * 40)

        # Test Planet Count
        direct_planets = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PLANETS_COUNT)
        nmspy_planets = self._safe_int(sys_data.Planets) if hasattr(sys_data, 'Planets') else -1
        match = direct_planets == nmspy_planets
        logger.info(f"Planets Count @ 0x{SolarSystemDataOffsets.PLANETS_COUNT:04X}: DIRECT={direct_planets}, NMSPY={nmspy_planets} {'✓' if match else '✗'}")
        results["offset_verification"].append({
            "field": "Planets", "offset": hex(SolarSystemDataOffsets.PLANETS_COUNT),
            "direct": direct_planets, "nmspy": nmspy_planets, "match": match
        })

        # Test Prime Planets
        direct_prime = self._read_int32(sys_data_addr, SolarSystemDataOffsets.PRIME_PLANETS)
        nmspy_prime = self._safe_int(sys_data.PrimePlanets) if hasattr(sys_data, 'PrimePlanets') else -1
        match = direct_prime == nmspy_prime
        logger.info(f"Prime Planets @ 0x{SolarSystemDataOffsets.PRIME_PLANETS:04X}: DIRECT={direct_prime}, NMSPY={nmspy_prime} {'✓' if match else '✗'}")
        results["offset_verification"].append({
            "field": "PrimePlanets", "offset": hex(SolarSystemDataOffsets.PRIME_PLANETS),
            "direct": direct_prime, "nmspy": nmspy_prime, "match": match
        })

        # Test Star Type
        direct_star = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.STAR_TYPE)
        direct_star_name = STAR_TYPES.get(direct_star, f"Unknown({direct_star})")
        nmspy_star = self._safe_enum(sys_data.StarType) if hasattr(sys_data, 'StarType') else "N/A"
        logger.info(f"Star Type @ 0x{SolarSystemDataOffsets.STAR_TYPE:04X}: DIRECT={direct_star_name} (raw:{direct_star}), NMSPY={nmspy_star}")
        results["offset_verification"].append({
            "field": "StarType", "offset": hex(SolarSystemDataOffsets.STAR_TYPE),
            "direct_raw": direct_star, "direct_mapped": direct_star_name, "nmspy": nmspy_star
        })

        # Test Trading Data
        trading_addr = sys_data_addr + SolarSystemDataOffsets.TRADING_DATA
        direct_trading = self._read_uint32(trading_addr, TradingDataOffsets.TRADING_CLASS)
        direct_trading_name = TRADING_CLASSES.get(direct_trading, f"Unknown({direct_trading})")
        direct_wealth = self._read_uint32(trading_addr, TradingDataOffsets.WEALTH_CLASS)
        direct_wealth_name = WEALTH_CLASSES.get(direct_wealth, f"Unknown({direct_wealth})")

        nmspy_trading = "N/A"
        nmspy_wealth = "N/A"
        if hasattr(sys_data, 'TradingData'):
            td = sys_data.TradingData
            if hasattr(td, 'TradingClass'):
                nmspy_trading = self._safe_enum(td.TradingClass)
            if hasattr(td, 'WealthClass'):
                nmspy_wealth = self._safe_enum(td.WealthClass)

        logger.info(f"Trading Class @ 0x{SolarSystemDataOffsets.TRADING_DATA:04X}+0x{TradingDataOffsets.TRADING_CLASS:02X}: DIRECT={direct_trading_name} (raw:{direct_trading}), NMSPY={nmspy_trading}")
        logger.info(f"Wealth Class @ 0x{SolarSystemDataOffsets.TRADING_DATA:04X}+0x{TradingDataOffsets.WEALTH_CLASS:02X}: DIRECT={direct_wealth_name} (raw:{direct_wealth}), NMSPY={nmspy_wealth}")
        results["offset_verification"].append({
            "field": "TradingClass", "offset": hex(SolarSystemDataOffsets.TRADING_DATA),
            "direct_raw": direct_trading, "direct_mapped": direct_trading_name, "nmspy": nmspy_trading
        })
        results["offset_verification"].append({
            "field": "WealthClass", "offset": hex(SolarSystemDataOffsets.TRADING_DATA + 4),
            "direct_raw": direct_wealth, "direct_mapped": direct_wealth_name, "nmspy": nmspy_wealth
        })

        # Test Conflict Data
        conflict_addr = sys_data_addr + SolarSystemDataOffsets.CONFLICT_DATA
        direct_conflict = self._read_uint32(conflict_addr, ConflictDataOffsets.CONFLICT_LEVEL)
        direct_conflict_name = CONFLICT_LEVELS.get(direct_conflict, f"Unknown({direct_conflict})")
        nmspy_conflict = self._safe_enum(sys_data.ConflictData) if hasattr(sys_data, 'ConflictData') else "N/A"
        logger.info(f"Conflict @ 0x{SolarSystemDataOffsets.CONFLICT_DATA:04X}: DIRECT={direct_conflict_name} (raw:{direct_conflict}), NMSPY={nmspy_conflict}")
        results["offset_verification"].append({
            "field": "ConflictLevel", "offset": hex(SolarSystemDataOffsets.CONFLICT_DATA),
            "direct_raw": direct_conflict, "direct_mapped": direct_conflict_name, "nmspy": nmspy_conflict
        })

        # Test Race
        direct_race = self._read_uint32(sys_data_addr, SolarSystemDataOffsets.INHABITING_RACE)
        direct_race_name = ALIEN_RACES.get(direct_race, f"Unknown({direct_race})")
        nmspy_race = self._safe_enum(sys_data.InhabitingRace) if hasattr(sys_data, 'InhabitingRace') else "N/A"
        logger.info(f"Race @ 0x{SolarSystemDataOffsets.INHABITING_RACE:04X}: DIRECT={direct_race_name} (raw:{direct_race}), NMSPY={nmspy_race}")
        results["offset_verification"].append({
            "field": "InhabitingRace", "offset": hex(SolarSystemDataOffsets.INHABITING_RACE),
            "direct_raw": direct_race, "direct_mapped": direct_race_name, "nmspy": nmspy_race
        })

        # =================================================================
        # TEST PLANET DATA OFFSETS
        # =================================================================
        logger.info("\n" + "=" * 40)
        logger.info("PLANET DATA OFFSET TESTS")
        logger.info("=" * 40)

        planet_count = direct_planets if direct_planets > 0 and direct_planets <= 6 else nmspy_planets
        if planet_count <= 0 or planet_count > 6:
            planet_count = 6

        for i in range(planet_count):
            logger.info(f"\n--- Planet {i} ---")

            # Direct read from PlanetGenerationInputData array
            planet_gen_addr = sys_data_addr + SolarSystemDataOffsets.PLANET_GEN_INPUTS
            planet_gen_addr += i * PlanetGenInputOffsets.STRUCT_SIZE

            direct_biome = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.BIOME)
            direct_biome_name = BIOME_TYPES.get(direct_biome, f"Unknown({direct_biome})")

            direct_size = self._read_uint32(planet_gen_addr, PlanetGenInputOffsets.PLANET_SIZE)
            direct_size_name = PLANET_SIZES.get(direct_size, f"Unknown({direct_size})")

            direct_common = self._read_string(planet_gen_addr, PlanetGenInputOffsets.COMMON_SUBSTANCE, 16)
            direct_rare = self._read_string(planet_gen_addr, PlanetGenInputOffsets.RARE_SUBSTANCE, 16)

            logger.info(f"  Gen Input @ 0x{planet_gen_addr:X}")
            logger.info(f"  Biome @ +0x{PlanetGenInputOffsets.BIOME:02X}: {direct_biome_name} (raw:{direct_biome})")
            logger.info(f"  Size @ +0x{PlanetGenInputOffsets.PLANET_SIZE:02X}: {direct_size_name} (raw:{direct_size}) {'[MOON]' if direct_size == 3 else ''}")
            logger.info(f"  Common Resource @ +0x{PlanetGenInputOffsets.COMMON_SUBSTANCE:02X}: '{direct_common}'")
            logger.info(f"  Rare Resource @ +0x{PlanetGenInputOffsets.RARE_SUBSTANCE:02X}: '{direct_rare}'")

            # Try NMS.py access for comparison
            try:
                planets_array = solar_system.maPlanets
                planet = planets_array[i]
                if planet and hasattr(planet, 'mPlanetGenerationInputData'):
                    gen_input = planet.mPlanetGenerationInputData
                    nmspy_biome = self._safe_enum(gen_input.Biome) if hasattr(gen_input, 'Biome') else "N/A"
                    nmspy_size = self._safe_enum(gen_input.PlanetSize) if hasattr(gen_input, 'PlanetSize') else "N/A"
                    logger.info(f"  NMSPY Biome: {nmspy_biome}, Size: {nmspy_size}")
            except Exception as e:
                logger.debug(f"  NMSPY access failed: {e}")

            results["planet_data"].append({
                "index": i,
                "biome_raw": direct_biome,
                "biome": direct_biome_name,
                "size_raw": direct_size,
                "size": direct_size_name,
                "is_moon": direct_size == 3,
                "common_resource": direct_common,
                "rare_resource": direct_rare,
            })

        # Save results
        output_file = self._output_dir / f"offset_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)

        logger.info("\n" + "=" * 60)
        logger.info(f"TEST COMPLETE - Results saved to: {output_file}")
        logger.info("=" * 60)

        # Print summary
        matches = sum(1 for v in results["offset_verification"] if v.get("match", False))
        total = len([v for v in results["offset_verification"] if "match" in v])
        logger.info(f"\nExact matches: {matches}/{total}")
        logger.info("Check the JSON file for full comparison data.")
        logger.info("Compare DIRECT values with in-game discovery screen to verify offsets!")
