"""Measured KCAP audio graph reader; no executable or console interaction."""
from dataclasses import dataclass
from pathlib import Path
import re
import struct

DATA_BASE = 0x110
EVENT_SIZE = 32
CLIP_SIZE = 24
# All voice-language suffixes seen in PPSA26344.
LANGUAGE_CODE = re.compile(r"_(en|fr|ja|de|es|it|pl|pt|br|ru|la)\.larc$")


def canonical_bank(name):
    return LANGUAGE_CODE.sub("_{lang}.larc", name)


@dataclass(frozen=True)
class Clip:
    position: int
    name: str
    duration: int  # Packed: low u16 duration in centiseconds; high u16 variant.
    properties: int  # Packed gain/flags; flags have different version semantics.
    bank_id: int
    offset: int
    bank: str

    @property
    def key(self):
        return canonical_bank(self.bank), self.name

    @property
    def metadata(self):
        return self.duration, self.properties, self.bank, self.offset


class Index:
    def __init__(self, data, version, source="memory"):
        if version not in ("old", "new"):
            raise ValueError("version must be old or new")
        self.data, self.version, self.source = bytes(data), version, str(source)
        self.require(self.data[:4] == b"KCAP", "KCAP magic")
        self.require(self.u64(0x4C) + DATA_BASE == len(data), "declared size")
        self.end = DATA_BASE + self.u32(0xC0)
        self.require(self.data[self.end:self.end + 4] == b"KNLI", "KNLI location")
        self.bank_field = DATA_BASE + (0x58 if version == "old" else 0x68)
        self.pointer_locations = {DATA_BASE + 8}
        root_fields = (0x28, 0x38, 0x48, 0x58) if version == "old" else (
            0x28, 0x38, 0x48, 0x58, 0x68, 0x78)
        self.pointer_locations.update(DATA_BASE + f for f in root_fields)
        bank_start, bank_count = self.array(self.bank_field, 8)
        self.bank_positions = [bank_start + i * 8 for i in range(bank_count)]
        self.pointer_locations.update(self.bank_positions)
        self.banks = [self.string(self.u64(p)) for p in self.bank_positions]
        self.events, self.children, self.clips, self.direct = [], [], [], []
        self._parse_graph()

    @classmethod
    def read(cls, path, version):
        return cls(Path(path).read_bytes(), version, path)

    def require(self, condition, message):
        if not condition:
            raise ValueError(f"{self.source}: {message}")

    def u32(self, position):
        return struct.unpack_from("<I", self.data, position)[0]

    def u64(self, position):
        return struct.unpack_from("<Q", self.data, position)[0]

    def string(self, relative):
        position = DATA_BASE + relative
        self.require(DATA_BASE <= position < self.end, "string pointer bounds")
        end = self.data.find(b"\0", position, min(position + 1024, self.end))
        self.require(end >= position, "terminated string")
        return self.data[position:end].decode("ascii")

    def array(self, field, stride):
        start, count = DATA_BASE + self.u64(field), self.u64(field + 8)
        self.require(DATA_BASE <= start <= self.end, "array start bounds")
        self.require(count <= (self.end - start) // stride, "array end bounds")
        return start, count

    def clip(self, position):
        name_relative, duration, properties, bank_id, offset = struct.unpack_from(
            "<QIIII", self.data, position)
        self.require(bank_id < len(self.banks), "clip bank id bounds")
        self.pointer_locations.add(position)
        return Clip(position, self.string(name_relative), duration, properties,
                    bank_id, offset, self.banks[bank_id])

    def _parse_graph(self):
        child_size = 56 if self.version == "old" else 48
        start, count = self.array(DATA_BASE + 0x28, EVENT_SIZE)
        for i in range(count):
            event = start + EVENT_SIZE * i
            self.events.append(event)
            self.pointer_locations.add(event + 16)
            child_start, child_count = self.array(event + 16, child_size)
            for j in range(child_count):
                child = child_start + child_size * j
                self.children.append(child)
                self.pointer_locations.add(child + 8)
                clip_start, clip_count = self.array(child + 8, CLIP_SIZE)
                self.clips.extend(self.clip(clip_start + CLIP_SIZE * k)
                                  for k in range(clip_count))
        start, count = self.array(DATA_BASE + 0x38, EVENT_SIZE)
        self.direct = [self.clip(start + EVENT_SIZE * i) for i in range(count)]

    def grouped(self, include_direct=True):
        result = {}
        for clip in self.clips + (self.direct if include_direct else []):
            result.setdefault(clip.key, []).append(clip)
        return result

    def summary(self):
        return dict(bytes=len(self.data), events=len(self.events),
                    children=len(self.children), clips=len(self.clips),
                    direct=len(self.direct), bank_names=len(self.banks),
                    unique_clip_keys=len(self.grouped()),
                    pointer_locations=len(self.pointer_locations),
                    schema_value=self.u32(8))
