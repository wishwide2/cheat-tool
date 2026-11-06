"""Recovered WOLF FF-save cipher. Pure Python; no game or emulator required.

This is the cipher for the observed 3.73 executable and UTF-8/FF save branch.
It does not establish compatibility with every WOLF editor build or save branch.
"""
import struct

MASK = (1 << 32) - 1


def rol(value, count):
    return ((value << count) | (value >> (32 - count))) & MASK


def material(a, b):
    """Generate the exact 64 bytes derived from header[0] and header[5]."""
    result = bytearray(64)
    for i in range(64):
        value = ((((i + b) * (a ^ 0xAA)) ^ ((a | 1) * i + b)) + (a & b)) & 0xFF
        count = i % 5
        rotated = ((value << count) | (value >> (8 - count))) & 0xFF
        result[i] = rotated ^ ((i * b + a) & 0xFF)
    return bytes(result)


def block(state):
    """Standard RFC 8439 ChaCha20 block for sixteen uint32 state words."""
    x = list(state)

    def qr(a, b, c, d):
        x[a] = (x[a] + x[b]) & MASK
        x[d] = rol(x[d] ^ x[a], 16)
        x[c] = (x[c] + x[d]) & MASK
        x[b] = rol(x[b] ^ x[c], 12)
        x[a] = (x[a] + x[b]) & MASK
        x[d] = rol(x[d] ^ x[a], 8)
        x[c] = (x[c] + x[d]) & MASK
        x[b] = rol(x[b] ^ x[c], 7)

    for _ in range(10):
        qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15)
        qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14)
    return struct.pack('<16I', *((a + b) & MASK for a, b in zip(x, state)))


def crypt(raw):
    """Preserve the 20-byte header; XOR the body at stream seek position 20."""
    if len(raw) < 20:
        raise ValueError('Save is shorter than its 20-byte header')
    m = material(raw[0], raw[5])
    state = list(struct.unpack('<16I', b'expand 32-byte k' + m[:32]
                              + struct.pack('<I', 6) + m[42:54]))
    out = bytearray(raw)
    for offset in range(0, len(out), 64):
        stream = block(state)
        for i in range(max(0, 20 - offset), min(64, len(out) - offset)):
            out[offset + i] ^= stream[i]
        state[12] = (state[12] + 1) & MASK
    return bytes(out)
