"""Check OpenPGP signatures of distribution images and checksum lists against pinned keys.

Version 4 keys and signatures (RFC 4880, RFC 9580) with RSA and SHA-2: detached signatures,
binary or ASCII-armoured, over a file or text, and clearsigned text. That is what Arch Linux
ARM, Ubuntu and Fedora publish (2026-11-08). Nothing is signed or decrypted here; a key is
trusted only because the recovery ships it, so subkeys inside a shipped key count as its own.
"""
from __future__ import annotations

import base64
import hashlib
import re
import struct

HASHES = {8: 'sha256', 9: 'sha384', 10: 'sha512', 11: 'sha224'}  # SHA-1 and MD5 are refused.
DIGEST_INFO = {
    'sha224': bytes.fromhex('302d300d06096086480165030402040500041c'),
    'sha256': bytes.fromhex('3031300d060960864801650304020105000420'),
    'sha384': bytes.fromhex('3041300d060960864801650304020205000430'),
    'sha512': bytes.fromhex('3051300d060960864801650304020305000440'),
}
RSA = (1, 3)  # RSA (encrypt or sign), RSA sign-only.
MIN_RSA_BITS = 2048
CHUNK = 1 << 20


class SignatureError(Exception):
    """The signature does not verify, or cannot be read."""


def dearmor(data: bytes) -> bytes:
    """Binary packets of the first ASCII-armoured block in DATA; binary data stays as it is."""
    text = data.lstrip()
    if not text.startswith(b'-----BEGIN PGP '):
        return data
    lines = text.decode('ascii', 'replace').splitlines()
    body, inside, headers = [], False, True
    for line in lines:
        if not inside:
            inside = line.startswith('-----BEGIN PGP ')
            continue
        if line.startswith('-----END PGP '):
            break
        if headers:
            # Armour headers ("Version: ...") end at the first empty line.
            if not line.strip():
                headers = False
                continue
            if ':' in line:
                continue
            headers = False
        if line.startswith('=') and len(line.strip()) == 5:
            continue  # The CRC-24 line; the signature check covers the content.
        body.append(line.strip())
    try:
        return base64.b64decode(''.join(body), validate=True)
    except ValueError as error:
        raise SignatureError('damaged ASCII armour') from error


def packets(data: bytes):
    """(tag, body) of each packet; old and new header formats, partial lengths joined."""
    i = 0
    while i < len(data):
        head = data[i]
        if not head & 0x80:
            raise SignatureError('not an OpenPGP packet')
        i += 1
        if head & 0x40:
            tag, body = head & 0x3F, b''
            while True:
                if i >= len(data):
                    raise SignatureError('truncated packet')
                first = data[i]
                if first < 192:
                    length, i = first, i + 1
                elif first < 224:
                    length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
                elif first == 255:
                    length, i = struct.unpack('>I', data[i + 1:i + 5])[0], i + 5
                else:
                    part = 1 << (first & 0x1F)  # A partial length: more parts follow.
                    body += data[i + 1:i + 1 + part]
                    i += 1 + part
                    continue
                body += data[i:i + length]
                i += length
                break
        else:
            tag, kind = (head >> 2) & 0x0F, head & 3
            if kind == 3:
                length, size = len(data) - i, 0
            else:
                size = (1, 2, 4)[kind]
                length = int.from_bytes(data[i:i + size], 'big')
            body = data[i + size:i + size + length]
            i += size + length
        if len(body) != length and not head & 0x40:
            raise SignatureError('truncated packet')
        yield tag, body


def mpi(body: bytes, at: int):
    bits = struct.unpack('>H', body[at:at + 2])[0]
    size = (bits + 7) // 8
    if at + 2 + size > len(body):
        raise SignatureError('truncated number')
    return int.from_bytes(body[at + 2:at + 2 + size], 'big'), at + 2 + size


def read_keys(data: bytes):
    """The RSA keys and subkeys of a key file: [{fingerprint, keyid, n, e}]."""
    keys = []
    for tag, body in packets(dearmor(data)):
        if tag not in (6, 14) or len(body) < 6 or body[0] != 4:
            continue
        algorithm = body[5]
        fingerprint = hashlib.sha1(b'\x99' + struct.pack('>H', len(body)) + body).hexdigest().upper()
        if algorithm not in RSA:
            continue
        n, at = mpi(body, 6)
        e, _ = mpi(body, at)
        if n.bit_length() >= MIN_RSA_BITS:
            keys.append({'fingerprint': fingerprint, 'keyid': fingerprint[-16:], 'n': n, 'e': e})
    if not keys:
        raise SignatureError('no usable RSA key')
    return keys


def subpackets(data: bytes):
    i = 0
    while i < len(data):
        first = data[i]
        if first < 192:
            length, i = first, i + 1
        elif first < 255:
            length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
        else:
            length, i = struct.unpack('>I', data[i + 1:i + 5])[0], i + 5
        if length < 1 or i + length > len(data):
            raise SignatureError('damaged signature subpacket')
        yield data[i] & 0x7F, data[i + 1:i + length]
        i += length


