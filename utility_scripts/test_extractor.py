#!/usr/bin/env python3
"""
Test script for Haven Extractor.

This script tests the extractor components without needing NMS running.
It verifies:
1. Struct definitions work correctly
2. Coordinate/glyph conversion works
3. Extraction watcher can read files
4. Sample data generation for testing

Usage:
    python test_extractor.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime

# Fix Windows console encoding for checkmarks
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from structs import (
    ExtractedSystemData,
    ExtractedPlanetData,
    coordinates_to_glyphs,
    glyphs_to_coordinates,
    get_galaxy_name,
)
from extraction_watcher import ExtractionWatcher, convert_extraction_to_haven_payload


def test_structs():
    """Test data structure creation and serialization."""
    print("\n" + "=" * 60)
    print("TEST: Data Structures")
    print("=" * 60)

    # Create sample planet data
    planet1 = ExtractedPlanetData(
        planet_index=0,
        planet_name="Paradise Moon",
        biome="Lush",
        weather="Humid",
        sentinel_level="Low",
        flora_level="Bountiful",
        fauna_level="Generous",
        common_resource="Ferrite Dust",
        uncommon_resource="Sodium",
        rare_resource="Activated Copper",
        has_water=True,
        has_caves=True
    )

    planet2 = ExtractedPlanetData(
        planet_index=1,
        planet_name="Scorched Desert",
        biome="Scorched",
        weather="Superheated Storms",
        sentinel_level="High",
        flora_level="Low",
        fauna_level="Sparse",
        common_resource="Ferrite Dust",
        uncommon_resource="Phosphorus",
        rare_resource="Gold",
        has_water=False,
        has_caves=True
    )

    # Create sample system data
    system = ExtractedSystemData(
        system_name="Test System Alpha",
        galaxy_name="Euclid",
        galaxy_index=0,
        glyph_code="0123456789AB",
        voxel_x=100,
        voxel_y=50,
        voxel_z=-200,
        solar_system_index=56,
        star_type="Yellow",
        economy_type="Mining",
        economy_strength="High",
        conflict_level="Low",
        dominant_lifeform="Gek",
        planet_count=2,
        planets=[planet1, planet2],
        discoverer_name="TestPlayer",
        discovery_timestamp=int(datetime.now().timestamp())
    )

    # Test serialization
    print("\nSystem Data (JSON):")
    print(system.to_json())

    # Verify dict conversion works
    data_dict = system.to_dict()
    assert data_dict['system_name'] == "Test System Alpha"
    assert len(data_dict['planets']) == 2
    assert data_dict['planets'][0]['biome'] == "Lush"

    print("\n✓ Structs test PASSED")
    return system


def test_coordinate_conversion():
    """Test glyph/coordinate conversions."""
    print("\n" + "=" * 60)
    print("TEST: Coordinate/Glyph Conversion")
    print("=" * 60)

    # Test cases: (planet_index, ssi, voxel_x, voxel_y, voxel_z)
    test_cases = [
        (0, 0, 0, 0, 0),              # Origin
        (0, 1, 2046, 127, 2046),      # Max values
        (15, 4095, -2047, -128, -2047), # Min values
        (1, 56, 100, 50, -200),       # Typical values
        (3, 123, -500, -10, 750),     # Mixed values
    ]

    all_passed = True
    for planet_idx, ssi, vx, vy, vz in test_cases:
        # Convert to glyphs
        glyphs = coordinates_to_glyphs(planet_idx, ssi, vx, vy, vz)
        print(f"\nCoords: P={planet_idx}, SSI={ssi}, X={vx}, Y={vy}, Z={vz}")
        print(f"  -> Glyphs: {glyphs}")

        # Convert back
        result = glyphs_to_coordinates(glyphs)
        print(f"  <- Back: P={result['planet_index']}, SSI={result['solar_system_index']}, "
              f"X={result['voxel_x']}, Y={result['voxel_y']}, Z={result['voxel_z']}")

        # Verify round-trip
        if (result['planet_index'] == planet_idx and
            result['solar_system_index'] == ssi and
            result['voxel_x'] == vx and
            result['voxel_y'] == vy and
            result['voxel_z'] == vz):
            print("  ✓ Round-trip OK")
        else:
            print("  ✗ Round-trip FAILED")
            all_passed = False

    if all_passed:
        print("\n✓ Coordinate conversion test PASSED")
    else:
        print("\n✗ Coordinate conversion test FAILED")

    return all_passed


def test_galaxy_names():
    """Test galaxy name lookup."""
    print("\n" + "=" * 60)
    print("TEST: Galaxy Names")
    print("=" * 60)

    test_galaxies = [0, 1, 2, 9, 255]
    for idx in test_galaxies:
        name = get_galaxy_name(idx)
        print(f"  Galaxy {idx}: {name}")

    print("\n✓ Galaxy names test PASSED")
    return True


def test_extraction_watcher():
    """Test the extraction file watcher."""
    print("\n" + "=" * 60)
    print("TEST: Extraction Watcher")
    print("=" * 60)

    # Create a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        print(f"  Using temp dir: {tmpdir}")

        # Create watcher
        received_data = []

        def on_extraction(data):
            received_data.append(data)
            print(f"  Received extraction: {data.get('system_name', 'Unknown')}")

        watcher = ExtractionWatcher(
            output_dir=tmpdir,
            callback=on_extraction,
            poll_interval=0.5
        )

        # Write a test extraction file
        test_extraction = {
            "system_name": "Watcher Test System",
            "galaxy_name": "Euclid",
            "star_type": "Blue",
            "economy_type": "Technology",
            "economy_strength": "High",
            "conflict_level": "Medium",
            "dominant_lifeform": "Korvax",
            "planet_count": 3,
            "planets": [
                {"planet_index": 0, "planet_name": "Test Planet 1", "biome": "Lush"},
                {"planet_index": 1, "planet_name": "Test Planet 2", "biome": "Toxic"},
                {"planet_index": 2, "planet_name": "Test Planet 3", "biome": "Frozen"},
            ]
        }

        # Write to latest.json
        latest_path = tmpdir / "latest.json"
        with open(latest_path, 'w') as f:
            json.dump(test_extraction, f)
        print(f"  Wrote test file: {latest_path}")

        # Check once (without starting background thread)
        result = watcher.check_once()

        if result:
            print(f"  Check returned: {result.get('system_name', 'Unknown')}")

            # Verify data
            assert result['system_name'] == "Watcher Test System"
            assert result['star_type'] == "Blue"
            assert len(result['planets']) == 3

            print("\n✓ Extraction watcher test PASSED")
            return True
        else:
            print("\n✗ Extraction watcher test FAILED - no data returned")
            return False


def test_haven_payload_conversion():
    """Test conversion to Haven API payload format."""
    print("\n" + "=" * 60)
    print("TEST: Haven Payload Conversion")
    print("=" * 60)

    extraction = {
        "system_name": "API Test System",
        "galaxy_name": "Hilbert Dimension",
        "glyph_code": "0123456789AB",
        "star_type": "Red",
        "economy_type": "Mining",
        "economy_strength": "Low",
        "conflict_level": "High",
        "dominant_lifeform": "Vy'keen",
        "voxel_x": 100,
        "voxel_y": -50,
        "voxel_z": 200,
        "solar_system_index": 123,
        "planet_count": 2,
        "planets": [
            {
                "planet_index": 0,
                "planet_name": "Warrior's Rest",
                "biome": "Barren",
                "weather": "Dusty",
                "sentinel_level": "Aggressive",
                "flora_level": "None",
                "fauna_level": "Low",
                "common_resource": "Rusted Metal",
                "uncommon_resource": "Sodium Nitrate",
                "rare_resource": "Platinum"
            }
        ]
    }

    payload = convert_extraction_to_haven_payload(extraction)

    print("\nInput extraction:")
    print(json.dumps(extraction, indent=2))

    print("\nConverted Haven payload:")
    print(json.dumps(payload, indent=2))

    # Verify conversion
    assert payload['name'] == "API Test System"
    assert payload['galaxy'] == "Hilbert Dimension"
    assert payload['star_type'] == "Red"
    assert payload['dominant_lifeform'] == "Vy'keen"
    assert len(payload['planets']) == 1
    assert payload['planets'][0]['name'] == "Warrior's Rest"

    print("\n✓ Haven payload conversion test PASSED")
    return True


def create_sample_extraction_file():
    """Create a sample extraction file for manual testing."""
    print("\n" + "=" * 60)
    print("Creating Sample Extraction File")
    print("=" * 60)

    # Default output directory
    output_dir = Path.home() / "Documents" / "Haven-Extractor"
    output_dir.mkdir(parents=True, exist_ok=True)

    sample_system = ExtractedSystemData(
        system_name="Sample Paradise System",
        galaxy_name="Euclid",
        galaxy_index=0,
        glyph_code="0038045A0123",
        voxel_x=291,
        voxel_y=5,
        voxel_z=90,
        solar_system_index=56,
        star_type="Yellow",
        economy_type="Trading",
        economy_strength="High",
        conflict_level="Low",
        dominant_lifeform="Gek",
        planet_count=4,
        planets=[
            ExtractedPlanetData(
                planet_index=0,
                planet_name="Paradise Prime",
                biome="Lush",
                weather="Temperate",
                sentinel_level="Low",
                flora_level="Bountiful",
                fauna_level="High",
                common_resource="Ferrite Dust",
                uncommon_resource="Sodium",
                rare_resource="Activated Indium",
                has_water=True,
                has_caves=True
            ),
            ExtractedPlanetData(
                planet_index=1,
                planet_name="Frozen Outpost",
                biome="Frozen",
                weather="Blizzards",
                sentinel_level="Standard",
                flora_level="Low",
                fauna_level="Sparse",
                common_resource="Ferrite Dust",
                uncommon_resource="Dioxite",
                rare_resource="Emeril",
                has_water=False,
                has_caves=True
            ),
            ExtractedPlanetData(
                planet_index=2,
                planet_name="Toxic Wastes",
                biome="Toxic",
                weather="Caustic Fog",
                sentinel_level="High",
                flora_level="Average",
                fauna_level="Low",
                common_resource="Ferrite Dust",
                uncommon_resource="Ammonia",
                rare_resource="Cadmium",
                has_water=False,
                has_caves=True
            ),
            ExtractedPlanetData(
                planet_index=3,
                planet_name="Exotic Anomaly",
                biome="Exotic",
                weather="Extreme Wind",
                sentinel_level="None",
                flora_level="Generous",
                fauna_level="None",
                common_resource="Rusted Metal",
                uncommon_resource="Ferrite Dust",
                rare_resource="Activated Copper",
                has_water=False,
                has_caves=False
            ),
        ],
        discoverer_name="TestVoyager",
        discovery_timestamp=int(datetime.now().timestamp())
    )

    # Write to latest.json
    latest_path = output_dir / "latest.json"
    with open(latest_path, 'w') as f:
        json.dump(sample_system.to_dict(), f, indent=2)

    # Also write a timestamped file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    extraction_path = output_dir / f"extraction_{timestamp}.json"
    with open(extraction_path, 'w') as f:
        json.dump(sample_system.to_dict(), f, indent=2)

    print(f"\n  Output directory: {output_dir}")
    print(f"  Latest file: {latest_path}")
    print(f"  Extraction file: {extraction_path}")
    print("\n  You can now test the Haven Watcher with live_extraction enabled!")

    return output_dir


def main():
    """Run all tests."""
    print("=" * 60)
    print("Haven Extractor Test Suite")
    print("=" * 60)

    results = []

    # Run tests
    try:
        test_structs()
        results.append(("Structs", True))
    except Exception as e:
        print(f"\n✗ Structs test FAILED: {e}")
        results.append(("Structs", False))

    try:
        passed = test_coordinate_conversion()
        results.append(("Coordinate Conversion", passed))
    except Exception as e:
        print(f"\n✗ Coordinate conversion test FAILED: {e}")
        results.append(("Coordinate Conversion", False))

    try:
        passed = test_galaxy_names()
        results.append(("Galaxy Names", passed))
    except Exception as e:
        print(f"\n✗ Galaxy names test FAILED: {e}")
        results.append(("Galaxy Names", False))

    try:
        passed = test_extraction_watcher()
        results.append(("Extraction Watcher", passed))
    except Exception as e:
        print(f"\n✗ Extraction watcher test FAILED: {e}")
        results.append(("Extraction Watcher", False))

    try:
        passed = test_haven_payload_conversion()
        results.append(("Haven Payload Conversion", passed))
    except Exception as e:
        print(f"\n✗ Haven payload conversion test FAILED: {e}")
        results.append(("Haven Payload Conversion", False))

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\nAll tests PASSED!")

        # Create sample file for manual testing
        create_sample_extraction_file()
    else:
        print("\nSome tests FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    main()
