# Formats (Ghost of Yōtei PPSA26344, v01.008.000 and v02.002.000)

Everything here was measured on real files of both versions unless marked **inference**. All integers are
little-endian. Where field meanings are unknown, the tools preserve the bytes.

## Files per voice language

| Path (inside the app folder) | What |
|---|---|
| `cache_ps5/lang_<language>_audio.xpps` | **Audio index** (KCAP container): every clip with its voice file, offset and duration |
| `cache_ps5/loc/<bank>_<code>.larc` | **Voice banks**: XVAG/ATRAC9 clips. `code` = en, fr, ja, de, es, la, it, pl, pt, br, ru; plus a shared `noloc.larc` |
| `cache_ps5/lang_<language>_text.xpps` | Subtitles/text (not involved) |

| | v1.008 | v2.002 |
|---|---|---|
| English banks | 411 | 524 |
| Banks per other language | 397–398 | 506–507 |
| `lang_english_audio.xpps` | 3,885,104 bytes | 4,629,560 bytes |

## Audio index (`.xpps`, KCAP)

### Header

```
0x00  "KCAP"   u16 41   u16 17 (kind: audio index)
0x08  u32 revision: 2220 (v1.008) / 2256 (v2.002). The meaning of the number is unknown; it changes with the release, not the language
0x0c  u32 0x10000, u64 0, u64 5
0x20  16 bytes: differ per language and version. Not an MD5 of the payload; unknown. Kept from the template, accepted by the game
0x40  u32 0x50, u32 0xc0, u32 0x110, then at 0x4c an unaligned u64 data_len: 0x110 + data_len == file size
      0x94 / 0xc0 / 0xfc hold the data end; 0xec = data end - 16; 0xbc / 0xf8 = tail length (rewritten on repack)
```

### Data section and root

The data section starts at file `0x110`. **Pointers are u64 values relative to `0x110`.** Data `+0` holds `[1, 1]`,
and data `+8` points to the root at data `+0x10`. After 24 zero bytes, the root holds `(pointer u64, count u64)`
pairs:

| Array | v1.008 | v2.002 | Element |
|---|---|---|---|
| Events | ✓ | ✓ | 32 B: u64 event ID, 8 B metadata, u64 pointer to variants, u64 variant count |
| Direct clips | ✓ | ✓ | 32 B: the 24-byte clip record (below) + u64 ID |
| Bit-mask table | — | **new**, 30 × u32 | shared across languages |
| Opaque IDs | 15 × u64 | 23 × u64 | |
| Bank-name pointers | ✓ (root `+0x48`) | ✓ (root `+0x58`) | u64 → NUL-terminated file name (`conv_…_de.larc`) |
| Auxiliary array | — | **new**, 4,835 × 16 B of zeros | one per multi-clip variant (see below) |

So the root grows from 4 to 6 pairs (0x60 → 0x80 bytes), and every array after the first moves.

### Variants: 56 bytes (v1.008) → 48 bytes (v2.002)

| Offset | v1.008 | v2.002 |
|---|---|---|
| `+0x00` | u64 selector ID | same |
| `+0x08` | u64 pointer to clips | same |
| `+0x10` | u64 clip count | same |
| `+0x18` | u64 second ID | same |
| `+0x20` | 4 bytes | 4 bytes |
| `+0x24` | u32 `0xffffffff` | u32 index into the auxiliary array (multi-clip variants, consecutive 0…4834), else `0xffffffff` |
| `+0x28` | u64 0 | u32 `0x0000ffff`, u32 `0xffffc7c0` |
| `+0x30` | u32 `0xffffc7c0`, u32 0 | — |

### Clip record: 24 bytes, unchanged

| Offset | Field |
|---|---|
| `+0x00` | u64 pointer to the clip name (without `.xvag`) |
| `+0x08` | u16 duration in centiseconds = `round(samples * 100 / sample_rate)` (a handful of exact ties differ by 1 in native data) |
| `+0x0a` | u16 selector/ordinal (shared across languages) |
| `+0x0c` | 4 bytes, often `00 3c …` (half-float 1.0; **inference:** gain); shared across languages |
| `+0x10` | u32 index into the bank-name array |
| `+0x14` | u32 **absolute** byte offset of the clip's XVAG inside that `.larc` |

