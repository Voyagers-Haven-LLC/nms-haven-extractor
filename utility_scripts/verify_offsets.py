"""
Offset Verification Tool for Haven Extractor v6.0.0

This script helps verify that the PDB-derived memory offsets are correct
for the current NMS game version. Run this alongside NMS to compare
direct memory reads with NMS.py struct access.

Usage:
1. Load NMS with pyMHF
2. Enter a system
3. Run this verification to compare results

The offsets in haven_extractor.py are from NMS 4.13 (Fractal413 PDB).
If you're running a different version, offsets may need adjustment.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Import the offset constants from the main extractor
# The public mod is in dist/HavenExtractor/mod/haven_extractor.py
import sys
from pathlib import Path

# Add the dist mod folder to path
dist_mod_path = Path(__file__).parent.parent / "dist" / "HavenExtractor" / "mod"
sys.path.insert(0, str(dist_mod_path))

try:
    from haven_extractor import (
        SolarSystemDataOffsets,
        TradingDataOffsets,
        ConflictDataOffsets,
        PlanetGenInputOffsets,
        BIOME_TYPES,
        PLANET_SIZES,
        TRADING_CLASSES,
        WEALTH_CLASSES,
        CONFLICT_LEVELS,
        ALIEN_RACES,
        STAR_TYPES,
    )
except ImportError:
    print("ERROR: Could not import from haven_extractor.py")
    print(f"Looking in: {dist_mod_path}")
    print("Make sure the dist/HavenExtractor/mod folder exists")
    exit(1)


@dataclass
class OffsetVerificationResult:
    """Results from offset verification."""
    field_name: str
    offset: int
    direct_value: Any
    direct_mapped: str
    nmspy_value: Any
    nmspy_mapped: str
    match: bool
    notes: str = ""


def verify_system_offsets(sys_data_addr: int, sys_data_struct) -> list:
    """
    Compare direct memory reads with NMS.py struct access for system data.

    Args:
        sys_data_addr: Memory address of GcSolarSystemData
        sys_data_struct: NMS.py mapped struct for comparison

    Returns:
        List of OffsetVerificationResult
    """
    import ctypes
    results = []

    def read_uint32(addr: int, offset: int) -> int:
        try:
            value = ctypes.c_uint32()
            ctypes.memmove(ctypes.addressof(value), addr + offset, 4)
            return value.value
        except:
            return -1

    def safe_enum(val) -> str:
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

    # Verify Planet Count
    direct_planets = read_uint32(sys_data_addr, SolarSystemDataOffsets.PLANETS_COUNT)
    nmspy_planets = -1
    try:
        if hasattr(sys_data_struct, 'Planets'):
            nmspy_planets = int(sys_data_struct.Planets)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Planets Count",
        offset=SolarSystemDataOffsets.PLANETS_COUNT,
        direct_value=direct_planets,
        direct_mapped=str(direct_planets),
        nmspy_value=nmspy_planets,
        nmspy_mapped=str(nmspy_planets),
        match=(direct_planets == nmspy_planets),
    ))

    # Verify Prime Planets
    direct_prime = read_uint32(sys_data_addr, SolarSystemDataOffsets.PRIME_PLANETS)
    nmspy_prime = -1
    try:
        if hasattr(sys_data_struct, 'PrimePlanets'):
            nmspy_prime = int(sys_data_struct.PrimePlanets)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Prime Planets",
        offset=SolarSystemDataOffsets.PRIME_PLANETS,
        direct_value=direct_prime,
        direct_mapped=str(direct_prime),
        nmspy_value=nmspy_prime,
        nmspy_mapped=str(nmspy_prime),
        match=(direct_prime == nmspy_prime),
    ))

    # Verify Star Type
    direct_star = read_uint32(sys_data_addr, SolarSystemDataOffsets.STAR_TYPE)
    nmspy_star = -1
    nmspy_star_name = "Unknown"
    try:
        if hasattr(sys_data_struct, 'StarType'):
            nmspy_star_name = safe_enum(sys_data_struct.StarType)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Star Type",
        offset=SolarSystemDataOffsets.STAR_TYPE,
        direct_value=direct_star,
        direct_mapped=STAR_TYPES.get(direct_star, f"Unknown({direct_star})"),
        nmspy_value=nmspy_star,
        nmspy_mapped=nmspy_star_name,
        match=False,  # Compare mapped names manually
        notes="Compare mapped names - different access methods"
    ))

    # Verify Trading Class (Economy Type)
    trading_addr = sys_data_addr + SolarSystemDataOffsets.TRADING_DATA
    direct_trading = read_uint32(trading_addr, TradingDataOffsets.TRADING_CLASS)
    nmspy_trading = "Unknown"
    try:
        if hasattr(sys_data_struct, 'TradingData'):
            trading = sys_data_struct.TradingData
            if hasattr(trading, 'TradingClass'):
                nmspy_trading = safe_enum(trading.TradingClass)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Trading Class (Economy)",
        offset=SolarSystemDataOffsets.TRADING_DATA + TradingDataOffsets.TRADING_CLASS,
        direct_value=direct_trading,
        direct_mapped=TRADING_CLASSES.get(direct_trading, f"Unknown({direct_trading})"),
        nmspy_value=-1,
        nmspy_mapped=nmspy_trading,
        match=False,
        notes="Compare mapped names"
    ))

    # Verify Wealth Class
    direct_wealth = read_uint32(trading_addr, TradingDataOffsets.WEALTH_CLASS)
    nmspy_wealth = "Unknown"
    try:
        if hasattr(sys_data_struct, 'TradingData'):
            trading = sys_data_struct.TradingData
            if hasattr(trading, 'WealthClass'):
                nmspy_wealth = safe_enum(trading.WealthClass)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Wealth Class",
        offset=SolarSystemDataOffsets.TRADING_DATA + TradingDataOffsets.WEALTH_CLASS,
        direct_value=direct_wealth,
        direct_mapped=WEALTH_CLASSES.get(direct_wealth, f"Unknown({direct_wealth})"),
        nmspy_value=-1,
        nmspy_mapped=nmspy_wealth,
        match=False,
        notes="Compare mapped names"
    ))

    # Verify Conflict Level
    conflict_addr = sys_data_addr + SolarSystemDataOffsets.CONFLICT_DATA
    direct_conflict = read_uint32(conflict_addr, ConflictDataOffsets.CONFLICT_LEVEL)
    nmspy_conflict = "Unknown"
    try:
        if hasattr(sys_data_struct, 'ConflictData'):
            nmspy_conflict = safe_enum(sys_data_struct.ConflictData)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Conflict Level",
        offset=SolarSystemDataOffsets.CONFLICT_DATA + ConflictDataOffsets.CONFLICT_LEVEL,
        direct_value=direct_conflict,
        direct_mapped=CONFLICT_LEVELS.get(direct_conflict, f"Unknown({direct_conflict})"),
        nmspy_value=-1,
        nmspy_mapped=nmspy_conflict,
        match=False,
        notes="Compare mapped names"
    ))

    # Verify Inhabiting Race
    direct_race = read_uint32(sys_data_addr, SolarSystemDataOffsets.INHABITING_RACE)
    nmspy_race = "Unknown"
    try:
        if hasattr(sys_data_struct, 'InhabitingRace'):
            nmspy_race = safe_enum(sys_data_struct.InhabitingRace)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name="Inhabiting Race",
        offset=SolarSystemDataOffsets.INHABITING_RACE,
        direct_value=direct_race,
        direct_mapped=ALIEN_RACES.get(direct_race, f"Unknown({direct_race})"),
        nmspy_value=-1,
        nmspy_mapped=nmspy_race,
        match=False,
        notes="Compare mapped names"
    ))

    return results


def verify_planet_offsets(sys_data_addr: int, planet_index: int, planet_struct) -> list:
    """
    Compare direct memory reads with NMS.py struct access for planet data.

    Args:
        sys_data_addr: Memory address of GcSolarSystemData
        planet_index: Index of planet (0-5)
        planet_struct: NMS.py mapped cGcPlanet struct

    Returns:
        List of OffsetVerificationResult
    """
    import ctypes
    results = []

    def read_uint32(addr: int, offset: int) -> int:
        try:
            value = ctypes.c_uint32()
            ctypes.memmove(ctypes.addressof(value), addr + offset, 4)
            return value.value
        except:
            return -1

    def safe_enum(val) -> str:
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

    # Calculate planet gen input address
    planet_gen_addr = sys_data_addr + SolarSystemDataOffsets.PLANET_GEN_INPUTS
    planet_gen_addr += planet_index * PlanetGenInputOffsets.STRUCT_SIZE

    # Verify Biome
    direct_biome = read_uint32(planet_gen_addr, PlanetGenInputOffsets.BIOME)
    nmspy_biome = "Unknown"
    try:
        if hasattr(planet_struct, 'mPlanetGenerationInputData'):
            gen_input = planet_struct.mPlanetGenerationInputData
            if hasattr(gen_input, 'Biome'):
                nmspy_biome = safe_enum(gen_input.Biome)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name=f"Planet {planet_index} Biome",
        offset=planet_gen_addr - sys_data_addr + PlanetGenInputOffsets.BIOME,
        direct_value=direct_biome,
        direct_mapped=BIOME_TYPES.get(direct_biome, f"Unknown({direct_biome})"),
        nmspy_value=-1,
        nmspy_mapped=nmspy_biome,
        match=False,
        notes="Compare mapped names"
    ))

    # Verify Planet Size
    direct_size = read_uint32(planet_gen_addr, PlanetGenInputOffsets.PLANET_SIZE)
    nmspy_size = "Unknown"
    try:
        if hasattr(planet_struct, 'mPlanetGenerationInputData'):
            gen_input = planet_struct.mPlanetGenerationInputData
            if hasattr(gen_input, 'PlanetSize'):
                nmspy_size = safe_enum(gen_input.PlanetSize)
    except:
        pass
    results.append(OffsetVerificationResult(
        field_name=f"Planet {planet_index} Size",
        offset=planet_gen_addr - sys_data_addr + PlanetGenInputOffsets.PLANET_SIZE,
        direct_value=direct_size,
        direct_mapped=PLANET_SIZES.get(direct_size, f"Unknown({direct_size})"),
        nmspy_value=-1,
        nmspy_mapped=nmspy_size,
        match=False,
        notes="Compare mapped names - Size=3 means Moon"
    ))

    return results


def print_verification_report(results: list):
    """Print a formatted verification report."""
    print("\n" + "=" * 80)
    print("OFFSET VERIFICATION REPORT")
    print("=" * 80)
    print(f"{'Field':<30} {'Offset':<12} {'Direct':<20} {'NMS.py':<20}")
    print("-" * 80)

    for r in results:
        offset_hex = f"0x{r.offset:04X}"
        direct_str = f"{r.direct_mapped}"[:18]
        nmspy_str = f"{r.nmspy_mapped}"[:18]

        status = "✓" if r.match else "?"
        print(f"{r.field_name:<30} {offset_hex:<12} {direct_str:<20} {nmspy_str:<20} {status}")
        if r.notes:
            print(f"  Note: {r.notes}")

    print("=" * 80)
    print("\nLegend:")
    print("  ✓ = Values match exactly")
    print("  ? = Manual comparison needed (different access methods)")
    print("\nIf Direct and NMS.py show different MAPPED values for the same field,")
    print("the offsets may need adjustment for your game version.")


if __name__ == "__main__":
    print("Offset Verification Tool for Haven Extractor v6.0.0")
    print("This script should be run as a pyMHF mod alongside NMS.")
    print("\nTo use:")
    print("1. Import this module in your test mod")
    print("2. Call verify_system_offsets() with sys_data address and struct")
    print("3. Call verify_planet_offsets() for each planet")
    print("4. Call print_verification_report() to see results")
