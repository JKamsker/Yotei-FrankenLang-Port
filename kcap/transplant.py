"""Transplant per-clip voice metadata from an old-layout index into a new-layout template.

Preserves the target graph, variant numbers, gains, and schema-specific flags.
Only duration (low 16 bits), bank path, and XVAG offset come from source clips.
Unmatched clips retain target English assets; no silence behavior is assumed.
"""
from collections import Counter
import struct

from index_model import DATA_BASE
from relocation import encode_fixups, read_fixups


def language_metadata(clip):
    return clip.duration & 0xFFFF, clip.bank, clip.offset


def make_plan(target, source, source_suffix=None, bank_aliases=None):
    sources = source.grouped()
    plan, counts = {}, Counter()
    for clip in target.clips + target.direct:
        candidates = sources.get(clip.key, [])
        choices = {language_metadata(c) for c in candidates}
        if len(choices) > 1:
            raise ValueError(f"ambiguous source metadata: {clip.key}")
        if not choices:
            counts["unmatched_keep_target"] += 1
            continue
        replacement = next(iter(choices))
        if source_suffix and not replacement[1].endswith(source_suffix):
            counts["other_source_bank_keep_target"] += 1
            continue
        if bank_aliases:
            replacement = (replacement[0], bank_aliases.get(replacement[1], replacement[1]),
                           replacement[2])
        plan[clip.position] = replacement
        counts["matched_records"] += 1
        counts["duration_changes"] += replacement[0] != (clip.duration & 0xFFFF)
        counts["offset_changes"] += replacement[2] != clip.offset
    return plan, dict(counts)


def repack(target, plan):
    """Append bank table and strings without moving the target's existing graph.

    Old table/string storage remains valid but unreachable from the bank root.
    Original pointer fixups remain valid, and new table pointers are added.
    Unknown content identity at 0x20 is retained: runtime acceptance is unproved.
    """
    banks = list(target.banks)
    for _, bank, _ in plan.values():
        if bank not in banks:
            banks.append(bank)
    bank_ids = {name: i for i, name in enumerate(banks)}
    body = bytearray(target.data[:target.end])
    for position, (duration, bank, offset) in plan.items():
        struct.pack_into("<H", body, position + 8, duration)
        struct.pack_into("<II", body, position + 16, bank_ids[bank], offset)
    pointers = set(read_fixups(target))
    if banks != target.banks:
        body += bytes((-len(body)) % 8)
        new_table = len(body)
        body += bytes(len(banks) * 8)
        bank_strings = {bank: target.u64(p)
                        for bank, p in zip(target.banks, target.bank_positions)}
        for bank in banks:
            if bank not in bank_strings:
                bank_strings[bank] = len(body) - DATA_BASE
                body += bank.encode("ascii") + b"\0"
        for i, bank in enumerate(banks):
            position = new_table + i * 8
            struct.pack_into("<Q", body, position, bank_strings[bank])
            pointers.add(position)
        struct.pack_into("<QQ", body, target.bank_field,
                         new_table - DATA_BASE, len(banks))
    body += bytes((-len(body)) % 8)
    relocation_start = len(body)
    old_chunk_length = 8 + target.u32(target.end + 4)
    tail = encode_fixups(pointers) + target.data[target.end + old_chunk_length:]
    body += tail
    relative_end = relocation_start - DATA_BASE
    struct.pack_into("<Q", body, 0x4C, len(body) - DATA_BASE)
    for field in (0x94, 0xC0, 0xFC):
        struct.pack_into("<I", body, field, relative_end)
    struct.pack_into("<I", body, 0xEC, relative_end - 16)
    for field in (0xBC, 0xF8):
        struct.pack_into("<I", body, field, len(tail))
    return bytes(body)


def validate_transplant(target, result, plan):
    """Check every output clip, scalar graph field, pointer, and retained byte."""
    if result.version != target.version:
        raise ValueError("output schema mismatch")
    before = target.clips + target.direct
    after = result.clips + result.direct
    if len(before) != len(after):
        raise ValueError("clip cardinality changed")
    for old, new in zip(before, after):
        expected = plan.get(old.position, language_metadata(old))
        if new.name != old.name or language_metadata(new) != expected:
            raise ValueError(f"transplant semantic mismatch: {old.key}")
        if old.duration >> 16 != new.duration >> 16 or old.properties != new.properties:
            raise ValueError("variant, gain, or new flags changed")
    actual_fixups = set(read_fixups(result))
    if not result.pointer_locations <= actual_fixups:
        raise ValueError("missing live pointer fixup")
    # Orphaned original bank pointers must be the only additional fixups.
    expected_extra = set(target.bank_positions) - set(result.bank_positions)
    if actual_fixups - result.pointer_locations != expected_extra:
        raise ValueError("unexplained pointer fixups")
    allowed = set()
    for position in plan:
        allowed.update(range(position + 8, position + 10))
        allowed.update(range(position + 16, position + 24))
    if result.banks != target.banks:
        allowed.update(range(target.bank_field, target.bank_field + 16))
    changed = {i for i in range(DATA_BASE, target.end)
               if target.data[i] != result.data[i]}
    if not changed <= allowed:
        raise ValueError("unintended original graph/data mutation")
    return dict(verified_records=len(after), added_banks=len(result.banks) - len(target.banks),
                fixups=len(actual_fixups), changed_original_data_bytes=len(changed),
                orphaned_bank_pointer_slots=len(expected_extra),
                retained_content_identity=result.data[0x20:0x30].hex())
