# Yotei-FrankenLang-Port

Restore a missing voice language in **Ghost of Yōtei** (PS5, `PPSA26344`) **v02.002.000** by porting the voice-over
from **v01.008.000**. It runs on a jailbroken PS5 through ShadowMount+'s runtime overlay, without touching the game
image.

**Status:** German works in game (tested 2026-10-28). The other languages build and pass the offline checks, but are
untested in game.

> This repository contains **no game files**. You need your own dumps of both game versions. Not affiliated with
> Sony Interactive Entertainment or Sucker Punch Productions. Use at your own risk.

## The problem

Some v02.002.000 dumps contain only English, French and Japanese voice-over. The other voice languages (German,
Spanish, Latin-American Spanish, Italian, Polish, Portuguese, Brazilian Portuguese, Russian) are there as **0-byte
placeholder files**. With one of those languages selected, the game is completely silent. Older dumps of v01.008.000
often have all voice languages.

Simply copying the old files over doesn't work. The audio index format changed between the versions, and v2.002
crashes at start-up when it reads the old index (details in [docs/FORMATS.md](docs/FORMATS.md)).

## The solution

1. Take the v2.002 **English audio index** as a template: it has the layout and content the new game expects.
2. For every clip that the old index of your language plays from its own language's files, write that clip's
   duration, voice file and offset into the template. Everything else stays byte-identical. Clips that only exist
   in v2.002 keep their English audio.
3. Check all ~41,000 clip references against the real voice files. The build refuses on any mismatch.
4. Put the new index plus the old, **unchanged** voice files into a ShadowMount+ **backport overlay** folder. At every
   launch, ShadowMount+ mounts that folder as a unionfs layer over the game image.

Result for German: 23,996 of 41,160 clip references play the original German recordings. The rest (content that is
new in v2.002, or lines the old German version itself played in English) plays in English.

## Requirements

- **PS5:** jailbroken, with **[ShadowMount+](https://github.com/drakmor/ShadowMountPlus) 1.7** (backport overlay
  for image games) and **[ps5upload](https://github.com/phantomptr/ps5upload)** (its Minion web API on port 9180 is
  used for uploading). Tested: FW 13.60, ShadowMount+ 1.7beta7, ps5upload 8.4.0.
- **The game:** v02.002.000 running as a ShadowMount+ **image** (tested: UFS2 `.ffpkg`).
- **Two extracted app folders:**
  - v02.002.000 with the empty language (`…/PPSA26344-app0`, containing `eboot.bin`, `cache_ps5/`, `sce_sys/`).
  - v01.008.000 with that language present (`…/PPSA26344-app`).
- **Python 3.9+,** standard library only. Run the build where both folders are on a local disk.

Check your dumps: `cache_ps5/lang_german_audio.xpps` must be **0 bytes in v2.002** and **~3.9 MB in v1.008**, and
`cache_ps5/loc/*_de.larc` must be non-empty in v1.008.

## Usage

```bash
# 1. Build (offline, a few seconds). Writes only --out (must not exist). Voice files are hard-linked if --out is on
#    the same drive as --old, otherwise copied (~1.6 GB).
python build_overlay.py --new /path/to/v2.002/PPSA26344-app0 --old /path/to/v1.008/PPSA26344-app \
                        --out /path/to/yotei-de-overlay [--lang german]

# 2. Upload to the console (resumable, every file size verified) into backports/_incoming-PPSA26344/
python overlay.py --host 192.168.x.y deploy /path/to/yotei-de-overlay/PPSA26344

# 3. Switch on, check, switch off (renames only; takes effect at the next game launch)
python overlay.py --host 192.168.x.y on
python overlay.py --host 192.168.x.y status
python overlay.py --host 192.168.x.y off
```

`--host` can also be given as the environment variable `PS5_HOST`. The overlay root defaults to
`/mnt/ext1/homebrew/backports`. It must be the `backports` folder of the ShadowMount+ scan path that the game image is
in; change it with `--root`.

In game: *Settings → Audio → voice language*. ShadowMount+'s debug log confirms each launch:
`[IMG] backport overlay mounted (ro): /mnt/ext1/homebrew/backports/PPSA26344 -> /system_ex/app/PPSA26344`.
If the overlay can't be mounted, the launch fails; run `overlay.py off`.

### Expected output (German, tested inputs)

| | |
|---|---|
| Input `v2.002 cache_ps5/lang_english_audio.xpps` | sha256 `c2ccfbc2af0f3c3f4cd137349231b946fef324d7d3fe66133a20e010c5fa64ec` |
| Input `v1.008 cache_ps5/lang_german_audio.xpps` | sha256 `bd4548c3533757db7beaf4a1832cdc3cc6fe9da83f6478cd439a1933be6df835` |
| Output `lang_german_audio.xpps` | 4,647,764 bytes, sha256 `7cff560a81745eeb768336e46db13ed917b148878c4cb87cef913410bb7c473e` |
| Plan | 23,996 references → German; 6,598 without a German counterpart + 10,566 that the old German index itself played from English/noloc banks → kept |
| Census | 41,160 references resolved (23,996 in 397 old German banks, 17,164 in current banks); 5 rounding ties of ±1 cs, which native data has too |
| Voice files | 397 `*_de.larc`, 1,602,033,449 bytes |

With exactly these inputs the script prints `inputs and output match the in-game-tested build`.

### Other languages

`--lang spanish | latino | italian | polish | portuguese | brazilian | russian` builds the same way. All pass the
offline census, but are **not tested in game**. Several languages can be active at once: deploy each build into the
same `_incoming-PPSA26344` folder (file names don't collide), then `on`.

## How it works

See **[docs/FORMATS.md](docs/FORMATS.md)** for the reverse-engineered formats:
- the KCAP audio index (`.xpps`) and its layout change between v1.008 and v2.002,
- the KNLI pointer relocations,
- the `.larc` voice banks (XVAG/ATRAC9),
- why the old index crashes the new game.

| File | What |
|---|---|
| `build_overlay.py` | Build the overlay: transplant, full census, manifest |
| `overlay.py` | Deploy / on / off / status on the console via ps5upload's Minion API |
| `kcap/index_model.py` | KCAP audio-index parser (old and new layout) |
| `kcap/relocation.py` | KNLI pointer-relocation decoder/encoder |
| `kcap/transplant.py` | Transplant, repack and structural validation |
| `kcap/larc_model.py` | `.larc` bank directory and XVAG reader |

## Limitations

- Lines that are new in v2.002 (the *Echoes of Sekigahara* story, *Most Wanted*, new bounty and campfire lines,
  new clips inside old banks) have no recording in v1.008 and play in English.
- Any other game version or dump needs a rebuild and a test. The script warns about unknown KCAP revisions
  (tested: v1.008 = 2220, v2.002 = 2256).
- The 16 bytes at KCAP offset `0x20` are kept from the English template. Their meaning is unknown, and the game
  accepts them.

## Credits

- The runtime overlay is ShadowMount+'s backport feature by [drakmor](https://github.com/drakmor/ShadowMountPlus).
- The format analysis and the converter were done with a group of AI reviewers (OpenAI Codex models) and Claude.

## License

MIT, see [LICENSE](LICENSE).
