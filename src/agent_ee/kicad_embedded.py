"""Verify KiCad embedded-file records from captured board bytes (no native tool runs).

KiCad 10 stores `(embedded_files (file (name N) (type T) (data |BASE64|) (checksum C)))`
at board and footprint level. The payload is base64 of one zstd frame. Native load
(common/embedded_files.cpp, KiCad 10.0.7) refuses a frame without a declared content
size, decompresses it and compares `checksum` with MurmurHash3-x64-128 in KiCad's
streaming variant (tail zero-padded to 4 bytes and counted in the length, seed
0xABBA2345, 32 uppercase hex digits) or, for 64 hex digits, SHA-256. A checksum
mismatch drops the record; a record without data is discarded on save. This module
repeats those steps in pure Python so presence means a decodable, checksum-verified,
nonempty payload, never a name alone.
"""

import base64
import binascii
import hashlib
import struct

MAGIC = b'\x28\xb5\x2f\xfd'
SEED = 0xABBA2345
FILE_TYPES = frozenset(('datasheet', 'font', 'model', 'worksheet', 'other'))
MAX_CONTENT = 64 * 1024 * 1024
M64 = (1 << 64) - 1


class ZstdError(ValueError):
    """The frame is not one complete, decodable zstd frame."""


class ZstdUnsupported(ValueError):
    """A valid frame uses a feature or size this verifier does not evaluate."""


# ------------------------------------------------------------------ checksum

def _rotl(x, r):
    return ((x << r) | (x >> (64 - r))) & M64


def _fmix(k):
    k ^= k >> 33
    k = (k * 0xff51afd7ed558ccd) & M64
    k ^= k >> 33
    k = (k * 0xc4ceb9fe1a85ec53) & M64
    return k ^ (k >> 33)


def kicad_checksum(data):
    """KiCad's MMH3_HASH(EMBEDDED_FILES::Seed()).digest().ToString() of `data`."""
    c1, c2 = 0x87c37b91114253d5, 0x4cf5ad432745937f
    h1 = h2 = SEED
    blocks = len(data) // 16
    for i in range(blocks):
        k1, k2 = struct.unpack_from('<QQ', data, i * 16)
        h1 ^= (_rotl((k1 * c1) & M64, 31) * c2) & M64
        h1 = ((_rotl(h1, 27) + h2) * 5 + 0x52dce729) & M64
        h2 ^= (_rotl((k2 * c2) & M64, 33) * c1) & M64
        h2 = ((_rotl(h2, 31) + h1) * 5 + 0x38495ab5) & M64
    length = blocks * 16
    tail = bytes(data[blocks * 16:])
    if tail:
        # KiCad pads the tail to a 4-byte boundary (a full 4 when already aligned)
        # and counts the padding; a padded length of 16 leaves the tail unhashed.
        padding = 4 - (len(tail) + 4) % 4
        length += len(tail) + padding
        tail += bytes(padding)
    tail += bytes(16 - len(tail))
    used = length & 15
    if used > 8:
        k2 = int.from_bytes(tail[8:used], 'little')
        h2 ^= (_rotl((k2 * c2) & M64, 33) * c1) & M64
    if used:
        k1 = int.from_bytes(tail[:min(used, 8)], 'little')
        h1 ^= (_rotl((k1 * c1) & M64, 31) * c2) & M64
    length &= 0xffffffff
    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & M64
    h2 = (h2 + h1) & M64
    h1, h2 = _fmix(h1), _fmix(h2)
    h1 = (h1 + h2) & M64
    h2 = (h2 + h1) & M64
    return f'{h1:016X}{h2:016X}'


# ------------------------------------------------------------- zstd (RFC 8878)

LL_BASE = list(range(16)) + [16, 18, 20, 22, 24, 28, 32, 40, 48, 64, 128, 256, 512, 1024, 2048,
                             4096, 8192, 16384, 32768, 65536]
LL_BITS = [0] * 16 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
ML_BASE = list(range(3, 35)) + [35, 37, 39, 41, 43, 47, 51, 59, 67, 83, 99, 131, 259, 515, 1027,
                                2051, 4099, 8195, 16387, 32771, 65539]
