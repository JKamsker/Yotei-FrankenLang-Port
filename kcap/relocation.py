"""KCAP KNLI pointer fixups, inferred and round-trip checked on seven indexes."""
import struct

from index_model import DATA_BASE

PAGE_BYTES = 0x20000
WORD_UNIT = 4
POINTER_BYTES = 8
PAGE_TAG = 0xC000
RUN_TAG = 0x8000
TAG_MASK = 0xC000
VALUE_MASK = 0x3FFF


def read_fixups(index):
    data, start = index.data, index.end
    length = index.u32(start + 4)
    count = index.u32(start + 16)
    index.require(start + 20 + count * 2 <= start + 8 + length, "KNLI word bounds")
    words = struct.unpack_from(f"<{count}H", data, start + 20)
    result, page = [], None
    for word in words:
        if word & TAG_MASK == PAGE_TAG:
            page = (word & VALUE_MASK) * PAGE_BYTES
        elif word & RUN_TAG:
            index.require(bool(result), "KNLI run without first pointer")
            for _ in range(word & VALUE_MASK):
                result.append(result[-1] + POINTER_BYTES)
        else:
            index.require(page is not None, "KNLI page required")
            result.append(DATA_BASE + page + word * WORD_UNIT)
    index.require(result == sorted(set(result)), "KNLI sorted unique pointers")
    for p in result:
        index.require(DATA_BASE <= p <= index.end - POINTER_BYTES, "fixup bounds")
        target = DATA_BASE + index.u64(p)
        index.require(DATA_BASE <= target < index.end, "fixup target bounds")
    return result


def encode_fixups(locations):
    """Encode consecutive 8-byte pointers as runs, split at 128-KiB pages."""
    locations = sorted(set(locations))
    words, i, last_page = [], 0, None
    while i < len(locations):
        relative = locations[i] - DATA_BASE
        if relative < 0 or relative % WORD_UNIT:
            raise ValueError("fixup location must be a nonnegative multiple of four")
        page, within = divmod(relative, PAGE_BYTES)
        if page > VALUE_MASK:
            raise ValueError("fixup page exceeds KNLI encoding")
        if page != last_page:
            words.append(PAGE_TAG | page)
            last_page = page
        words.append(within // WORD_UNIT)
        run = 0
        while i + run + 1 < len(locations) and run < VALUE_MASK:
            next_relative = locations[i + run + 1] - DATA_BASE
            if (locations[i + run + 1] != locations[i] + (run + 1) * POINTER_BYTES
                    or next_relative // PAGE_BYTES != page):
                break
            run += 1
        if run:
            words.append(RUN_TAG | run)
        i += run + 1
    payload = struct.pack("<QI", 1, len(words)) + struct.pack(f"<{len(words)}H", *words)
    payload += bytes((-len(payload)) % 4)
    return b"KNLI" + struct.pack("<I", len(payload)) + payload


def original_chunk(index):
    return index.data[index.end:index.end + 8 + index.u32(index.end + 4)]
