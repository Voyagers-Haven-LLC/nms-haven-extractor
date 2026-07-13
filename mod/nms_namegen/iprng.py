MASK64 = 0xFFFFFFFFFFFFFFFF
MASK32 = 0xFFFFFFFF


def ror(x, r):
    return ((x >> r) | (x << (64 - r))) & MASK64


def ror64(x, r):
    r &= 63
    if r < 0:
        return ror(x, 64 + r)  # ROL
    else:
        return ror(x, r)


def hash_round(a, b, c, d, rota, rotb):
    a1 = (ror64(b, rota) ^ c) & MASK64
    b1 = (ror64(a, rotb) ^ d) & MASK64
    c1 = (b1 + c) & MASK64
    d1 = (a1 + d) & MASK64
    # print("hr:", hex(a1), hex(b1), hex(c1), hex(d1))
    return (a1, b1, c1, d1)


def do_hash(a, b, c, d, key, seed):
    o = [0, 0, 0, 0]

    a, b, c, d = hash_round(a, b, c, d, -0x17, 0x18)
    a, b, c, d = hash_round(a, b, c, d, -0x5, 0x1B)
    a += key + 1
    d += key + 1
    a, b, c, d = hash_round(a, b, c, d, -0x19, 0x1F)
    a, b, c, d = hash_round(a, b, c, d, 0x12, -0xC)
    a, b, c, d = hash_round(a, b, c, d, 0x6, -0x16)
    a, b, c, d = hash_round(a, b, c, d, -0x20, -0x20)
    a += seed + 2
    d += key + seed + 2
    a, b, c, d = hash_round(a, b, c, d, -0xE, -0x10)
    a, b, c, d = hash_round(b, a, d, c, 0x7, 0xC)
    a, b, c, d = hash_round(b, a, d, c, -0x17, 0x18)
    a, b, c, d = hash_round(a, b, c, d, -0x5, 0x1B)
    a += 3
    b += key
    c += key
    d += 3 + seed
    a, b, c, d = hash_round(a, b, c, d, -0x19, 0x1F)
    a, b, c, d = hash_round(a, b, c, d, 0x12, -0xC)
    a, b, c, d = hash_round(a, b, c, d, 0x6, -0x16)
    a, b, c, d = hash_round(a, b, c, d, -0x20, -0x20)
    a += 4
    b += seed
    c += seed + key
    d += 4
    a, b, c, d = hash_round(a, b, c, d, -0xE, -0x10)
    a, b, c, d = hash_round(a, b, c, d, 0xC, 0x7)
    a, b, c, d = hash_round(a, b, c, d, -0x17, 0x18)
    o[0] = c + seed
    o[1] = ror64(a, 0x1B) ^ d
    o[2] = d
    o[3] = (ror64(b, -0x5) ^ c) + 5
    return o


def indexPrimedPRNG(lUA):
    o = [0, 0, 0, 0]
    o_counter = 0

    seed = lUA & 0xFFFFFFFFFF
    system_id = ((lUA >> 0x20) >> 8) & 0xFFF
    key = (seed ^ 0x1BD11BDAA9FC1A22) & MASK64
    a = seed
    b = ror64(seed, 7) ^ seed
    c = b + a
    d = seed + seed
    o = do_hash(a, b, c, d, key, seed)

    if system_id >= 9:
        system_index = system_id - 1
        sys_high = (system_index - 8) >> 3
        o_counter = (system_index - 8) & 7

        a = sys_high + 1 + seed
        b = ror64(a, 7) ^ a
        c = b + a
        d = a + a
        o = do_hash(a, b, c, d, key, seed)
    else:
        o_counter = system_id - 1

    index = o_counter >> 1
    o_counter += 1
    # print(hex(o[0]), hex(o[1]),hex(o[2]),hex(o[3]))
    if not (1 & o_counter):
        out = (o[index] >> 0x20) & MASK64
    else:
        out = o[index] & MASK64

    return out