ML_BITS = [0] * 32 + [1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
LL_DEFAULT = [4, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 2, 1, 1, 1,
              1, 1, -1, -1, -1, -1]
ML_DEFAULT = [1, 4, 3, 2, 2, 2, 2, 2, 2] + [1] * 37 + [-1] * 7
OF_DEFAULT = [1, 1, 1, 1, 1, 1, 2, 2, 2] + [1] * 15 + [-1] * 5
# (name, mode bit shift, predefined distribution, predefined log, max log, max symbol)
SEQUENCE_TABLES = (('ll', 6, LL_DEFAULT, 6, 9, 35), ('of', 4, OF_DEFAULT, 5, 8, 31),
                   ('ml', 2, ML_DEFAULT, 6, 9, 52))


class Forward:
    """Little-endian, least-significant-bit-first reader (table descriptions)."""

    def __init__(self, data):
        self.data, self.pos = data, 0

    def read(self, n):
        end = self.pos + n
        if end > 8 * len(self.data):
            raise ZstdError('truncated table description')
        value = (int.from_bytes(self.data[self.pos >> 3:(end + 7) >> 3], 'little') >> (self.pos & 7)) & ((1 << n) - 1)
        self.pos = end
        return value

    def used(self):
        return (self.pos + 7) >> 3


class Backward:
    """Reverse bitstream from the highest set (padding) bit; reads past the start yield zeros."""

    def __init__(self, data):
        if not data or data[-1] == 0:
            raise ZstdError('bitstream lacks its end marker')
        self.data = data
        self.pos = 8 * (len(data) - 1) + data[-1].bit_length() - 1

    def read(self, n):
        if n == 0:
            return 0
        self.pos -= n
        low, high = self.pos, self.pos + n
        if low >= 0:
            return (int.from_bytes(self.data[low >> 3:(high + 7) >> 3], 'little') >> (low & 7)) & ((1 << n) - 1)
        if high <= 0:
            return 0
        return (int.from_bytes(self.data[:(high + 7) >> 3], 'little') & ((1 << high) - 1)) << -low


def fse_table(probabilities, log):
    size = 1 << log
    if sum(abs(p) for p in probabilities) != size:
        raise ZstdError('FSE distribution does not fill its table')
    symbols, high, nexts = [0] * size, size, [0] * len(probabilities)
    for symbol, p in enumerate(probabilities):
        if p == -1:
            high -= 1
            symbols[high] = symbol
            nexts[symbol] = 1
    step, mask, pos = (size >> 1) + (size >> 3) + 3, size - 1, 0
    for symbol, p in enumerate(probabilities):
        if p <= 0:
            continue
        nexts[symbol] = p
        for _ in range(p):
            symbols[pos] = symbol
            pos = (pos + step) & mask
            while pos >= high:
                pos = (pos + step) & mask
    if pos:
        raise ZstdError('FSE spread did not return to state 0')
    bits, base = [0] * size, [0] * size
    for state in range(size):
        symbol = symbols[state]
        following = nexts[symbol]
        nexts[symbol] += 1
        bits[state] = log - (following.bit_length() - 1)
        base[state] = (following << bits[state]) - size
    return log, symbols, bits, base


def fse_description(reader, max_symbol, max_log):
    log = reader.read(4) + 5
    if log > max_log:
        raise ZstdError(f'FSE accuracy log {log} exceeds {max_log}')
    remaining, probabilities = 1 << log, []
    while remaining > 0 and len(probabilities) <= max_symbol:
        bits = (remaining + 1).bit_length()
        value = reader.read(bits)
        lower = (1 << (bits - 1)) - 1
        threshold = (1 << bits) - 1 - (remaining + 1)
        if value & lower < threshold:
            reader.pos -= 1
            value &= lower
        elif value > lower:
            value -= threshold
        probability = value - 1
        remaining -= abs(probability)
        probabilities.append(probability)
        if probability == 0:
            while True:
                repeat = reader.read(2)
                probabilities += [0] * repeat
                if repeat != 3:
                    break
    if remaining != 0 or len(probabilities) > max_symbol + 1:
        raise ZstdError('corrupt FSE table description')
    return fse_table(probabilities, log)


def rle_table(symbol):
    return 0, [symbol], [0], [0]


def huffman_table(data):
    """(table, bytes consumed) from a Huffman tree description."""
    if not data:
        raise ZstdError('missing Huffman tree description')
    header = data[0]
    if header >= 128:
        count = header - 127
        size = (count + 1) // 2
        if len(data) < 1 + size:
            raise ZstdError('truncated Huffman weights')
        weights = [n for byte in data[1:1 + size] for n in (byte >> 4, byte & 15)][:count]
    else:
        size = header
        if size == 0 or len(data) < 1 + size:
            raise ZstdError('truncated Huffman weights')
        chunk = data[1:1 + size]
        reader = Forward(chunk)
        log, fse_symbols, fse_bits, fse_base = fse_description(reader, 255, 6)
        stream = Backward(chunk[reader.used():])
        states = [stream.read(log), stream.read(log)]
        weights, which = [], 0
        while True:
            state = states[which]
            weights.append(fse_symbols[state])
            states[which] = fse_base[state] + stream.read(fse_bits[state])
            if stream.pos < 0:
                weights.append(fse_symbols[states[1 - which]])
                break
            which = 1 - which
            if len(weights) > 255:
                raise ZstdError('too many Huffman weights')
    if not weights or len(weights) > 255 or max(weights) > 11:
        raise ZstdError('invalid Huffman weights')
    total = sum(1 << (w - 1) for w in weights if w)
    if not total:
        raise ZstdError('empty Huffman weights')
    max_bits = total.bit_length()
    left = (1 << max_bits) - total
    if left & (left - 1) or max_bits > 11:
        raise ZstdError('Huffman weights do not complete a prefix code')
    weights.append(left.bit_length())
    lengths = [max_bits + 1 - w if w else 0 for w in weights]
    counts = [0] * (max_bits + 1)
    for n in lengths:
        if n:
            counts[n] += 1
    starts = [0] * (max_bits + 1)
    for n in range(max_bits, 0, -1):
        starts[n - 1] = starts[n] + counts[n] * (1 << (max_bits - n))
    symbols, bits = [0] * (1 << max_bits), [0] * (1 << max_bits)
    for symbol, n in enumerate(lengths):
        if n:
            span = 1 << (max_bits - n)
            symbols[starts[n]:starts[n] + span] = [symbol] * span
            bits[starts[n]:starts[n] + span] = [n] * span
            starts[n] += span
    return (max_bits, symbols, bits), 1 + size


def huffman_stream(table, data, count):
    max_bits, symbols, bits = table
    stream = Backward(data)
    state, mask, out = stream.read(max_bits), (1 << max_bits) - 1, bytearray()
    for _ in range(count):
        out.append(symbols[state])
        n = bits[state]
        state = ((state << n) + stream.read(n)) & mask
    if stream.pos != -max_bits:
        raise ZstdError('Huffman stream length disagrees with its regenerated size')
    return out


def literals(block, state):
    if not block:
        raise ZstdError('empty compressed block')
    first, kind = block[0], block[0] & 3
    form = (first >> 2) & 3
    if kind < 2:
        if form in (0, 2):
            size, pos = first >> 3, 1
        elif form == 1:
            size, pos = (first >> 4) + (block[1] << 4), 2
        else:
            size, pos = (first >> 4) + (block[1] << 4) + (block[2] << 12), 3
        if kind == 0:
            if len(block) < pos + size:
                raise ZstdError('truncated raw literals')
            return bytes(block[pos:pos + size]), pos + size
        if len(block) <= pos:
            raise ZstdError('truncated RLE literals')
        return bytes([block[pos]]) * size, pos + 1
    width = {0: 3, 1: 3, 2: 4, 3: 5}[form]
    if len(block) < width:
        raise ZstdError('truncated literals header')
    value = int.from_bytes(block[:width], 'little')
    field = {3: 10, 4: 14, 5: 18}[width]
    size = (value >> 4) & ((1 << field) - 1)
    compressed = (value >> (4 + field)) & ((1 << field) - 1)
    payload = block[width:width + compressed]
    if len(payload) != compressed:
        raise ZstdError('truncated compressed literals')
    if kind == 2:
        state['huffman'], used = huffman_table(payload)
        payload = payload[used:]
    elif state.get('huffman') is None:
        raise ZstdError('treeless literals without a previous Huffman table')
    table = state['huffman']
    if form == 0:
        out = huffman_stream(table, payload, size)
    else:
        if len(payload) < 10:
            raise ZstdError('truncated four-stream literals')
        sizes = list(struct.unpack_from('<HHH', payload))
        sizes.append(len(payload) - 6 - sum(sizes))
        segment = (size + 3) // 4
        counts = [segment, segment, segment, size - 3 * segment]
        if sizes[3] < 1 or counts[3] < 0:
            raise ZstdError('inconsistent four-stream literals')
        out, pos = bytearray(), 6
        for length, count in zip(sizes, counts):
            out += huffman_stream(table, payload[pos:pos + length], count)
            pos += length
    return bytes(out), width + compressed


def sequences(block, pos, state):
    if pos >= len(block):
        raise ZstdError('missing sequences section')
    first = block[pos]
    if first == 0:
        if pos + 1 != len(block):
            raise ZstdError('bytes after an empty sequences section')
        return []
    if first < 128:
        count, pos = first, pos + 1
    elif first < 255:
        count, pos = ((first - 128) << 8) + block[pos + 1], pos + 2
    else:
        count, pos = block[pos + 1] + (block[pos + 2] << 8) + 0x7F00, pos + 3
    if pos >= len(block):
        raise ZstdError('truncated sequences header')
    modes = block[pos]
    pos += 1
    if modes & 3:
        raise ZstdError('reserved sequence mode bits set')
    tables = state['tables']
    for name, shift, default, log, max_log, max_symbol in SEQUENCE_TABLES:
        mode = (modes >> shift) & 3
        if mode == 0:
            tables[name] = fse_table(default, log)
        elif mode == 1:
            if pos >= len(block) or block[pos] > max_symbol:
                raise ZstdError('invalid RLE sequence symbol')
            tables[name] = rle_table(block[pos])
            pos += 1
        elif mode == 2:
            reader = Forward(block[pos:])
            tables[name] = fse_description(reader, max_symbol, max_log)
            pos += reader.used()
        elif name not in tables:
            raise ZstdError('repeat sequence table without a previous table')
    stream = Backward(block[pos:])
    ll, of, ml = tables['ll'], tables['of'], tables['ml']
    ll_state, of_state, ml_state = stream.read(ll[0]), stream.read(of[0]), stream.read(ml[0])
    out = []
    for index in range(count):
        code_of, code_ml, code_ll = of[1][of_state], ml[1][ml_state], ll[1][ll_state]
        if code_of > 31:
            raise ZstdError('offset code out of range')
        offset = (1 << code_of) + stream.read(code_of)
        match = ML_BASE[code_ml] + stream.read(ML_BITS[code_ml])
        literal = LL_BASE[code_ll] + stream.read(LL_BITS[code_ll])
        out.append((literal, offset, match))
        if index != count - 1:
            ll_state = ll[3][ll_state] + stream.read(ll[2][ll_state])
            ml_state = ml[3][ml_state] + stream.read(ml[2][ml_state])
            of_state = of[3][of_state] + stream.read(of[2][of_state])
    if stream.pos != 0:
        raise ZstdError('sequence bitstream not consumed exactly')
    return out


def compressed_block(block, state, out, limit):
    lits, pos = literals(block, state)
    used, history = 0, state['offsets']
    for literal, value, match in sequences(block, pos, state):
        if used + literal > len(lits):
            raise ZstdError('sequence reads past its literals')
        out += lits[used:used + literal]
        used += literal
        if value > 3:
            offset = value - 3
            history[:] = [offset, history[0], history[1]]
        else:
            index = value - 1 + (literal == 0)
            if index == 0:
                offset = history[0]
            else:
                offset = history[index] if index < 3 else history[0] - 1
                if index > 1:
                    history[2] = history[1]
                history[1] = history[0]
                history[0] = offset
        if offset <= 0 or offset > len(out):
            raise ZstdError('match offset outside the decoded history')
        if len(out) + match > limit:
            raise ZstdError('decoded data exceeds the declared content size')
        start = len(out) - offset
        if match <= offset:
            out += out[start:start + match]
        else:
            out += (out[start:] * (match // offset + 1))[:match]
    out += lits[used:]


def decompress(frame, limit=MAX_CONTENT):
    """Content of one complete zstd frame that declares its size (as KiCad requires)."""
    if len(frame) < 6 or frame[:4] != MAGIC:
        raise ZstdError('payload is not a zstd frame')
    descriptor = frame[4]
    size_flag, single, checksum, dictionary = descriptor >> 6, (descriptor >> 5) & 1, (descriptor >> 2) & 1, descriptor & 3
    if descriptor & 8:
        raise ZstdError('reserved frame header bit set')
    pos = 5 + (not single)
    width = (0, 1, 2, 4)[dictionary]
    if int.from_bytes(frame[pos:pos + width], 'little'):
        raise ZstdUnsupported('frame needs a zstd dictionary')
    pos += width
    width = ((1 if single else 0), 2, 4, 8)[size_flag]
    if not width:
        raise ZstdError('frame does not declare its content size; native KiCad load refuses it')
    if len(frame) < pos + width:
        raise ZstdError('truncated frame header')
    size = int.from_bytes(frame[pos:pos + width], 'little') + (256 if width == 2 else 0)
    pos += width
    if size > limit:
        raise ZstdUnsupported(f'declared content of {size} bytes exceeds the {limit}-byte verification limit')
    out, state = bytearray(), {'tables': {}, 'huffman': None, 'offsets': [1, 4, 8]}
    while True:
        if len(frame) < pos + 3:
            raise ZstdError('truncated block header')
        header = int.from_bytes(frame[pos:pos + 3], 'little')
        pos += 3
        last, kind, length = header & 1, (header >> 1) & 3, header >> 3
        if kind == 1:
            if len(frame) <= pos:
                raise ZstdError('truncated RLE block')
            out += frame[pos:pos + 1] * length
            pos += 1
        elif kind == 3:
            raise ZstdError('reserved block type')
        else:
            body = frame[pos:pos + length]
            if len(body) != length:
                raise ZstdError('truncated block')
            pos += length
            if kind == 0:
                out += body
            else:
                compressed_block(body, state, out, size)
        if len(out) > size:
            raise ZstdError('decoded data exceeds the declared content size')
        if last:
            break
    pos += 4 * checksum
    if pos != len(frame):
        raise ZstdError('frame is truncated or followed by extra bytes')
    if len(out) != size:
        raise ZstdError(f'decoded {len(out)} bytes but the frame declares {size}')
    return bytes(out)


# ------------------------------------------------------------- board records

def records(node):
    """{name: [record, ...]} from the `embedded_files` groups directly under one node."""
    found = {}
    for group in (x for x in node[1:] if isinstance(x, list) and x and x[0] == 'embedded_files'):
        for item in (x for x in group[1:] if isinstance(x, list) and x and x[0] == 'file'):
            fields = {}
            for field in item[1:]:
                if isinstance(field, list) and field and isinstance(field[0], str):
                    fields.setdefault(field[0], []).append([a for a in field[1:] if isinstance(a, str)])
            names = fields.get('name', [])
            if len(names) != 1 or len(names[0]) != 1:
                continue
            found.setdefault(names[0][0], []).append(fields)
    return found


def verify(candidates, name, limit=MAX_CONTENT):
    """('resolved'|'unresolved'|'uncovered', reason) for the records named `name` in one scope."""
    if not candidates:
        return 'unresolved', f'no embedded file {name!r} in the captured board'
    if len(candidates) > 1:
        if any(c != candidates[0] for c in candidates[1:]):
            return 'uncovered', f'{len(candidates)} differing embedded records named {name!r}; native load keeps one'
    fields = candidates[0]
    types = fields.get('type', [])
    if len(types) != 1 or len(types[0]) != 1 or types[0][0] not in FILE_TYPES:
        return 'uncovered', f'embedded {name!r} has no single recognised type'
    data = fields.get('data', [])
    if not data or not data[0] or ''.join(data[0]) in ('||', ''):
        return 'unresolved', f'embedded {name!r} has no data; native save discards an empty record'
    if len(data) != 1:
        return 'uncovered', f'embedded {name!r} has several data fields'
    text = ''.join(data[0])
    if len(text) < 3 or text[0] != '|' or text[-1] != '|' or '|' in text[1:-1]:
        return 'unresolved', f'embedded {name!r} data is not one |base64| block'
    try:
        frame = base64.b64decode(text[1:-1], validate=True)
    except (binascii.Error, ValueError):
        return 'unresolved', f'embedded {name!r} data is not base64'
    try:
        content = decompress(frame, limit)
    except ZstdUnsupported as exc:
        return 'uncovered', f'embedded {name!r}: {exc}'
    except (ZstdError, IndexError) as exc:
        return 'unresolved', f'embedded {name!r} payload does not decompress ({exc})'
    if not content:
        return 'unresolved', f'embedded {name!r} decompresses to zero bytes'
    checksums = fields.get('checksum', [])
    if len(checksums) != 1 or len(checksums[0]) != 1:
        return 'unresolved', f'embedded {name!r} lacks one checksum; native load rejects it'
    declared = checksums[0][0]
    expected = hashlib.sha256(content).hexdigest() if len(declared) == 64 else kicad_checksum(content)
    if declared != expected:
        return 'unresolved', f'embedded {name!r} checksum mismatch; native load drops the record'
    return 'resolved', f'embedded {name!r}: {len(content)} bytes decoded, checksum verified'
