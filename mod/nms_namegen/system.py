from nms_namegen.generator import generateName
from nms_namegen.prng import PRNG
from nms_namegen.iprng import indexPrimedPRNG
from nms_namegen.region import voxelAttributes
from nms_namegen.roman import toRoman
import numpy as np

TINY_DOUBLE = np.double(2.3283064370807974e-10)

CONST_A = 0x64DD81482CBD31D7
CONST_B = 0xE36AA5C613612997


# Returns a system name for No Man's Sky
#
# Parameters:
#   Portal code as a hexadecimal integer,
#   in the format 0xPSSSYYZZZXXX
#   Where P - Planet id (unused)
#   SSS - System Id
#   YY - Y coordinate
#   ZZZ - Z coordinate
#   XXX - X coordinate
#
#   Galaxy is the galaxy number starting with Euclid as 0.
#
def systemName(portal_code, galaxy):
    galacticCoords = portal_code & 0xFFFFFFFF
    systemIndex = ((portal_code & 0x0FFF00000000) >> 24) | galaxy

    rolCoords = ((galacticCoords & 0x0000FFFF) << 16) | (
        (galacticCoords & 0xFFFF0000) >> 16
    )
    rolCoords = (rolCoords ^ galacticCoords ^ systemIndex) & 0xFFFFFFFF

    seed = 0
    if galacticCoords == 0:
        seed = ((galacticCoords + 1) * PRNG.MULTIPLIER) + rolCoords
    else:
        seed = (galacticCoords * PRNG.MULTIPLIER) + rolCoords

    rng = PRNG(seed)

    alphaset_index = 0x00
    alphaset_reg = ((seed & 0xFFFFFFFF) * 0x5) >> 32
    if alphaset_reg == 0:
        alphaset_index = 0x02
    else:
        if (alphaset_reg & 0xFF) < 0x04:
            alphaset_index = (alphaset_reg & 0xFF) + 0x02
        else:
            alphaset_index = 0x07

    max_length = rng.random(4) + 0x06
    name = generateName(rng, alphaset_index, 6, max_length)
    name = name.capitalize()

    # Hyphenated names
    if len(name) < 8:
        r = rng.random(4)
        if r < 2:
            alphaset_reg = rng.random(5)
            min_length = 3
            max_length = 5

            if alphaset_reg == 0:
                alphaset_index = 0x02
            else:
                if alphaset_reg < 4:
                    alphaset_index = alphaset_reg + 2
                else:
                    alphaset_index = 0x07

            name2 = generateName(rng, alphaset_index, min_length, max_length)
            name = f"{name}-{name2.capitalize()}"

    # Might have to tune the rng parameter here.
    # I'm not sure where the cutoff actually as yet
    if rng.random(0x0A) < 0x03:
        n = (rng.random(19)) + 1
        if n > 19:
            n == 19

        name = f"{name} {toRoman(n)}"
    return name


# Returns a dictionary containing planet counts for a system.
def systemAttributes(portal_code, galaxy):
    portal_code = portal_code & 0xFFFFFFFFFFF
    system_id = (portal_code & 0xFFF00000000) >> 32
    universalAddress = (((system_id << 8) | (galaxy & 0xFF)) << 32) | (
        portal_code & 0xFFFFFFFF
    )
    # print("UA:", hex(universalAddress))
    va = voxelAttributes(portal_code)
    system_seed = indexPrimedPRNG(universalAddress) & 0xFFFFFFFF
    # print("system seed: ", hex(system_seed))
    rol16 = ((system_seed & 0x0000FFFF) << 16) | ((system_seed & 0xFFFF0000) >> 16)
    rol16 = (rol16 ^ system_seed) & 0xFFFFFFFF
    seed = 0
    if system_seed == 0:
        seed = ((system_seed + 1) * PRNG.MULTIPLIER) + rol16
    else:
        seed = (system_seed * PRNG.MULTIPLIER) + rol16

    rng = PRNG(seed)
    star_type = 0
    safe_start = 0
    prime_planet_count = 2
    planet_count = 1

    if system_id < va["guide_star_count"]:
        planet_count = (((seed & 0xFFFFFFFF) * 4) >> 0x20) + 3
        safe_start = rng.random(planet_count) + 1
    else:
        star_type = 0

        if (((seed & 0xFFFFFFFF) * 0x64) >> 0x20) < 0x1E:
            star_type = rng.random(3) + 1

        planet_count = rng.random(6) + 1

        if va["guide_star_renegade_count"] >= 10 or star_type != 0:
            safe_start = 0
        else:
            safe_start = rng.random(planet_count + 2)

    rng._updateSeed()
    rng._updateSeed()
    rng._updateSeed()
    rng._updateSeed()

    if system_id < va["guide_star_renegade_count"]:
        star_type = rng.random(3) + 1

    if system_id > 0x3E9 and system_id < 0x429:  # Purple
        star_type = 4

    rng._updateSeed()
    rng._updateSeed()

    diff = 6 - planet_count
    if diff < 1:
        prime_planet_count = 0
    elif (rng.random(100) >= 33) or diff < 2:
        prime_planet_count = 1

    unknown_attribute2 = 0
    if star_type == 4:
        planet_count = 0

        if rng.random(100) > 0xF:
            if rng.random(100) < 0x42:
                unknown_attribute2 = 1

    if unknown_attribute2:
        planet_count = 0
        star_type = 4
        prime_planet_count = 6

    return {
        "planet_count": planet_count,
        "prime_planet_count": prime_planet_count,
        "safe_start_planet": safe_start,
    }

