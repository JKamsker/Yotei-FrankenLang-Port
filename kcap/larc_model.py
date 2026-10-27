"""Read-only directory and bounded XVAG chunk inspection for supplied samples."""
from dataclasses import dataclass
from pathlib import Path
import struct


@dataclass(frozen=True)
class Entry:
    name: str
    offset: int
    size: int
    duration: float
    version: int
    fmat: tuple
    chunks: tuple


def read_larc(path):
    data = Path(path).read_bytes()
    if not data:
        return []
    if data[:8] != b"cral\x01\x05\0\0":
        raise ValueError(f"{path}: unknown LARC header")
    base, count = struct.unpack_from("<II", data, 0x18)
    if not 0x20 <= base <= len(data) or count > (base - 0x20) // 16:
        raise ValueError(f"{path}: invalid LARC directory bounds")
    cursor, entries = 0x20, []
    for _ in range(count):
        name_len = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        if not 1 <= name_len <= 512 or cursor + name_len + 12 > base:
            raise ValueError(f"{path}: invalid LARC entry bounds")
        name = data[cursor:cursor + name_len].decode("ascii")
        cursor += name_len
        relative, size, duration = struct.unpack_from("<IIf", data, cursor)
        cursor += 12
        offset = base + relative
        if offset < base or offset + size > len(data) or size < 0x20:
            raise ValueError(f"{path}: invalid XVAG extent")
        if data[offset:offset + 4] != b"XVAG":
            raise ValueError(f"{path}: invalid XVAG signature")
        header_size = struct.unpack_from("<I", data, offset + 4)[0]
        if not 0x20 <= header_size <= size:
            raise ValueError(f"{path}: invalid XVAG header length")
        chunk_cursor, chunks, fmat = offset + 0x20, [], None
        while chunk_cursor < offset + header_size:
            tag, length = struct.unpack_from("<4sI", data, chunk_cursor)
            chunk_cursor += 8
            if chunk_cursor + length > offset + header_size:
                raise ValueError(f"{path}: XVAG chunk exceeds header")
            chunks.append((tag.decode("ascii"), length))
            if tag == b"fmat":
                if length != 40:
                    raise ValueError(f"{path}: unrecognized fmat size")
                fmat = struct.unpack_from("<10I", data, chunk_cursor)
            chunk_cursor += length
            # Observed chunks are aligned to four bytes relative to XVAG start.
            chunk_cursor += (-(chunk_cursor - offset)) % 4
        if fmat is None:
            raise ValueError(f"{path}: missing fmat")
        entries.append(Entry(name.removesuffix(".xvag"), offset, size, duration,
                             data[offset + 0xB], fmat, tuple(chunks)))
    if cursor + 4 != base or data[cursor:base] != bytes.fromhex("13 ad db ba"):
        raise ValueError(f"{path}: directory terminator mismatch")
    if entries:
        extents = sorted((e.offset, e.offset + e.size) for e in entries)
        if extents[0][0] != base or extents[-1][1] != len(data):
            raise ValueError(f"{path}: bank payload coverage mismatch")
        if any(a[1] != b[0] for a, b in zip(extents, extents[1:])):
            raise ValueError(f"{path}: noncontiguous bank payloads")
    return entries
