"""
Offline Memory Dump Analyzer

This script analyzes the .bin memory dumps created by offset_scanner.py
to help identify correct offsets without needing the game running.

Usage:
    python analyze_dump.py <dump_file.bin>

Or in interactive mode:
    python analyze_dump.py
"""

import sys
import struct
from pathlib import Path
from typing import Dict, List, Tuple

# Known offsets from MBINCompiler (NMS 4.13)
KNOWN_OFFSETS_413 = {
    "GcSolarSystemData": {
        "PLANETS_COUNT": 0x2264,
        "PRIME_PLANETS": 0x2268,
        "STAR_CLASS": 0x224C,
        "STAR_TYPE": 0x2270,
        "TRADING_DATA": 0x2240,
        "CONFLICT_DATA": 0x2250,
        "INHABITING_RACE": 0x2254,
        "SEED": 0x21A0,
        "PLANET_GEN_INPUTS": 0x1EA0,
    },
    "GcPlanetTradingData": {
        "TRADING_CLASS": 0x0,
        "WEALTH_CLASS": 0x4,
    },
    "GcPlayerConflictData": {
        "CONFLICT_LEVEL": 0x0,
    },
    "GcPlanetGenerationInputData": {
        "STRUCT_SIZE": 0x54,
        "COMMON_SUBSTANCE": 0x00,
        "RARE_SUBSTANCE": 0x10,
        "BIOME": 0x30,
        "BIOME_SUBTYPE": 0x34,
        "PLANET_INDEX": 0x3C,
        "PLANET_SIZE": 0x40,
    },
}

# Enum value mappings
BIOME_TYPES = {
    0: "Lush", 1: "Toxic", 2: "Scorched", 3: "Radioactive", 4: "Frozen",
    5: "Barren", 6: "Dead", 7: "Weird", 8: "Red", 9: "Green", 10: "Blue",
    11: "Test", 12: "Swamp", 13: "Lava", 14: "Waterworld", 15: "GasGiant"
}
PLANET_SIZES = {0: "Large", 1: "Medium", 2: "Small", 3: "Moon", 4: "Giant"}
TRADING_CLASSES = {0: "Mining", 1: "HighTech", 2: "Trading", 3: "Manufacturing", 4: "Fusion", 5: "Scientific", 6: "PowerGeneration"}
WEALTH_CLASSES = {0: "Poor", 1: "Average", 2: "Wealthy", 3: "Pirate"}
CONFLICT_LEVELS = {0: "Low", 1: "Default", 2: "High", 3: "Pirate"}
ALIEN_RACES = {0: "Gek", 1: "Vy'keen", 2: "Korvax", 3: "Robots", 4: "Atlas", 5: "Diplomats", 6: "None"}
STAR_TYPES = {0: "Yellow", 1: "Red", 2: "Green", 3: "Blue"}


def read_uint32(data: bytes, offset: int) -> int:
    """Read 32-bit unsigned int from bytes."""
    if offset + 4 > len(data):
        return 0
    return struct.unpack('<I', data[offset:offset+4])[0]


def read_int32(data: bytes, offset: int) -> int:
    """Read 32-bit signed int from bytes."""
    if offset + 4 > len(data):
        return 0
    return struct.unpack('<i', data[offset:offset+4])[0]


def read_string(data: bytes, offset: int, max_len: int = 16) -> str:
    """Read null-terminated string from bytes."""
    if offset >= len(data):
        return ""
    end = min(offset + max_len, len(data))
    raw = data[offset:end]
    null_pos = raw.find(b'\x00')
    if null_pos >= 0:
        raw = raw[:null_pos]
    try:
        return raw.decode('utf-8', errors='ignore')
    except:
        return ""