def planetSeeds(portal_code, galaxy):
    system_attributes = systemAttributes(portal_code, galaxy)

    planet_seeds = []
    galacticCoords = portal_code & 0xFFFFFFFF
    systemIndex = ((portal_code & 0x0FFF00000000) >> 24) | galaxy
    moon_count = 0

    register = (systemIndex << 0x20) | galacticCoords
    register = ((register >> 33) ^ register) * CONST_A
    register &= 0xFFFFFFFFFFFFFFFF
    register = ((register >> 33) ^ register) * CONST_B
    register &= 0xFFFFFFFFFFFFFFFF
    register = (register >> 33) ^ register

    seed_h = (
        (((register & 0xFFFF0000) >> 16) | ((register & 0x0000FFFF) << 16))
        ^ (register & 0xFFFFFFFF)
        ^ (register >> 32)
    )
    seed_l = register & 0xFFFFFFFF
    if seed_h == 0:
        seed_h = 1

    seed = seed_h << 32 | seed_l
    rng = PRNG(seed)

    i = 0
    planet_count = 0

    while i < system_attributes["planet_count"]:
        i += 1
        size = rng.random(3)
        planet_count += 1

        if size == 0:
            # This is a large planet
            # Add 1 or 2 moons.
            m = system_attributes["planet_count"] - i
            if m < 0:
                m = 0
            if m > 2:
                m = 2

            n_moons = rng.random(m + 1)
            moon_count = n_moons
            if n_moons > 0:
                while i != system_attributes["safe_start_planet"] - 1:
                    i += 1
                    planet_count += 1
                    n_moons -= 1
                    if n_moons <= 0:
                        break

    i = 0
    while i < system_attributes["planet_count"]:
        low = rng.randi() & 0xFFFFFFFF
        high = rng.randi() & 0xFFFFFFFF

        register = (high << 0x20) | low
        register = ((register >> 33) ^ register) * CONST_A
        register &= 0xFFFFFFFFFFFFFFFF
        register = ((register >> 33) ^ register) * CONST_B
        register &= 0xFFFFFFFFFFFFFFFF
        p_seed = (register >> 33) ^ register
        planet_seeds.append(p_seed)
        i += 1

    # Extra planet(s)
    rng._updateSeed()

    while i < (
        system_attributes["planet_count"] + system_attributes["prime_planet_count"]
    ):

        low = rng.randi() & 0xFFFFFFFF
        high = rng.randi() & 0xFFFFFFFF
        register = (high << 0x20) | low
        register = ((register >> 33) ^ register) * CONST_A
        register &= 0xFFFFFFFFFFFFFFFF
        register = ((register >> 33) ^ register) * CONST_B
        register &= 0xFFFFFFFFFFFFFFFF
        p_seed = (register >> 33) ^ register
        planet_seeds.append(p_seed)

        size = rng.random(3)
        if(size != 0):
            rng._updateSeed()
        i += 1

    planet_count = system_attributes["planet_count"] + system_attributes["prime_planet_count"] - moon_count
    # print(list(map(lambda x: hex(x), planet_seeds)))
    return {"planet_seeds": planet_seeds, "planet_count": planet_count, "moon_count": moon_count}
