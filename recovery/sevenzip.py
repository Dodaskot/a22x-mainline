#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""7z archives read in place (2026-11-10): GSIs such as LineageOS ship their system image as .7z.

Only what an image needs: the archive's (possibly LZMA-compressed) header, its folders and the
files in them, and a reader that decodes one file through Python's lzma (raw LZMA or LZMA2, the
methods 7-Zip uses for disk images, with the BCJ filters it may put before them) or copies a
stored one. The file's CRC32 is checked at its end. Encrypted archives and other methods (PPMd,
BZip2, Deflate) are refused by name. Nothing is unpacked to disk.
"""
import lzma
import os
import struct
import zlib

SIGNATURE = b"7z\xbc\xaf'\x1c"
K_END, K_HEADER, K_ARCHIVE_PROPERTIES, K_ADDITIONAL_STREAMS, K_MAIN_STREAMS, K_FILES_INFO = 0, 1, 2, 3, 4, 5
K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS_INFO, K_SIZE, K_CRC, K_FOLDER = 6, 7, 8, 9, 10, 11
K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM, K_EMPTY_STREAM, K_EMPTY_FILE, K_NAME, K_ENCODED_HEADER, K_DUMMY = 12, 13, 14, 15, 17, 23, 25
MAX_HEADER = 64 << 20
MAX_FILES = 100000
# 7-Zip's method ids and their lzma filter.
LZMA2, LZMA, COPY = b'\x21', b'\x03\x01\x01', b'\x00'
BCJ = {b'\x03\x03\x01\x03': lzma.FILTER_X86, b'\x03\x03\x05\x01': lzma.FILTER_ARM, b'\x03\x03\x07\x01': lzma.FILTER_ARMTHUMB}
REFUSED = {b'\x06\xf1\x07\x01': 'encrypted', b'\x03\x04\x01': 'PPMd', b'\x04\x02\x02': 'BZip2', b'\x04\x01\x08': 'Deflate'}


class SevenZipError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise SevenZipError(code, message)


def guarded(function):
    """What a damaged archive makes the decoders raise is said as SevenZipError, which every caller
    handles (2026-11-13 review: a cut .7z gave lzma.LZMAError, StopIteration or IndexError, and a
    traceback instead of an answer)."""
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SevenZipError:
            raise
        except (lzma.LZMAError, StopIteration, IndexError, KeyError, struct.error, EOFError, OverflowError, TypeError) as error:
            raise SevenZipError('INVALID_7Z', 'The 7z archive is damaged') from error
    wrapper.__name__, wrapper.__doc__ = function.__name__, function.__doc__
    return wrapper


class Cursor:
    def __init__(self, data):
        self.data, self.at = data, 0

    def byte(self):
        if self.at >= len(self.data):
            fail('INVALID_7Z', 'The 7z header is damaged')
        self.at += 1
        return self.data[self.at - 1]

    def take(self, count):
        if count < 0 or self.at + count > len(self.data):
            fail('INVALID_7Z', 'The 7z header is damaged')
        self.at += count
        return self.data[self.at - count:self.at]

    def number(self):
        """7z's variable-length UINT64: the leading ones of the first byte count the bytes after it."""
        first, mask, value = self.byte(), 0x80, 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def u32(self):
        return struct.unpack('<I', self.take(4))[0]

    def bits(self, count):
        data, out = self.take((count + 7) // 8), []
        for i in range(count):
            out.append(bool(data[i // 8] & (0x80 >> (i % 8))))
        return out

    def defined(self, count):
        """A byte saying every item is defined, or a bit vector of the defined ones."""
        return [True] * count if self.byte() else self.bits(count)


def digests(cur, count):
    defined = cur.defined(count)
    return [cur.u32() if flag else None for flag in defined]


def pack_info(cur):
    position, streams = cur.number(), cur.number()
    sizes, crcs = [0] * streams, [None] * streams
    while True:
        kind = cur.byte()
        if kind == K_END:
            return position, sizes, crcs
        if kind == K_SIZE:
            sizes = [cur.number() for _ in range(streams)]
        elif kind == K_CRC:
            crcs = digests(cur, streams)
        else:
            fail('INVALID_7Z', 'The 7z header is damaged')


def folder(cur):
    coders, ins, outs = [], 0, 0
    for _ in range(cur.number()):
        flag = cur.byte()
        method = cur.take(flag & 0x0f)
        if flag & 0x80:
            fail('UNSUPPORTED_7Z', 'This 7z uses a method this recovery cannot read')
        n_in, n_out = (cur.number(), cur.number()) if flag & 0x10 else (1, 1)
        props = cur.take(cur.number()) if flag & 0x20 else b''
        coders.append({'method': method, 'props': props, 'ins': n_in, 'outs': n_out})
        ins, outs = ins + n_in, outs + n_out
    binds = [(cur.number(), cur.number()) for _ in range(outs - 1)]
    packed = ins - len(binds)
    streams = [cur.number() for _ in range(packed)] if packed > 1 else [next(i for i in range(ins) if all(b[0] != i for b in binds))]
    return {'coders': coders, 'binds': binds, 'packed': streams, 'outs': outs}


def unpack_info(cur):
    if cur.byte() != K_FOLDER:
        fail('INVALID_7Z', 'The 7z header is damaged')
    count = cur.number()
    if cur.byte():
        fail('UNSUPPORTED_7Z', 'This 7z keeps its folders elsewhere')
    folders = [folder(cur) for _ in range(count)]
    if cur.byte() != K_CODERS_UNPACK_SIZE:
        fail('INVALID_7Z', 'The 7z header is damaged')
    for item in folders:
        item['sizes'] = [cur.number() for _ in range(item['outs'])]
        item['crc'] = None
    while True:
        kind = cur.byte()
        if kind == K_END:
            return folders
        if kind == K_CRC:
            for item, crc in zip(folders, digests(cur, len(folders))):
                item['crc'] = crc
        else:
            fail('INVALID_7Z', 'The 7z header is damaged')


def unpack_size(item):
    """A folder's final output: the out stream no bind pair consumes."""
    bound = {out for _, out in item['binds']}
    final = [i for i in range(item['outs']) if i not in bound]
    return item['sizes'][final[0]] if len(final) == 1 else item['sizes'][-1]


def substreams(cur, folders):
    """How many files each folder holds, their sizes (the last one is what is left of the folder)
    and their CRCs: a folder of one file with a CRC keeps it; the others are listed here."""
    counts = [1] * len(folders)
    kind = cur.byte()
    if kind == K_NUM_UNPACK_STREAM:
        counts = [cur.number() for _ in folders]
        kind = cur.byte()
    sizes = []
    if kind == K_SIZE:
        for item, count in zip(folders, counts):
            if count:
                parts = [cur.number() for _ in range(count - 1)]
                if sum(parts) > unpack_size(item):
                    fail('INVALID_7Z', 'The 7z header is damaged')
                sizes.append(parts + [unpack_size(item) - sum(parts)])
        kind = cur.byte()
    else:
        for item, count in zip(folders, counts):
            if count > 1:
                fail('INVALID_7Z', 'The 7z header is damaged')
            if count:
                sizes.append([unpack_size(item)])
    crcs = [[item['crc']] if count == 1 and item['crc'] is not None else [None] * count for item, count in zip(folders, counts)]
    if kind == K_CRC:
        unknown = sum(len(c) for c in crcs if c and c[0] is None)
        values = iter(digests(cur, unknown))
        crcs = [[next(values) for _ in c] if c and c[0] is None else c for c in crcs]
        kind = cur.byte()
    if kind != K_END:
        fail('INVALID_7Z', 'The 7z header is damaged')
    return counts, sizes, crcs


def streams_info(cur):
    info = {'position': 0, 'pack_sizes': [], 'folders': [], 'counts': None, 'sizes': None, 'crcs': None}
    while True:
        kind = cur.byte()
        if kind == K_END:
            break
        if kind == K_PACK_INFO:
            info['position'], info['pack_sizes'], _ = pack_info(cur)
        elif kind == K_UNPACK_INFO:
            info['folders'] = unpack_info(cur)
        elif kind == K_SUBSTREAMS_INFO:
            info['counts'], info['sizes'], info['crcs'] = substreams(cur, info['folders'])
        else:
            fail('INVALID_7Z', 'The 7z header is damaged')
    if info['counts'] is None:
        info['counts'] = [1] * len(info['folders'])
        info['sizes'] = [[unpack_size(item)] for item in info['folders']]
        info['crcs'] = [[item['crc']] for item in info['folders']]
    return info


def filters_of(item):
    """The lzma raw filter chain of a folder, in liblzma's order (BCJ first, then LZMA/LZMA2), or
    None for a stored folder."""
    chain = []
    for coder in item['coders']:
        method, props = coder['method'], coder['props']
        if method in REFUSED:
            fail('ENCRYPTED_7Z' if REFUSED[method] == 'encrypted' else 'UNSUPPORTED_7Z',
                 'This 7z is encrypted' if REFUSED[method] == 'encrypted' else 'This 7z uses a method this recovery cannot read')
        if method == COPY:
            continue
        if method == LZMA2:
            if len(props) != 1 or props[0] > 40:
                fail('INVALID_7Z', 'The 7z header is damaged')
            p = props[0]
            size = 0xffffffff if p == 40 else (2 | (p & 1)) << (p // 2 + 11)
            chain.append({'id': lzma.FILTER_LZMA2, 'dict_size': size})
        elif method == LZMA:
            if len(props) != 5:
                fail('INVALID_7Z', 'The 7z header is damaged')
            d = props[0]
            chain.append({'id': lzma.FILTER_LZMA1, 'lc': d % 9, 'lp': (d // 9) % 5, 'pb': d // 45,
                          'dict_size': struct.unpack('<I', props[1:5])[0]})
        elif method in BCJ:
            chain.insert(0, {'id': BCJ[method]})
        else:
            fail('UNSUPPORTED_7Z', 'This 7z uses a method this recovery cannot read')
    if len(item['packed']) != 1:
        fail('UNSUPPORTED_7Z', 'This 7z uses a method this recovery cannot read')
    return chain or None


@guarded
def read_header(fd, size):
    head = os.pread(fd, 32, 0)
    if len(head) != 32 or head[:6] != SIGNATURE:
        fail('NOT_7Z', 'This file is not a 7z archive')
    if zlib.crc32(head[12:32]) != struct.unpack('<I', head[8:12])[0]:
        fail('INVALID_7Z', 'The 7z header is damaged')
    offset, length, crc = struct.unpack('<QQI', head[12:32])
    if length > MAX_HEADER or 32 + offset + length > size:
        fail('INVALID_7Z', 'The 7z archive is incomplete')
    data = os.pread(fd, length, 32 + offset)
    if len(data) != length or zlib.crc32(data) != crc:
        fail('INVALID_7Z', 'The 7z header is damaged')
    for _ in range(4):  # An encoded header holds the real one, packed with LZMA.
        cur = Cursor(data)
        kind = cur.byte()
        if kind == K_HEADER:
            return cur
        if kind != K_ENCODED_HEADER:
            fail('INVALID_7Z', 'The 7z header is damaged')
        info = streams_info(cur)
        if len(info['folders']) != 1:
            fail('INVALID_7Z', 'The 7z header is damaged')
        item = info['folders'][0]
        packed = os.pread(fd, info['pack_sizes'][0], 32 + info['position'])
        chain = filters_of(item)
        data = packed if chain is None else lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=chain).decompress(packed, unpack_size(item))
        if len(data) != unpack_size(item) or (item['crc'] is not None and zlib.crc32(data) != item['crc']):
            fail('INVALID_7Z', 'The 7z header is damaged')
    fail('INVALID_7Z', 'The 7z header is damaged')


@guarded
def entries(fd):
    """The files of the archive: name, size, folder, offset of its data in the folder's output, CRC."""
    st = os.fstat(fd)
    cur = read_header(fd, st.st_size)
    info, names, empty, empty_file = None, [], [], []
    count = 0
    while True:
        kind = cur.byte()
        if kind == K_END:
            break
        if kind == K_ARCHIVE_PROPERTIES:
            while cur.byte() != K_END:
                cur.take(cur.number())
        elif kind == K_ADDITIONAL_STREAMS:
            streams_info(cur)
        elif kind == K_MAIN_STREAMS:
            info = streams_info(cur)
        elif kind == K_FILES_INFO:
            count = cur.number()
            if count > MAX_FILES:
                fail('INVALID_7Z', 'The 7z archive holds too many files')
            empty = [False] * count
            while True:
                prop = cur.byte()
                if prop == K_END:
                    break
                size = cur.number()
                body = Cursor(cur.take(size))
                if prop == K_EMPTY_STREAM:
                    empty = body.bits(count)
                elif prop == K_EMPTY_FILE:
                    empty_file = body.bits(sum(empty))
                elif prop == K_NAME:
                    if body.byte():
                        fail('UNSUPPORTED_7Z', 'This 7z keeps its names elsewhere')
                    raw = body.data[body.at:]
                    names = raw.decode('utf-16-le', 'replace').split('\0')[:count]
        else:
            fail('INVALID_7Z', 'The 7z header is damaged')
    if info is None:
        return [], None
    flat, groups = [], iter(info['sizes'])
    for folder_number, held in enumerate(info['counts']):
        if not held:
            continue
        group, offset = next(groups), 0
        crcs = info['crcs'][folder_number] if info['crcs'] else [None] * held
        for index, size in enumerate(group):
            flat.append((folder_number, offset, size, crcs[index] if index < len(crcs) else None))
            offset += size
    streams, files = iter(flat), []
    for i in range(count):
        if empty and empty[i]:
            continue
        try:
            folder_number, offset, size, crc = next(streams)
        except StopIteration:
            fail('INVALID_7Z', 'The 7z header is damaged')
        files.append({'name': names[i] if i < len(names) else 'file%d' % i, 'size': size, 'folder': folder_number,
                      'offset': offset, 'crc': crc})
    return files, info


class EntryReader:
    """One file of the archive, decoded as it is read; its CRC32 is checked at its end."""

    @guarded
    def __init__(self, fd, info, entry):
        self.fd, self.entry = fd, entry
        item = info['folders'][entry['folder']]
        self.chain = filters_of(item)
        start = 32 + info['position'] + sum(info['pack_sizes'][:entry['folder']])
        self.position, self.end = start, start + info['pack_sizes'][entry['folder']]
        self.decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=self.chain) if self.chain else None
        self.skip, self.left, self.crc = entry['offset'], entry['size'], 0

    def _packed(self, count):
        count = max(0, min(count, self.end - self.position))
        data = os.pread(self.fd, count, self.position) if count else b''
        self.position += len(data)
        return data

    def _decoded(self, want):
        if self.decoder is None:
            return self._packed(want)
        while True:
            if self.decoder.eof:
                return b''
            data = self._packed(1 << 20) if self.decoder.needs_input else b''
            if not data and self.decoder.needs_input:
                fail('SHORT_7Z', 'The 7z archive ended early')
            out = self.decoder.decompress(data, max_length=want)
            if out:
                return out

    @guarded
    def read(self, count):
        while self.skip:  # A file after others in a solid folder: their output is passed over.
            data = self._decoded(min(self.skip, 1 << 20))
            if not data:
                fail('SHORT_7Z', 'The 7z archive ended early')
            self.skip -= len(data)
        if self.left <= 0:
            return b''
        data = self._decoded(min(count, self.left))
        if not data:
            fail('SHORT_7Z', 'The 7z archive ended early')
        self.left -= len(data)
        self.crc = zlib.crc32(data, self.crc)
        return data

    @guarded
    def finish(self):
        if self.left:
            fail('SHORT_7Z', 'The 7z archive ended early')
        if self.entry['crc'] is not None and self.crc != self.entry['crc']:
            fail('DAMAGED_7Z', 'The file inside the 7z is damaged')


def choose(files, member=None):
    """MEMBER, else the system image: system.img, or the archive's only image."""
    if member:
        found = next((f for f in files if f['name'] == member), None)
        if found is None:
            fail('7Z_MEMBER_MISSING', 'The 7z has no such image')
        return found
    images = [f for f in files if f['name'].lower().endswith('.img')]
    system = next((f for f in images if f['name'].rsplit('/', 1)[-1].lower() == 'system.img'), None)
    if system:
        return system
    if len(images) != 1:
        fail('7Z_AMBIGUOUS', 'The 7z holds no image or several: keep one image in it')
    return images[0]
