"""
Custom struct definitions for Haven Extractor.

These extend or reference the structs defined in nmspy.data.
Most structs are already available via nmspy.data.exported_types (as nmse).
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, List
from enum import Enum
import json


class StarType(Enum):
    """Star types in NMS."""
    YELLOW = "Yellow"
    RED = "Red"
    GREEN = "Green"
    BLUE = "Blue"
    UNKNOWN = "Unknown"


class ConflictLevel(Enum):
    """Conflict levels."""
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    UNKNOWN = "Unknown"


class EconomyStrength(Enum):
    """Economy strength levels."""
    LOW = "Low"        # Struggling, Declining, etc.
    MEDIUM = "Medium"  # Adequate, Balanced, etc.
    HIGH = "High"      # Wealthy, Booming, etc.
    UNKNOWN = "Unknown"


class DominantLifeform(Enum):
    """Dominant alien species."""
    GEK = "Gek"
    KORVAX = "Korvax"
    VYKEEN = "Vy'keen"
    NONE = "None"      # Uninhabited
    UNKNOWN = "Unknown"


class BiomeType(Enum):
    """Planet biome types."""
    LUSH = "Lush"
    BARREN = "Barren"
    DEAD = "Dead"
    TOXIC = "Toxic"
    SCORCHED = "Scorched"
    FROZEN = "Frozen"
    RADIOACTIVE = "Radioactive"
    SWAMP = "Swamp"
    EXOTIC = "Exotic"
    ANOMALY = "Anomaly"
    UNKNOWN = "Unknown"


class SentinelLevel(Enum):
    """Sentinel activity levels."""
    NONE = "None"
    LOW = "Low"
    STANDARD = "Standard"
    HIGH = "High"
    AGGRESSIVE = "Aggressive"
    UNKNOWN = "Unknown"


class FloraFaunaLevel(Enum):
    """Flora and fauna abundance levels."""
    NONE = "None"
    SPARSE = "Sparse"
    LOW = "Low"
    AVERAGE = "Average"
    GENEROUS = "Generous"
    HIGH = "High"
    BOUNTIFUL = "Bountiful"
    UNKNOWN = "Unknown"


@dataclass
class ExtractedPlanetData:
    """Planet data extracted from game memory."""

    # Identification
    planet_index: int = 0
    planet_name: str = ""

    # Environment
    biome: str = "Unknown"
    weather: str = "Unknown"
    sentinel_level: str = "Unknown"

    # Life
    flora_level: str = "Unknown"
    fauna_level: str = "Unknown"

    # Resources
    common_resource: str = ""
    uncommon_resource: str = ""
    rare_resource: str = ""

    # Extras
    has_water: bool = False
    has_caves: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class ExtractedSystemData:
    """Solar system data extracted from game memory."""

    # Identification
    system_name: str = ""
    galaxy_name: str = "Euclid"
    galaxy_index: int = 0

    # Portal address
    glyph_code: str = ""

    # Coordinates
    voxel_x: int = 0
    voxel_y: int = 0
    voxel_z: int = 0
    solar_system_index: int = 0

    # Properties
    star_type: str = "Unknown"
    economy_type: str = "Unknown"
    economy_strength: str = "Unknown"
    conflict_level: str = "Unknown"
    dominant_lifeform: str = "Unknown"

    # Number of planets
    planet_count: int = 0

    # Planets in this system
    planets: List[ExtractedPlanetData] = field(default_factory=list)

    # Discoverer info (if available)
    discoverer_name: str = ""
    discovery_timestamp: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data['planets'] = [p.to_dict() if isinstance(p, ExtractedPlanetData) else p for p in self.planets]
        return data

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# Galaxy name mapping (index to name)
GALAXY_NAMES = {
    0: "Euclid",
    1: "Hilbert Dimension",
    2: "Calypso",
    3: "Hesperius Dimension",
    4: "Hyades",
    5: "Ickjamatew",
    6: "Budullangr",
    7: "Kikolgallr",
    8: "Eltiensleen",
    9: "Eissentam",
    # ... more galaxies exist but these are the most common
}


def get_galaxy_name(index: int) -> str:
    """Get galaxy name from index."""
    return GALAXY_NAMES.get(index, f"Galaxy {index}")


# Glyph characters for portal addresses
GLYPHS = "0123456789ABCDEF"


def coordinates_to_glyphs(
    planet_index: int,
    solar_system_index: int,
    voxel_x: int,
    voxel_y: int,
    voxel_z: int
) -> str:
    """
    Convert coordinates to portal glyph code.

    Portal address format (12 glyphs):
    - Glyph 1: Planet index (0-F)
    - Glyphs 2-4: Solar system index (000-FFF)
    - Glyphs 5-6: VoxelY component (00-FF, signed)
    - Glyphs 7-9: VoxelZ component (000-FFF, with offset)
    - Glyphs 10-12: VoxelX component (000-FFF, with offset)
    """
    # Planet index (1 hex digit, 0-15)
    p = planet_index & 0xF

    # Solar system index (3 hex digits, 0-4095)
    ssi = solar_system_index & 0xFFF

    # Voxel Y (2 hex digits, signed byte)
    # Y is stored as signed, convert to unsigned for display
    y = (voxel_y + 0x80) & 0xFF

    # Voxel X and Z (3 hex digits each, with 0x800 offset for center)
    x = (voxel_x + 0x801) & 0xFFF
    z = (voxel_z + 0x801) & 0xFFF

    # Build the glyph code
    glyph_code = f"{p:01X}{ssi:03X}{y:02X}{z:03X}{x:03X}"

    return glyph_code


def glyphs_to_coordinates(glyph_code: str) -> dict:
    """
    Convert portal glyph code back to coordinates.

    Returns dict with planet_index, solar_system_index, voxel_x, voxel_y, voxel_z
    """
    if len(glyph_code) != 12:
        raise ValueError(f"Glyph code must be 12 characters, got {len(glyph_code)}")

    # Parse hex values
    p = int(glyph_code[0], 16)
    ssi = int(glyph_code[1:4], 16)
    y = int(glyph_code[4:6], 16)
    z = int(glyph_code[6:9], 16)
    x = int(glyph_code[9:12], 16)

    # Convert back to signed coordinates
    voxel_y = y - 0x80
    voxel_x = x - 0x801
    voxel_z = z - 0x801

    return {
        "planet_index": p,
        "solar_system_index": ssi,
        "voxel_x": voxel_x,
        "voxel_y": voxel_y,
        "voxel_z": voxel_z
    }
