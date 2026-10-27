#!/usr/bin/env python3
"""Build the Ghost of Yotei voice-language overlay (ShadowMount+ backport folder) from two releases.

Inputs (read-only, never written):
  --new  the v02.002.000 app folder whose voice files for --lang are 0-byte placeholders
  --old  a v01.008.000 app folder that has real voices for --lang

Output (--out, must not exist): <out>/<TITLE>/cache_ps5/lang_<lang>_audio.xpps  (new-layout audio index)
                                <out>/<TITLE>/cache_ps5/loc/*_<code>.larc        (old voice banks, unchanged)
                                <out>/manifest.json                               (hashes, counts, census)

How: the v2.002 English audio index is the template (its layout is what the game expects). For every clip that the
old index of that language plays from a bank of that language, its duration, bank and XVAG offset are transplanted
(see kcap/ and docs/FORMATS.md). Clips without data in that language keep their current
English/noloc asset. Then every one of the ~41k clip references is checked against the real bank files (name,
offset, duration); any mismatch aborts before anything is written.

Only --lang german is tested in game (2026-10-28). Python 3.9+, stdlib only. Usage:
  python build_overlay.py --new NEW_APP0 --old OLD_APP --out OUTDIR [--lang german] [--copy] [--title PPSA26344]
"""
import argparse, collections, hashlib, json, os, shutil, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcap"))
from index_model import Index  # noqa: E402
from larc_model import read_larc  # noqa: E402
from transplant import make_plan, repack, validate_transplant  # noqa: E402

LANGUAGES = {"german": "de", "spanish": "es", "italian": "it", "polish": "pl", "portuguese": "pt",
             "brazilian": "br", "russian": "ru", "latino": "la", "french": "fr", "japanese": "ja"}