### Identity and cross-language facts

- A clip occurrence is identified by `(event ID, variant ID, clip name)`. All 33,376 v1.008 occurrences exist in
  v2.002 (40,641), so matching by ordinal would misassign thousands.
- Within one release, events, variants and the shared arrays are byte-identical across languages. Only the clip
  records' duration, bank and offset differ (plus bank tables and pointers).
- A non-English index is **not** single-language. The old German index references 397 `_de` banks, 50 `_en` banks
  and `noloc.larc`; 8,456 of its references play English, 1,126 play noloc.
- New content also appears inside existing banks (e.g. `conv_bountybr`: 158 → 198 clips).

### KNLI pointer relocations

The section at `0x110 + u32(file[0xc0])` is tagged `KNLI`. It is followed by a u32 payload length; the u16 word
count is at chunk `+16`, and the words start at chunk `+20`:
- `0xc000 | page`: select page base `page * 0x20000`.
- A word `< 0x8000`: the pointer slot is at `0x110 + page_base + word * 4`.
- `0x8000 | n`: `n` more slots, each 8 bytes after the previous one.

This accounts for every pointer slot found by walking the graph, and re-encoding reproduces the original stream byte
for byte in all seven non-empty indexes. The `DIC` / `DNE` chunks after it are identical everywhere and are kept.

### Why the old index crashes v2.002

The v2.002 executable loads `lang_%s_audio.xpps` at start-up. The object's bank-name registration callback reads the
bank array from root `+0x58` (pointer) and `+0x60` (count); v1.008 used `+0x48/+0x50`. In an old-layout file, those
bytes are `0` and `0x2964405b` (the start of the first event record). That gives a NULL pointer with a huge count →
SIGSEGV 1.4 s after launch, with exactly those values in `r13` and `r15` of the crash dump. Changing the revision
number alone can't fix it; the whole graph layout must match.

## Voice banks (`.larc`)

```
0x00  "cral"  u16 0x0105 (both versions)  u16 0
0x08  16 bytes, differ per file/language/version (not MD5 of anything obvious; unknown; never needed)
0x18  u32 payload start      0x1c  u32 entry count
0x20  entries: u32 name length, name (".xvag"), u32 offset relative to payload start, u32 XVAG size, f32 duration (s)
      then 4 bytes 13 ad db ba, then the XVAG payloads, contiguous to EOF
```

XVAG: `"XVAG"`, u32 header length, version byte at `+0x0b`: **0x62** in v1.008, **0x63** in v2.002. Chunks start at
`+0x20`:
- `fmat` (40 bytes): channels 1, codec 9 = **ATRAC9**, sample count, 48000 Hz, data size.
- `a9in`: frame size 256, 1024 samples per frame, config `FE 70 07 F0`, encoder delay 256. Same in both versions.
- v2.002 adds a 32-byte `lyr ` chunk after `fmat`: 24-byte payload of 1, 0, 0, 9, 48000 and a per-bank float
  (**inference:** gain).

v2.002 re-encoded the audio (the payloads differ throughout), but with the same codec settings. Its XVAG reader
accepts version bytes below 0x64, and **unchanged v1.008 banks play correctly in v2.002**, which was confirmed in game.
No bank conversion is needed.

## ShadowMount+ backport overlay

ShadowMount+ 1.7 mounts `<scan path>/backports/<TITLE_ID>/` as **unionfs** (`copymode=transparent`, read-only if the
image is) over `/system_ex/app/<TITLE_ID>` right after the image's nullfs mount, at every launch
(`src/sm_scan.c mount_backport_overlay_for_title`, `src/sm_filesystem.c mount_backport_overlay`). The folder name must
equal or start with the title ID; any other name is ignored, which makes on/off a rename. If the mount fails, the
launch fails.