def analyze_at_known_offsets(data: bytes) -> Dict:
    """Analyze data using known 4.13 offsets."""
    results = {}

    offsets = KNOWN_OFFSETS_413["GcSolarSystemData"]

    # Planet counts
    planets = read_int32(data, offsets["PLANETS_COUNT"])
    prime = read_int32(data, offsets["PRIME_PLANETS"])
    results["planets"] = {"offset": hex(offsets["PLANETS_COUNT"]), "value": planets, "valid": 1 <= planets <= 6}
    results["prime_planets"] = {"offset": hex(offsets["PRIME_PLANETS"]), "value": prime, "valid": 0 <= prime <= planets}

    # Star type
    star = read_uint32(data, offsets["STAR_TYPE"])
    results["star_type"] = {
        "offset": hex(offsets["STAR_TYPE"]),
        "raw": star,
        "mapped": STAR_TYPES.get(star, f"Unknown({star})"),
        "valid": star in STAR_TYPES
    }

    # Trading data
    trading_base = offsets["TRADING_DATA"]
    trading_class = read_uint32(data, trading_base + 0)
    wealth_class = read_uint32(data, trading_base + 4)
    results["trading_class"] = {
        "offset": hex(trading_base),
        "raw": trading_class,
        "mapped": TRADING_CLASSES.get(trading_class, f"Unknown({trading_class})"),
        "valid": trading_class in TRADING_CLASSES
    }
    results["wealth_class"] = {
        "offset": hex(trading_base + 4),
        "raw": wealth_class,
        "mapped": WEALTH_CLASSES.get(wealth_class, f"Unknown({wealth_class})"),
        "valid": wealth_class in WEALTH_CLASSES
    }

    # Conflict data
    conflict_base = offsets["CONFLICT_DATA"]
    conflict = read_uint32(data, conflict_base)
    results["conflict_level"] = {
        "offset": hex(conflict_base),
        "raw": conflict,
        "mapped": CONFLICT_LEVELS.get(conflict, f"Unknown({conflict})"),
        "valid": conflict in CONFLICT_LEVELS
    }

    # Race
    race = read_uint32(data, offsets["INHABITING_RACE"])
    results["inhabiting_race"] = {
        "offset": hex(offsets["INHABITING_RACE"]),
        "raw": race,
        "mapped": ALIEN_RACES.get(race, f"Unknown({race})"),
        "valid": race in ALIEN_RACES
    }

    # Planet generation inputs
    planet_gen_base = offsets["PLANET_GEN_INPUTS"]
    struct_size = KNOWN_OFFSETS_413["GcPlanetGenerationInputData"]["STRUCT_SIZE"]
    planet_offsets = KNOWN_OFFSETS_413["GcPlanetGenerationInputData"]

    results["planets_data"] = []
    for i in range(min(planets if 1 <= planets <= 6 else 6, 6)):
        planet_addr = planet_gen_base + (i * struct_size)

        biome = read_uint32(data, planet_addr + planet_offsets["BIOME"])
        size = read_uint32(data, planet_addr + planet_offsets["PLANET_SIZE"])
        common = read_string(data, planet_addr + planet_offsets["COMMON_SUBSTANCE"], 16)
        rare = read_string(data, planet_addr + planet_offsets["RARE_SUBSTANCE"], 16)

        results["planets_data"].append({
            "index": i,
            "base_offset": hex(planet_addr),
            "biome": {"raw": biome, "mapped": BIOME_TYPES.get(biome, f"Unknown({biome})"), "valid": biome in BIOME_TYPES},
            "size": {"raw": size, "mapped": PLANET_SIZES.get(size, f"Unknown({size})"), "valid": size in PLANET_SIZES, "is_moon": size == 3},
            "common_resource": common,
            "rare_resource": rare,
        })

    return results


def scan_for_patterns(data: bytes, scan_range: int = None) -> Dict:
    """Scan for likely offset patterns."""
    if scan_range is None:
        scan_range = len(data)

    results = {
        "planet_count_candidates": [],
        "trading_candidates": [],
        "biome_candidates": [],
        "size_candidates": [],
        "string_candidates": [],
    }

    # Scan for planet count (1-6)
    for offset in range(0, min(scan_range, len(data) - 4), 4):
        val = read_int32(data, offset)
        if 1 <= val <= 6:
            results["planet_count_candidates"].append({"offset": hex(offset), "value": val})

    # Scan for trading class patterns (look for 0-6 followed by 0-3)
    for offset in range(0, min(scan_range, len(data) - 8), 4):
        trading = read_uint32(data, offset)
        wealth = read_uint32(data, offset + 4)
        if trading <= 6 and wealth <= 3:
            results["trading_candidates"].append({
                "offset": hex(offset),
                "trading": trading,
                "trading_mapped": TRADING_CLASSES.get(trading, "?"),
                "wealth": wealth,
                "wealth_mapped": WEALTH_CLASSES.get(wealth, "?"),
            })

    # Scan for biome values (0-15)
    for offset in range(0, min(scan_range, len(data) - 4), 4):
        val = read_uint32(data, offset)
        if val <= 15:
            results["biome_candidates"].append({"offset": hex(offset), "value": val, "mapped": BIOME_TYPES.get(val, "?")})

    # Scan for size values, especially Moon (3)
    for offset in range(0, min(scan_range, len(data) - 4), 4):
        val = read_uint32(data, offset)
        if val <= 4:
            results["size_candidates"].append({
                "offset": hex(offset),
                "value": val,
                "mapped": PLANET_SIZES.get(val, "?"),
                "is_moon": val == 3
            })

    # Scan for resource strings
    resource_patterns = [b"FUEL", b"LAND", b"OXYGEN", b"FERRITE", b"SODIUM", b"COPPER"]
    for pattern in resource_patterns:
        idx = 0
        while True:
            pos = data.find(pattern, idx)
            if pos == -1:
                break
            # Get full string
            end = data.find(b'\x00', pos)
            if end == -1:
                end = pos + 20
            full_str = data[pos:min(end, pos + 20)].decode('utf-8', errors='ignore')
            results["string_candidates"].append({"offset": hex(pos), "string": full_str})
            idx = pos + 1

    return results


