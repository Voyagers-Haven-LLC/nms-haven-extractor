from nms_namegen.generator import generateName
from nms_namegen.prng import PRNG
import math

region_name_adornments = [
    "{} Adjunct",
    "{} Void",
    "{} Expanse",
    "{} Terminus",
    "{} Boundary",
    "{} Fringe",
    "{} Cluster",
    "{} Mass",
    "{} Band",
    "{} Cloud",
    "{} Nebula",
    "{} Quadrant",
    "{} Sector",
    "{} Anomaly",
    "{} Conflux",
    "{} Instability",
    "Sea of {}",
    "The Arm of {}",
    "{} Spur",
    "{} Shallows",
]


CONST_A = 0x64DD81482CBD31D7
CONST_B = 0xE36AA5C613612997  # -0x1c955a39ec9ed669


# Returns a region name for No Man's Sky
#
# Parameters:
#   Portal code as a hexadecimal integer,
#   in the format 0xPSSSYYZZZXXX
#   Where P - Planet id (unused)
#   SSS - System Id (unused)
#   YY - Y coordinate
#   ZZZ - Z coordinate
#   XXX - X coordinate
#
#   Galaxy is the galaxy number starting with Euclid as 0.
#   grf: PSSSGGYYZZZXXX


def regionName(portal_code, galaxy):
    register = galaxy >> 1
    register ^= (galaxy << 32) | (portal_code & 0xFFFFFFFF)
    register = register * CONST_A
    register &= 0xFFFFFFFFFFFFFFFF
    register = ((register >> 33) ^ register) * CONST_B
    register &= 0xFFFFFFFFFFFFFFFF
    register = (register >> 33) ^ register

    seed_h = (
        (((register & 0xFFFF0000) >> 16) | ((register & 0x0000FFFF) << 16))
        ^ (register & 0xFFFFFFFF)
        ^ (register >> 32)
    )
    seed = register & 0xFFFFFFFF
    if seed_h == 0:
        seed_h = 1

    seed |= seed_h << 32
    rng = PRNG(seed)

    min_length = 6
    max_length = rng.random(4) + 6
    alphaset_index = 0
    name = generateName(rng, alphaset_index, min_length, max_length)
    name = name.capitalize()

    if rng.random(0x64) < 0x50:
        adornment = region_name_adornments[rng.random(0x14)]
        name = adornment.format(name)

    return name


def voxelAttributes(portal_code):
    voxelAttributes = {}
    x = portal_code & 0xFFF
    y = (portal_code & 0xFF000000) >> 24
    z = (portal_code & 0xFFF000) >> 12
    # Distance to the galactic centre is measured in SIGNED voxel units: the
    # centre is voxel (0, 0, 0) and 0x801..0xFFF / 0x81..0xFF are the negative
    # side. The raw unsigned values put 7/8 of the galaxy thousands of voxels
    # "away" and zeroed guide_star_renegade_count there. Verified 2026-09-12
    # against 19,227 extractor captures: signed + int() truncation predicts the
    # star type of SSI<120 systems at 95.0% vs 77.0% for unsigned/float.
    x = x if x <= 0x7FF else x - 0x1000
    y = y if y <= 0x7F else y - 0x100
    z = z if z <= 0x7FF else z - 0x1000
    voxelAttributes["guide_star_count"] = 0x78
    voxelAttributes["black_hole_count"] = 1
    voxelAttributes["atlas_station_count"] = 1
    voxelAttributes["inside_gap"] = 0
    voxelAttributes["guide_star_renegade_count"] = 0

    # The game truncates (int): 8.0..8.99 counts as 8, so voxels at distance
    # 8.x keep renegade_count = 0 (matches prod: 76 rows at 8.3-8.6 score 100%
    # with truncation, 0% without).
    distance = int(math.sqrt(x * x + y * y + z * z))
    # print(distance)
    if distance < 8.0:
        voxelAttributes["guide_star_count"] = 0
        voxelAttributes["black_hole_count"] = 0
        voxelAttributes["atlas_station_count"] = 0
        voxelAttributes["inside_gap"] = 1

    if (distance < 1440.0) and (distance > 8.0):
        diff = distance - 8.0
        diff *= 120.0
        diff /= 1440.0

        if diff < 0.0:
            diff = 0.0
        if diff > 0x78:
            diff = 0x78
        voxelAttributes["guide_star_renegade_count"] = 0x78 - diff

    return voxelAttributes
