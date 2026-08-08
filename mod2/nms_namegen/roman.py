ROMAN_NUMERALS = ["I", "II", "III", "IV", "V", "VI", "VII",
 "VIII", "IX", "X", "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]

def toRoman(numeral):
    return ROMAN_NUMERALS[int(numeral) - 1]
