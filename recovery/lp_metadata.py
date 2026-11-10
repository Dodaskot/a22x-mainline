#!/usr/bin/env python3
"""The logical partitions inside super (Android's liblp format), read and changed without
Android's tools (2026-11-10).

A GSI or a ROM brings system.img (and sometimes product.img, vendor.img) for the logical
partitions of super, not for super as a whole. This module reads the metadata at the start of
super, lists the partitions with their extents, writes an image into one of them, resizes one
within the free space of super and its group, and removes one, keeping every copy of the
metadata (each slot, primary and backup) with correct checksums.

Layout of super (liblp metadata_format.h): 4096 reserved bytes; the primary geometry and its
backup (4096 bytes each); then metadata_slot_count primary metadata copies of metadata_max_size
bytes each, then as many backup copies. A metadata copy is a header followed by four tables:
partitions, extents, groups and block devices. Extents are in 512-byte sectors of super.

    lp_metadata.py dump SUPER          JSON: geometry, partitions with sizes, groups, free space
"""
# SPDX-License-Identifier: MIT
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys

RESERVED = 4096
GEOMETRY_SIZE = 4096
GEOMETRY_MAGIC = 0x616c4467
HEADER_MAGIC = 0x414C5030
SECTOR = 512
GEOMETRY = struct.Struct('<II32sIII')
HEADER_V10_0 = struct.Struct('<IHHI32sI32s' + 'III' * 4)  # 128 bytes
PARTITION = struct.Struct('<36sIIII')  # name, attributes, first_extent_index, num_extents, group_index
EXTENT = struct.Struct('<QIQI')  # num_sectors, target_type, target_data, target_source
GROUP = struct.Struct('<36sIQ')  # name, flags, maximum_size
BLOCK_DEVICE = struct.Struct('<QIIQ36sI')  # first_logical_sector, alignment, alignment_offset, size, name, flags
TARGET_LINEAR, TARGET_ZERO = 0, 1
ATTR_READONLY = 1
NAME = 36


class MetadataError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise MetadataError(code, message)


def cstring(raw):
    return raw.split(b'\0', 1)[0].decode('ascii', 'replace')


def name_bytes(name):
    data = name.encode('ascii')
    require(0 < len(data) < NAME, 'INVALID_NAME', 'A partition name has 1 to 35 ASCII characters')
    return data.ljust(NAME, b'\0')


def read_at(fd, offset, size):
    data = os.pread(fd, size, offset)
    require(len(data) == size, 'SHORT_READ', 'super ends before its metadata')
    return data


def parse_geometry(block):
    magic, size, checksum, max_size, slots, block_size = GEOMETRY.unpack_from(block)
    require(magic == GEOMETRY_MAGIC and size == GEOMETRY.size, 'NOT_SUPER', 'No logical partition geometry: this is not super')
    zeroed = GEOMETRY.pack(magic, size, bytes(32), max_size, slots, block_size)
    require(hashlib.sha256(zeroed).digest() == checksum, 'GEOMETRY_DAMAGED', 'The geometry of super does not match its checksum')
    require(slots >= 1 and max_size >= 512 and max_size % SECTOR == 0 and block_size >= SECTOR, 'GEOMETRY_DAMAGED',
            'The geometry of super is not usable')
    return {'metadata_max_size': max_size, 'metadata_slot_count': slots, 'logical_block_size': block_size}


def metadata_offsets(geometry, slot):
    base = RESERVED + GEOMETRY_SIZE * 2
    primary = base + geometry['metadata_max_size'] * slot
    backup = base + geometry['metadata_max_size'] * (geometry['metadata_slot_count'] + slot)
    return primary, backup


