from nms_namegen.generator import generateName
from nms_namegen.prng import PRNG
from nms_namegen.system import planetSeeds
from nms_namegen.roman import toRoman
import numpy as np

adornments = [
    "Prime",
    "Major",
    "Minor",
    "Alpha",
    "Beta",
    "Gamma",
    "Delta",
    "Omega",
    "Sigma",
    "Tau",
]

styles = [
    "%PROCNORM%",
    "%PROCNORM% %ADORNMENT%",
    "%PROCNORM% %NUMERAL%",
    "%PROCNORM% %SHORTCODE%",
    "%PROCLONG% %PROCSHORT%",
    "%PROCSHORT% %LONGCODE%",
    "%PROCNORM%",
    "%PROCNORM% %ADORNMENT%",
    "%PROCNORM% %NUMERAL%",
    "Style 9",
    "New %PROCNORM%"
]

TINY_DOUBLE = np.double(2.3283064370807974e-10)


def planetSeed(portal_code, galaxy):
    planet_id = (portal_code & 0xF00000000000) >> 44
    return planetSeeds(portal_code, galaxy)["planet_seeds"][planet_id - 1]


def format_longcode(longcode, digit, alpha):
    return f"{longcode}/{bytes([alpha]).decode('ascii')}{digit}"


def format_shortcode(alpha, num):
    return f"{bytes([alpha]).decode('ascii')}{num}"


# Returns a planet name given a planet seed.
def planetName(planet_seed_or_code, galaxy=None):
    planet_seed = planet_seed_or_code
    if galaxy is not None:
        planet_seed = planetSeed(planet_seed_or_code, galaxy)

    lowword = planet_seed & 0xFFFFFFFF
    highword = planet_seed >> 32

    rol16 = ((lowword & 0x0000FFFF) << 16) | ((lowword & 0xFFFF0000) >> 16)
    rol16 = (rol16 ^ lowword ^ highword) & 0xFFFFFFFF

    seed = 0
    if lowword == 0:
        seed = ((lowword + 1) * PRNG.MULTIPLIER) + rol16
    else:
        seed = (lowword * PRNG.MULTIPLIER) + rol16

    rng = PRNG(seed)
    adornment = ((rng.seed & 0xFFFFFFFF) * 10) >> 0x20
    # print(f"adornment: {hex(adornment)}")
    code = rng.random(50) + 1
    shortcode = rng.random(0x1A) + 0x41
    # print(f"shortcode: ", bytes([shortcode]).decode("ascii"), hex(shortcode))
    numeral = rng.random(0x12) + 2
    # print(f"numeral: {hex(numeral)}")
    digit = rng.random(0x09) + 1
    # print(f"digit: {hex(digit)}")
    alpha = rng.random(0x1A) + 0x41
    # print(f"alpha: {bytes([alpha]).decode("ascii")} {alpha}")
    longcode = rng.random(0x59) + 0xB
    # print(f"longcode: {hex(longcode)} {longcode}")

    procnorm = generateName(rng, 7, 4, 8)
    procshort = generateName(rng, 5, 4, 5)
    proclong = generateName(rng, 7, 6, 10)
    # print(procnorm, procshort, proclong)

    namegen_style = rng.random(9)
    # print(f"namegen Style: {hex(namegen_style)} {styles[namegen_style]}")

    target = np.double(rng.randi()) * TINY_DOUBLE
    if not (np.double(0.0350000001) <= target):
        namegen_style = 10
        # Do something?

    name = styles[namegen_style]

    name = name.replace("%PROCNORM%", procnorm.capitalize())
    name = name.replace("%PROCSHORT%", procshort.capitalize())
    name = name.replace("%PROCLONG%", proclong.capitalize())
    name = name.replace("%ADORNMENT%", adornments[adornment])
    name = name.replace("%SHORTCODE%", format_shortcode(shortcode, code % 0x50))
    name = name.replace("%NUMERAL%", toRoman(numeral))
    name = name.replace("%LONGCODE%", format_longcode(longcode, digit, alpha))

    return name