def read_signature(data: bytes):
    """The first version 4 signature in DATA (binary or armoured)."""
    for tag, body in packets(dearmor(data)):
        if tag != 2:
            continue
        if len(body) < 6 or body[0] != 4:
            raise SignatureError('only version 4 signatures are checked')
        kind, algorithm, hashed_with = body[1], body[2], body[3]
        hashed_size = struct.unpack('>H', body[4:6])[0]
        hashed = body[6:6 + hashed_size]
        at = 6 + hashed_size
        unhashed_size = struct.unpack('>H', body[at:at + 2])[0]
        unhashed = body[at + 2:at + 2 + unhashed_size]
        at += 2 + unhashed_size
        left = body[at:at + 2]
        if algorithm not in RSA:
            raise SignatureError('unsupported signature algorithm %d' % algorithm)
        value, _ = mpi(body, at + 2)
        issuers = set()
        for space in (hashed, unhashed):
            for kind_, content in subpackets(space):
                if kind_ == 16 and len(content) == 8:
                    issuers.add(content.hex().upper())
                elif kind_ == 33 and len(content) == 21 and content[0] == 4:
                    issuers.add(content[1:].hex().upper()[-16:])
        if hashed_with not in HASHES:
            raise SignatureError('unsupported hash %d' % hashed_with)
        return {'kind': kind, 'hash': HASHES[hashed_with], 'prefix': body[:6 + hashed_size],
                'left': left, 'value': value, 'issuers': issuers}
    raise SignatureError('no signature packet')


def canonical_text(data: bytes) -> bytes:
    """Line ends as CR LF, as signature type 0x01 requires."""
    return re.sub(rb'\r?\n', b'\r\n', data)


def finish(signature, digest, keys):
    """Compare a filled hash with the signature and the pinned keys."""
    prefix = signature['prefix']
    digest.update(prefix)
    digest.update(b'\x04\xff' + struct.pack('>I', len(prefix)))
    value = digest.digest()
    if value[:2] != signature['left']:
        raise SignatureError('the data does not match the signature')
    candidates = [k for k in keys if k['keyid'] in signature['issuers']] or keys
    for key in candidates:
        size = (key['n'].bit_length() + 7) // 8
        if signature['value'] >= key['n']:
            continue
        message = pow(signature['value'], key['e'], key['n']).to_bytes(size, 'big')
        expected = DIGEST_INFO[signature['hash']] + value
        padding = size - 3 - len(expected)
        if padding >= 8 and message == b'\x00\x01' + b'\xff' * padding + b'\x00' + expected:
            return key['fingerprint']
    raise SignatureError('not signed by a pinned key')


def verify_file(path, signature_data: bytes, key_data: bytes) -> str:
    """Check a detached signature over a file, read in chunks; returns the signing key."""
    signature, keys = read_signature(signature_data), read_keys(key_data)
    digest = hashlib.new(signature['hash'])
    if signature['kind'] == 0x01:
        with open(path, 'rb') as data:
            digest.update(canonical_text(data.read()))
    elif signature['kind'] == 0x00:
        with open(path, 'rb') as data:
            while block := data.read(CHUNK):
                digest.update(block)
    else:
        raise SignatureError('not a document signature')
    return finish(signature, digest, keys)


def verify_bytes(data: bytes, signature_data: bytes, key_data: bytes) -> str:
    signature, keys = read_signature(signature_data), read_keys(key_data)
    if signature['kind'] not in (0x00, 0x01):
        raise SignatureError('not a document signature')
    digest = hashlib.new(signature['hash'])
    digest.update(canonical_text(data) if signature['kind'] == 0x01 else data)
    return finish(signature, digest, keys)


def verify_clearsigned(text: bytes, key_data: bytes) -> bytes:
    """The text of a clearsigned message whose signature verifies; dash escapes removed."""
    lines = text.replace(b'\r\n', b'\n').split(b'\n')
    try:
        start = lines.index(b'-----BEGIN PGP SIGNED MESSAGE-----')
        end = lines.index(b'-----BEGIN PGP SIGNATURE-----', start)
    except ValueError as error:
        raise SignatureError('not a clearsigned message') from error
    at = start + 1
    while at < end and lines[at].strip():
        at += 1  # "Hash:" headers until the empty line.
    body = [line[2:] if line.startswith(b'- ') else line for line in lines[at + 1:end]]
    signed = b'\r\n'.join(line.rstrip(b' \t') for line in body)
    signature, keys = read_signature(b'\n'.join(lines[end:])), read_keys(key_data)
    if signature['kind'] != 0x01:
        raise SignatureError('not a text signature')
    digest = hashlib.new(signature['hash'])
    digest.update(signed)
    finish(signature, digest, keys)
    return b'\n'.join(body) + b'\n'