def print_analysis(results: Dict, title: str = "Analysis"):
    """Print analysis results in a readable format."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)

    for key, value in results.items():
        if key == "planets_data":
            print(f"\n--- Planet Data ---")
            for planet in value:
                valid_str = "✓" if planet["biome"]["valid"] and planet["size"]["valid"] else "✗"
                moon_str = " [MOON]" if planet["size"]["is_moon"] else ""
                print(f"  Planet {planet['index']} @ {planet['base_offset']}: "
                      f"{planet['biome']['mapped']}, {planet['size']['mapped']}{moon_str} "
                      f"({planet['common_resource']}/{planet['rare_resource']}) {valid_str}")
        elif isinstance(value, dict) and "valid" in value:
            valid_str = "✓" if value["valid"] else "✗"
            mapped = value.get("mapped", value.get("value", "?"))
            print(f"{key}: {mapped} (raw: {value.get('raw', value.get('value', '?'))}) @ {value['offset']} {valid_str}")
        elif isinstance(value, list) and len(value) > 0:
            print(f"\n{key}: {len(value)} candidates found")
            for item in value[:5]:  # Show first 5
                print(f"  {item}")
        else:
            print(f"{key}: {value}")


def interactive_mode():
    """Interactive analysis mode."""
    print("Memory Dump Analyzer - Interactive Mode")
    print("=" * 40)

    # Find dump files
    dump_dir = Path.home() / "Documents" / "Haven-Extractor" / "offset_scans"
    if dump_dir.exists():
        bin_files = list(dump_dir.glob("*.bin"))
        if bin_files:
            print(f"\nFound {len(bin_files)} dump files in {dump_dir}:")
            for i, f in enumerate(bin_files[:10]):
                print(f"  {i+1}. {f.name}")

            choice = input("\nEnter file number (or path to .bin file): ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(bin_files):
                    return str(bin_files[idx])
            except ValueError:
                return choice
    else:
        print(f"\nNo dump directory found at {dump_dir}")
        return input("Enter path to .bin file: ").strip()


def main():
    if len(sys.argv) > 1:
        dump_file = sys.argv[1]
    else:
        dump_file = interactive_mode()

    if not dump_file:
        print("No file specified")
        return

    dump_path = Path(dump_file)
    if not dump_path.exists():
        print(f"File not found: {dump_path}")
        return

    print(f"\nLoading: {dump_path}")
    with open(dump_path, 'rb') as f:
        data = f.read()

    print(f"Loaded {len(data)} bytes")

    # Analyze at known offsets
    print("\n" + "=" * 60)
    print("ANALYSIS AT KNOWN 4.13 OFFSETS")
    print("=" * 60)
    known_results = analyze_at_known_offsets(data)
    print_analysis(known_results, "Known Offset Analysis")

    # Check if results look valid
    valid_count = sum(1 for k, v in known_results.items()
                      if isinstance(v, dict) and v.get("valid", False))
    total_count = sum(1 for k, v in known_results.items()
                      if isinstance(v, dict) and "valid" in v)

    print(f"\nValid fields: {valid_count}/{total_count}")

    if valid_count < total_count // 2:
        print("\n⚠️  Many fields show invalid values!")
        print("The offsets may have shifted in your game version.")
        print("Running pattern scan to find candidates...\n")

        # Run pattern scan
        pattern_results = scan_for_patterns(data, 0x2500)

        # Show trading candidates (most distinctive)
        print("\n--- Trading+Wealth Pattern Candidates ---")
        print("(Look for pairs that match what you see in-game)")
        for r in pattern_results["trading_candidates"][:10]:
            print(f"  @ {r['offset']}: Trading={r['trading_mapped']}, Wealth={r['wealth_mapped']}")

    # Save results
    output_file = dump_path.with_suffix('.analysis.txt')
    with open(output_file, 'w') as f:
        import json
        f.write("Known Offset Analysis:\n")
        f.write(json.dumps(known_results, indent=2, default=str))

    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