def parse_metadata(blob):
    """One metadata copy: the header, then its tables. Checksums are checked."""
    require(len(blob) >= HEADER_V10_0.size, 'METADATA_DAMAGED', 'The metadata of super is cut short')
    fields = HEADER_V10_0.unpack_from(blob)
    magic, major, minor, header_size, header_sum, tables_size, tables_sum = fields[:7]
    require(magic == HEADER_MAGIC and major == 10 and minor <= 2, 'METADATA_DAMAGED', 'Unknown metadata of super')
    require(HEADER_V10_0.size <= header_size <= 512 and header_size + tables_size <= len(blob), 'METADATA_DAMAGED',
            'The metadata of super has impossible sizes')
    header = bytearray(blob[:header_size])
    header[8 + 4:8 + 4 + 32] = bytes(32)  # magic, versions and header_size come first: 12 bytes
    require(hashlib.sha256(bytes(header)).digest() == header_sum, 'METADATA_DAMAGED', 'The metadata header does not match its checksum')
    tables = blob[header_size:header_size + tables_size]
    require(hashlib.sha256(tables).digest() == tables_sum, 'METADATA_DAMAGED', 'The metadata tables do not match their checksum')
    descriptors = [fields[7 + 3 * n:10 + 3 * n] for n in range(4)]

    def table(index, layout):
        offset, count, entry = descriptors[index]
        require(entry == layout.size and offset + count * entry <= tables_size, 'METADATA_DAMAGED', 'A table of super is damaged')
        return [layout.unpack_from(tables, offset + n * entry) for n in range(count)]
    partitions = [{'name': cstring(p[0]), 'attributes': p[1], 'first_extent': p[2], 'extents': p[3], 'group': p[4]}
                  for p in table(0, PARTITION)]
    extents = [{'sectors': e[0], 'type': e[1], 'start': e[2], 'device': e[3]} for e in table(1, EXTENT)]
    groups = [{'name': cstring(g[0]), 'flags': g[1], 'maximum_size': g[2]} for g in table(2, GROUP)]
    devices = [{'first_logical_sector': d[0], 'alignment': d[1], 'alignment_offset': d[2], 'size': d[3],
                'name': cstring(d[4]), 'flags': d[5]} for d in table(3, BLOCK_DEVICE)]
    for p in partitions:
        require(p['first_extent'] + p['extents'] <= len(extents) and p['group'] < len(groups), 'METADATA_DAMAGED',
                'A partition of super points outside its tables')
    for e in extents:
        require(e['type'] in (TARGET_LINEAR, TARGET_ZERO) and (e['type'] == TARGET_ZERO or e['device'] < len(devices)),
                'METADATA_DAMAGED', 'An extent of super is damaged')
    require(devices, 'METADATA_DAMAGED', 'super lists no block device')
    return {'major': major, 'minor': minor, 'header_size': header_size, 'header_tail': bytes(blob[HEADER_V10_0.size:header_size]),
            'partitions': partitions, 'extents': extents, 'groups': groups, 'devices': devices}


def serialize(metadata, max_size):
    """A metadata copy for MAX_SIZE bytes, with fresh checksums; the header keeps its version."""
    tables = bytearray()
    descriptors = []
    for rows, layout, pack in (
            (metadata['partitions'], PARTITION,
             lambda p: PARTITION.pack(name_bytes(p['name']), p['attributes'], p['first_extent'], p['extents'], p['group'])),
            (metadata['extents'], EXTENT, lambda e: EXTENT.pack(e['sectors'], e['type'], e['start'], e['device'])),
            (metadata['groups'], GROUP, lambda g: GROUP.pack(name_bytes(g['name']), g['flags'], g['maximum_size'])),
            (metadata['devices'], BLOCK_DEVICE,
             lambda d: BLOCK_DEVICE.pack(d['first_logical_sector'], d['alignment'], d['alignment_offset'], d['size'],
                                         name_bytes(d['name']), d['flags']))):
        descriptors.append((len(tables), len(rows), layout.size))
        for row in rows:
            tables += pack(row)
    header_size = metadata['header_size']
    flat = [x for d in descriptors for x in d]
    head = bytearray(HEADER_V10_0.pack(HEADER_MAGIC, 10, metadata['minor'], header_size, bytes(32), len(tables),
                                       hashlib.sha256(bytes(tables)).digest(), *flat) + metadata['header_tail'])
    require(len(head) == header_size, 'METADATA_DAMAGED', 'The metadata header changed its size')
    head[12:44] = hashlib.sha256(bytes(head)).digest()
    blob = bytes(head) + bytes(tables)
    require(len(blob) <= max_size, 'METADATA_TOO_LARGE', 'The new metadata does not fit in super')
    return blob + bytes(max_size - len(blob))


