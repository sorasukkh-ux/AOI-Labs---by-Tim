"""QR code as a PNG picture, made on the server with the standard library only (update 47).

The same QR as the printed queue label (static/js/qr.js, ported): QR Code Model 2, byte mode, error correction M,
versions 1-6 (up to 106 bytes). Used for the picture sent to the customer's LINE: LINE fetches an image from a
public https address, so the picture has to come from the server, not from the page.
"""
import struct
import zlib

_EXP, _LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]

# level M: (blocks, data codewords per block, EC codewords per block) per version 1-6
VERSIONS = [None, (1, 16, 10), (1, 28, 16), (1, 44, 26), (2, 32, 18), (2, 43, 24), (4, 27, 16)]
MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
         lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
         lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
         lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _ec(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _mul(g, _EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        factor = b ^ rem.pop(0)
        rem.append(0)
        for k in range(n):
            rem[k] ^= _mul(gen[k + 1], factor)
    return rem


def _penalty(m):
    n, score = len(m), 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, bits = 1, "".join("1" if v else "0" for v in line)
        for b in range(1, n):
            if line[b] == line[b - 1]:
                run += 1
                score += 3 if run == 5 else (1 if run > 5 else 0)
            else:
                run = 1
        score += 40 * (bits.count("10111010000") + bits.count("00001011101"))
    dark = 0
    for r in range(n):
        for c in range(n):
            dark += m[r][c]
            if r < n - 1 and c < n - 1 and m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    return score + abs(dark * 20 - n * n * 10) // (n * n) * 10


def encode(text):
    """Square list of lists of booleans (True = dark module)."""
    data = text.encode("utf-8")
    version = next((v for v in range(1, 7) if len(data) + 2 <= VERSIONS[v][0] * VERSIONS[v][1]), 0)
    if not version:
        raise ValueError("QR text too long: %d bytes (max 106)" % len(data))
    blocks, per, ec_n = VERSIONS[version]
    capacity, n = blocks * per, version * 4 + 17
    bits = []

    def push(value, length):
        bits.extend((value >> i) & 1 for i in range(length - 1, -1, -1))
    push(4, 4)
    push(len(data), 8)
    for b in data:
        push(b, 8)
    push(0, min(4, capacity * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(words) < capacity:
        words.append(0xEC if pad % 2 == 0 else 0x11)
        pad += 1
    data_blocks = [words[b * per:(b + 1) * per] for b in range(blocks)]
    ec_blocks = [_ec(bl, ec_n) for bl in data_blocks]
    stream = [bl[i] for i in range(per) for bl in data_blocks] + [bl[i] for i in range(ec_n) for bl in ec_blocks]
    stream_bits = [(cw >> k) & 1 for cw in stream for k in range(7, -1, -1)]

    m = [[False] * n for _ in range(n)]
    reserved = [[False] * n for _ in range(n)]

    def put(row, col, dark):
        if 0 <= row < n and 0 <= col < n:
            m[row][col] = dark
            reserved[row][col] = True

    def finder(row, col):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                inside = 0 <= dr <= 6 and 0 <= dc <= 6
                put(row + dr, col + dc, inside and (dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4)))
    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)
    for i in range(8, n - 8):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    if version >= 2:
        for ar in range(-2, 3):
            for ac in range(-2, 3):
                put(n - 7 + ar, n - 7 + ac, max(abs(ar), abs(ac)) != 1)
    for i in range(9):
        if not reserved[8][i]:
            put(8, i, False)
        if not reserved[i][8]:
            put(i, 8, False)
    for i in range(8):
        put(8, n - 1 - i, False)
        put(n - 1 - i, 8, False)
    put(n - 8, 8, True)
    idx, up, right = 0, True, n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for step in range(n):
            row = n - 1 - step if up else step
            for k in range(2):
                col = right - k
                if reserved[row][col]:
                    continue
                m[row][col] = idx < len(stream_bits) and stream_bits[idx] == 1
                idx += 1
        up = not up
        right -= 2

    def with_mask(mask):
        out = [line[:] for line in m]
        for r in range(n):
            for c in range(n):
                if not reserved[r][c] and MASKS[mask](r, c):
                    out[r][c] = not out[r][c]
        rem = mask
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        fmt = ((mask << 10) | rem) ^ 0x5412
        bit = lambda q: (fmt >> q) & 1 == 1
        for q in range(6):
            out[q][8] = bit(q)
        out[7][8], out[8][8], out[8][7] = bit(6), bit(7), bit(8)
        for q in range(9, 15):
            out[8][14 - q] = bit(q)
        for q in range(8):
            out[8][n - 1 - q] = bit(q)
        for q in range(8, 15):
            out[n - 15 + q][8] = bit(q)
        out[n - 8][8] = True
        return out
    return min((with_mask(k) for k in range(8)), key=_penalty)


def png(text, scale=12, quiet=4):
    """PNG bytes (black on white, grey-scale 8-bit) of the QR of `text`."""
    matrix = encode(text)
    size = (len(matrix) + 2 * quiet) * scale
    rows = []
    blank = b"\x00" + b"\xff" * size
    for _ in range(quiet * scale):
        rows.append(blank)
    for line in matrix:
        pixels = b"\xff" * (quiet * scale) + b"".join((b"\x00" if dark else b"\xff") * scale for dark in line) + b"\xff" * (quiet * scale)
        rows.extend([b"\x00" + pixels] * scale)
    for _ in range(quiet * scale):
        rows.append(blank)

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))
