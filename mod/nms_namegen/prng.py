# Pseudorandom number generator for NMS Namegen
class PRNG:
    MULTIPLIER = 0x5A76F899

    def __init__(self, seed):
        self.seed = seed

    def _updateSeed(self):
        self.seed = ((self.seed & 0xFFFFFFFF) * self.MULTIPLIER) + (self.seed >> 32)
        #print(f"PRNG seed: {hex(self.seed)}")

    # Returns a random integer from 0 to range-1
    def random(self, range):
        self._updateSeed()
        return ((self.seed & 0xFFFFFFFF) * range) >> 32

    # Returns eight random bytes
    def randi(self):
        self._updateSeed()
        return self.seed & 0xFFFFFFFF

    # Returns sixteen random bytes
    def randl(self):
        self._updateSeed()
        return self.seed & 0xFFFFFFFFFFFFFFFF