# The German build that was tested in game (2026-10-28): input index hashes -> output index hash.
TESTED_GERMAN = {
    "new_english_index": "c2ccfbc2af0f3c3f4cd137349231b946fef324d7d3fe66133a20e010c5fa64ec",
    "old_german_index": "bd4548c3533757db7beaf4a1832cdc3cc6fe9da83f6478cd439a1933be6df835",
    "output_index": "7cff560a81745eeb768336e46db13ed917b148878c4cb87cef913410bb7c473e",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def census(result, old_loc, new_loc, suffix):
    """Resolve every clip reference against the real banks."""
    cache, counts, problems, ties = {}, collections.Counter(), [], []
    for clip in result.clips + result.direct:
        gen = "old" if clip.bank.endswith(suffix) else "new"
        path = os.path.join(old_loc if gen == "old" else new_loc, clip.bank)
        if path not in cache:
            cache[path] = {e.name: e for e in read_larc(path)} if os.path.getsize(path) else {}
        entry = cache[path].get(clip.name)
        if entry is None:
            problems.append(("clip not in bank", gen, clip.bank, clip.name))
            continue
        if entry.offset != clip.offset:
            problems.append(("offset mismatch", gen, clip.bank, clip.name, clip.offset, entry.offset))
            continue
        samples, rate = entry.fmat[2], entry.fmat[5]
        want = (samples * 200 + rate) // (2 * rate)  # round(samples * 100 / rate), half up
        got = clip.duration & 0xFFFF
        if got != want:
            # The game's own tooling rounds exact ties differently in a handful of clips (native data too).
            if abs(got - want) == 1:
                ties.append((gen, clip.bank, clip.name, got, want))
            else:
                problems.append(("duration mismatch", gen, clip.bank, clip.name, got, want))
                continue
        counts[f"{gen}_xvag_{entry.version:#x}"] += 1
    old_opened = sum(1 for p in cache if p.startswith(old_loc))
    return dict(counts), problems, ties, old_opened, len(cache)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", required=True, help="v02.002.000 app folder (voices for --lang empty)")
    ap.add_argument("--old", required=True, help="v01.008.000 app folder (voices for --lang present)")
    ap.add_argument("--out", required=True, help="output folder (must not exist)")
    ap.add_argument("--lang", default="german", choices=sorted(LANGUAGES),
                    help="voice language to restore (only german is tested in game)")
    ap.add_argument("--title", default="PPSA26344")
    ap.add_argument("--copy", action="store_true", help="copy banks instead of hard-linking them")
    a = ap.parse_args()
    if os.path.exists(a.out):
        sys.exit(f"refusing: {a.out} exists")
    t0 = time.time()
    suffix = f"_{LANGUAGES[a.lang]}.larc"
    new_en = os.path.join(a.new, "cache_ps5", "lang_english_audio.xpps")
    new_idx = os.path.join(a.new, "cache_ps5", f"lang_{a.lang}_audio.xpps")
    old_idx = os.path.join(a.old, "cache_ps5", f"lang_{a.lang}_audio.xpps")
    new_loc, old_loc = os.path.join(a.new, "cache_ps5", "loc"), os.path.join(a.old, "cache_ps5", "loc")
    if os.path.getsize(new_idx) != 0:
        print(f"note: {new_idx} is not empty ({os.path.getsize(new_idx)} bytes); this release may already have {a.lang}")
    if os.path.getsize(old_idx) == 0:
        sys.exit(f"{old_idx} is empty: the old release has no {a.lang} voices")

    english, source = Index.read(new_en, "new"), Index.read(old_idx, "old")
    if english.u32(8) != 2256 or source.u32(8) != 2220:
        print(f"warning: unexpected KCAP revisions new={english.u32(8)} old={source.u32(8)} (tested: 2256 / 2220)")
    plan, plan_counts = make_plan(english, source, suffix)
    result = Index(repack(english, plan), "new", a.lang)
    structure = validate_transplant(english, result, plan)
    counts, problems, ties, old_banks_opened, banks_opened = census(result, old_loc, new_loc, suffix)
    if problems:
        for p in problems[:20]:
            print("  PROBLEM", p)
        sys.exit(f"census failed: {len(problems)} problems, nothing written")

    root = os.path.join(a.out, a.title, "cache_ps5")
    os.makedirs(os.path.join(root, "loc"))
    index_out = os.path.join(root, f"lang_{a.lang}_audio.xpps")
    with open(index_out, "wb") as f:
        f.write(result.data)
    banks = sorted({c.bank for c in result.clips + result.direct if c.bank.endswith(suffix)})
    for bank in banks:
        src, dst = os.path.join(old_loc, bank), os.path.join(root, "loc", bank)
        if a.copy:
            shutil.copyfile(src, dst)
        else:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "title": a.title,
        "language": a.lang,
        "inputs": {"new_english_index": [new_en, sha256(new_en)], f"old_{a.lang}_index": [old_idx, sha256(old_idx)]},
        "output_index": {"path": index_out, "bytes": len(result.data), "sha256": sha256(index_out)},
        "plan": plan_counts,
        "structure": structure,
        "census": {"references": len(result.clips) + len(result.direct), "ok_by_generation": counts,
                   "rounding_ties": len(ties), "banks_opened": banks_opened, "old_banks_opened": old_banks_opened},
        "banks": [[b, os.path.getsize(os.path.join(root, "loc", b))] for b in banks],
    }
    json.dump(manifest, open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print(json.dumps({k: v for k, v in manifest.items() if k != "banks"}, indent=1))
    size = sum(s for _, s in manifest["banks"]) / 1e9
    print(f"\n{len(banks)} {a.lang} banks ({size:.2f} GB) in {root}")
    inputs = {k: v[1] for k, v in manifest["inputs"].items()}
    same_inputs = (inputs.get("new_english_index") == TESTED_GERMAN["new_english_index"]
                   and inputs.get("old_german_index") == TESTED_GERMAN["old_german_index"])
    if a.lang == "german" and same_inputs:
        ok = manifest["output_index"]["sha256"] == TESTED_GERMAN["output_index"]
        print("inputs and output match the in-game-tested build" if ok else
              "WARNING: same inputs as the tested build but a different output - do not use")
    elif a.lang == "german":
        print("inputs differ from the in-game-tested build: census passed, but test carefully")
    else:
        print(f"{a.lang}: built and census-checked offline, NOT tested in game")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