class Super:
    """super opened at FD: its geometry and the metadata of SLOT (0 on a phone without A/B slots)."""
    def __init__(self, fd, slot=0):
        self.fd = fd
        self.size = os.fstat(fd).st_size or os.lseek(fd, 0, os.SEEK_END)
        self.geometry = parse_geometry(read_at(fd, RESERVED, GEOMETRY.size))
        require(slot < self.geometry['metadata_slot_count'], 'INVALID_SLOT', 'super has no such metadata slot')
        self.slot = slot
        primary, backup = metadata_offsets(self.geometry, slot)
        try:
            self.metadata = parse_metadata(read_at(fd, primary, self.geometry['metadata_max_size']))
        except MetadataError:
            self.metadata = parse_metadata(read_at(fd, backup, self.geometry['metadata_max_size']))

    # --- reading ---
    def partition(self, name):
        found = [p for p in self.metadata['partitions'] if p['name'] == name]
        require(len(found) == 1, 'NO_SUCH_PARTITION', 'super has no logical partition ' + name)
        return found[0]

    def extents_of(self, p):
        return self.metadata['extents'][p['first_extent']:p['first_extent'] + p['extents']]

    def size_of(self, p):
        return sum(e['sectors'] for e in self.extents_of(p)) * SECTOR

    def free_regions(self):
        """Unused sectors of the (first) block device: [(start, end)), sorted."""
        device = self.metadata['devices'][0]
        used = sorted((e['start'], e['start'] + e['sectors']) for e in self.metadata['extents']
                      if e['type'] == TARGET_LINEAR and e['device'] == 0)
        free, at = [], device['first_logical_sector']
        end = device['size'] // SECTOR
        for start, stop in used:
            if start > at:
                free.append((at, start))
            at = max(at, stop)
        if at < end:
            free.append((at, end))
        return free

    def describe(self):
        groups = self.metadata['groups']
        return {'geometry': self.geometry, 'block_device': {k: v for k, v in self.metadata['devices'][0].items()},
                'partitions': [{'name': p['name'], 'size': self.size_of(p), 'group': groups[p['group']]['name'],
                                'readonly': bool(p['attributes'] & ATTR_READONLY), 'extents': p['extents']}
                               for p in self.metadata['partitions']],
                'groups': [{'name': g['name'], 'maximum_size': g['maximum_size'],
                            'used': sum(self.size_of(p) for p in self.metadata['partitions'] if groups[p['group']] is g)}
                           for g in groups],
                'free_bytes': sum(end - start for start, end in self.free_regions()) * SECTOR}

    # --- writing ---
    def _commit(self):
        """Write the metadata into every slot, primary and backup copies, and read it back."""
        blob = serialize(self.metadata, self.geometry['metadata_max_size'])
        for slot in range(self.geometry['metadata_slot_count']):
            for offset in metadata_offsets(self.geometry, slot):
                written = os.pwrite(self.fd, blob, offset)
                require(written == len(blob), 'SHORT_WRITE', 'The metadata of super was not written')
        os.fsync(self.fd)
        for slot in range(self.geometry['metadata_slot_count']):
            for offset in metadata_offsets(self.geometry, slot):
                require(read_at(self.fd, offset, len(blob)) == blob, 'READBACK_MISMATCH', 'The metadata of super did not read back')

    def resize(self, name, size):
        """Make partition NAME exactly SIZE bytes (rounded up to the alignment of super), taking
        free space after its own extents first; the group's maximum size is kept."""
        p = self.partition(name)
        device = self.metadata['devices'][0]
        align = max(device['alignment'], SECTOR) // SECTOR
        want = -(-size // (align * SECTOR)) * align  # sectors, rounded up to the alignment
        group = self.metadata['groups'][p['group']]
        others = sum(self.size_of(q) for q in self.metadata['partitions'] if q['group'] == p['group'] and q is not p)
        require(not group['maximum_size'] or others + want * SECTOR <= group['maximum_size'], 'GROUP_FULL',
                'The partition group of super has no room for this size', )
        own = [dict(e) for e in self.extents_of(p)]
        have = sum(e['sectors'] for e in own)
        if want < have:  # Shrink from the end.
            cut = have - want
            while cut and own:
                if own[-1]['sectors'] <= cut:
                    cut -= own.pop()['sectors']
                else:
                    own[-1]['sectors'] -= cut
                    cut = 0
        elif want > have:
            need = want - have
            for start, end in self.free_regions():
                start = -(-start // align) * align
                if start >= end:
                    continue
                take = min(need, end - start)
                if own and own[-1]['type'] == TARGET_LINEAR and own[-1]['start'] + own[-1]['sectors'] == start:
                    own[-1]['sectors'] += take
                else:
                    own.append({'sectors': take, 'type': TARGET_LINEAR, 'start': start, 'device': 0})
                need -= take
                if not need:
                    break
            require(not need, 'NO_SPACE', 'super has not enough free space for this size')
        self._replace_extents(p, own)
        self._commit()
        return self.size_of(self.partition(name))

    def remove(self, name):
        """Remove partition NAME from the metadata; its space becomes free."""
        p = self.partition(name)
        self._replace_extents(p, [])
        index = self.metadata['partitions'].index(p)
        del self.metadata['partitions'][index]
        self._commit()

    def _replace_extents(self, p, own):
        """The extent table again, partition by partition, with OWN for partition P."""
        extents = []
        for q in self.metadata['partitions']:
            rows = own if q is p else self.extents_of(q)
            q_first = len(extents)
            extents.extend(dict(e) for e in rows)
            q['_first'], q['_count'] = q_first, len(rows)
        for q in self.metadata['partitions']:
            q['first_extent'], q['extents'] = q.pop('_first'), q.pop('_count')
        self.metadata['extents'] = extents

    def write_image(self, name, reader, length, progress=None):
        """Copy LENGTH bytes from READER (with read(count)) into partition NAME; the rest of the
        partition is zero-filled. Returns the SHA-256 of what the partition now holds."""
        p = self.partition(name)
        total = self.size_of(p)
        require(length <= total, 'IMAGE_TOO_LARGE', 'The image is larger than the logical partition; resize it first')
        digest = hashlib.sha256()
        done = 0
        for e in self.extents_of(p):
            span = e['sectors'] * SECTOR
            offset = e['start'] * SECTOR
            at = 0
            while at < span:
                count = min(1 << 20, span - at)
                if done < length:
                    data = reader.read(min(count, length - done))
                    require(data, 'SHORT_IMAGE', 'The image ended early')
                    count = len(data)
                    if e['type'] == TARGET_LINEAR:
                        require(os.pwrite(self.fd, data, offset + at) == count, 'SHORT_WRITE', 'The image was not written')
                else:
                    data = bytes(count)
                    if e['type'] == TARGET_LINEAR:
                        os.pwrite(self.fd, data, offset + at)
                digest.update(data)
                at += count
                done += count
                if progress:
                    progress(done, total)
        os.fsync(self.fd)
        return digest.hexdigest()

    def room(self, p):
        """How far partition P can grow: the free space of super, and of its group."""
        align = max(self.metadata['devices'][0]['alignment'], SECTOR) // SECTOR
        free = sum(max(0, end - (-(-start // align) * align)) for start, end in self.free_regions()) * SECTOR
        group = self.metadata['groups'][p['group']]
        if group['maximum_size']:
            used = sum(self.size_of(q) for q in self.metadata['partitions'] if q['group'] == p['group'])
            free = min(free, max(0, group['maximum_size'] - used))
        return free

    def write_chunks(self, name, chunks, progress=None):
        """Write the byte strings of CHUNKS one after another into partition NAME, then zeros to
        its end. Returns (bytes of the image, SHA-256 of the image, SHA-256 of the partition)."""
        p = self.partition(name)
        spans = [(e['start'] * SECTOR, e['sectors'] * SECTOR, e['type']) for e in self.extents_of(p)]
        total = sum(span for _, span, _ in spans)
        image, whole, done = hashlib.sha256(), hashlib.sha256(), 0

        def put(data):
            nonlocal done
            view, at = memoryview(data), 0
            while at < len(view):
                index, inside = done, 0
                for offset, span, kind in spans:  # The extent that holds byte DONE.
                    if index < span:
                        inside = index
                        break
                    index -= span
                else:
                    raise MetadataError('IMAGE_TOO_LARGE', 'The image is larger than the logical partition')
                count = min(len(view) - at, span - inside)
                if kind == TARGET_LINEAR:
                    written = os.pwrite(self.fd, view[at:at + count], offset + inside)
                    require(written == count, 'SHORT_WRITE', 'The image was not written')
                at += count
                done += count
                if progress:
                    progress(done, total)
        length = 0
        for data in chunks:
            image.update(data)
            whole.update(data)
            length += len(data)
            put(data)
        zero = bytes(1 << 20)
        while done < total:
            data = zero[:min(len(zero), total - done)]
            whole.update(data)
            put(data)
        os.fsync(self.fd)
        return length, image.hexdigest(), whole.hexdigest()

    def chunks(self, name, size=1 << 20):
        """What partition NAME holds, in order, at most SIZE bytes at a time (2026-11-10: its old
        content is saved before it is written, as for the partitions outside super)."""
        for e in self.extents_of(self.partition(name)):
            span, offset, at = e['sectors'] * SECTOR, e['start'] * SECTOR, 0
            while at < span:
                count = min(size, span - at)
                yield read_at(self.fd, offset + at, count) if e['type'] == TARGET_LINEAR else bytes(count)
                at += count

    def read_partition(self, name):
        """The SHA-256 of what partition NAME holds, read from super."""
        digest = hashlib.sha256()
        for data in self.chunks(name):
            digest.update(data)
        return digest.hexdigest()


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2 or args[0] != 'dump':
        print('usage: lp_metadata.py dump SUPER', file=sys.stderr)
        return 2
    try:
        fd = os.open(args[1], os.O_RDONLY | os.O_CLOEXEC)
        try:
            print(json.dumps({'ok': True, **Super(fd).describe()}, indent=1))
        finally:
            os.close(fd)
    except (OSError, MetadataError) as exc:
        print(json.dumps({'ok': False, 'error': {'code': getattr(exc, 'code', 'IO_ERROR'), 'message': str(exc)}}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
