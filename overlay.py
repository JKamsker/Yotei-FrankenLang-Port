#!/usr/bin/env python3
"""Deploy and switch a ShadowMount+ backport overlay on the PS5 through ps5upload's Minion API (:9180).

ShadowMount+ (1.7) mounts <scan path>/backports/<TITLE>/ as a unionfs layer over an image game at every launch;
files there replace the game's files of the same relative path. A folder whose name doesn't start with the title id
(e.g. "_off-…", "_incoming-…") is ignored. So switching = renaming, and nothing in the game image is ever written.

  python overlay.py status                         list the backports folder
  python overlay.py deploy LOCAL_TITLE_DIR          upload to backports/_incoming-<TITLE>/ (resumable, size-checked)
  python overlay.py on                              activate _incoming-<TITLE> (or --name FOLDER) as <TITLE>
  python overlay.py off                             rename the active <TITLE> to _off-<timestamp>-<TITLE>
Options: --host IP (or env PS5_HOST)  --title PPSA26344  --root /mnt/ext1/homebrew/backports  --dry-run

Takes effect at the next game launch. Close the game first when switching. Stdlib only.
Talks only to the ps5upload Minion web API (port 9180), nothing else.
"""
import argparse, json, os, sys, time, urllib.error, urllib.request

BASE = None  # set in main() from --host / PS5_HOST


def token():
    return json.load(urllib.request.urlopen(f"{BASE}/api/token", timeout=10))["token"]


def call(route, payload=None, timeout=120):
    req = urllib.request.Request(BASE + route, data=json.dumps(payload or {}).encode(),
                                 headers={"X-PS5U-Token": token(), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode(errors="replace")[:500]}


def listdir(path):
    entries, nxt = [], 0
    while True:
        r = call("/api/fs/list", {"path": path, "offset": nxt})
        if "entries" not in r:
            return None
        entries += r["entries"]
        if not r.get("more"):
            return entries
        nxt = r["next"]


def upload(local, remote, size):
    tok = token()
    begin = json.dumps({"path": remote, "size": size, "overwrite": False}).encode()
    req = urllib.request.Request(BASE + "/api/upload/begin", data=begin,
                                 headers={"X-PS5U-Token": tok, "Content-Type": "application/json"})
    uid = json.load(urllib.request.urlopen(req, timeout=60))["id"]
    with open(local, "rb") as f:
        req = urllib.request.Request(f"{BASE}/api/upload/data?id={uid}", data=f.read(),
                                     headers={"X-PS5U-Token": tok, "Content-Type": "application/octet-stream"})
    written = json.load(urllib.request.urlopen(req, timeout=1800)).get("size")
    if written != size:
        sys.exit(f"size mismatch after upload: {remote} ({written} != {size})")


def mkdirs(path, dry):
    parts, cur = path.strip("/").split("/"), ""
    for p in parts:
        cur += "/" + p
        if call("/api/fs/stat", {"path": cur}).get("kind") == "dir":
            continue
        print("mkdir", cur)
        if not dry:
            r = call("/api/fs/mkdir", {"path": cur})
            if "http_error" in r:
                sys.exit(f"mkdir failed: {cur}: {r}")


def rename(src, dst, dry):
    print(f"rename {src} -> {dst}")
    if not dry:
        r = call("/api/fs/rename", {"from": src, "to": dst})
        if "http_error" in r:
            sys.exit(f"rename failed: {r}")


def cmd_deploy(a):
    local_root = os.path.abspath(a.local)
    target = f"{a.root}/_incoming-{a.title}"
    files = []
    for d, _, names in os.walk(local_root):
        for n in sorted(names):
            p = os.path.join(d, n)
            files.append((p, os.path.relpath(p, local_root).replace(os.sep, "/"), os.path.getsize(p)))
    total = sum(s for *_, s in files)
    print(f"{len(files)} files, {total / 1e9:.2f} GB -> {target}")
    done = sent = 0
    for local, rel, size in files:
        remote = f"{target}/{rel}"
        st = call("/api/fs/stat", {"path": remote})
        if st.get("kind") == "file" and st.get("size") == size:
            done += 1
            continue  # resume: already there with the right size
        if st.get("kind") == "file":
            sys.exit(f"{remote} exists with size {st.get('size')} != {size}; delete it and retry")
        mkdirs(remote.rsplit("/", 1)[0], a.dry_run)
        if not a.dry_run:
            upload(local, remote, size)
        done += 1
        sent += size
        if done % 25 == 0 or done == len(files):
            print(f"  {done}/{len(files)} files, {sent / 1e9:.2f} GB sent")
    if a.dry_run:
        return
    bad = [rel for _, rel, size in files if call("/api/fs/stat", {"path": f"{target}/{rel}"}).get("size") != size]
    if bad:
        sys.exit(f"verification failed for {len(bad)} files, e.g. {bad[:3]}")
    print(f"verified {len(files)} files on the console. Activate with: overlay.py on")


def cmd_on(a):
    src = f"{a.root}/{a.name or '_incoming-' + a.title}"
    if call("/api/fs/stat", {"path": src}).get("kind") != "dir":
        sys.exit(f"{src} not found (see: overlay.py status)")
    active = f"{a.root}/{a.title}"
    if call("/api/fs/stat", {"path": active}).get("kind") == "dir":
        rename(active, f"{a.root}/_off-{time.strftime('%Y%m%d-%H%M%S')}-{a.title}", a.dry_run)
    rename(src, active, a.dry_run)
    print("active at the next launch")


def cmd_off(a):
    active = f"{a.root}/{a.title}"
    if call("/api/fs/stat", {"path": active}).get("kind") != "dir":
        sys.exit(f"no active overlay at {active}")
    rename(active, f"{a.root}/_off-{time.strftime('%Y%m%d-%H%M%S')}-{a.title}", a.dry_run)
    print("inactive from the next launch")


def cmd_status(a):
    entries = listdir(a.root)
    if entries is None:
        print(f"{a.root} does not exist")
        return
    for e in entries:
        flag = "ACTIVE" if e["name"] == a.title or (e["name"].startswith(a.title) and e["kind"] == "dir") else ""
        print(f"  {e['kind']:4} {e['size']:>12}  {e['name']}  {flag}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["status", "deploy", "on", "off"])
    ap.add_argument("local", nargs="?", help="deploy: local folder whose contents become backports/<TITLE>/")
    ap.add_argument("--host", default=os.environ.get("PS5_HOST"), help="PS5 IP address (default: env PS5_HOST)")
    ap.add_argument("--title", default="PPSA26344")
    ap.add_argument("--root", default="/mnt/ext1/homebrew/backports")
    ap.add_argument("--name", help="on: folder name inside --root to activate (default _incoming-<TITLE>)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.host:
        ap.error("give the PS5 address with --host or the PS5_HOST environment variable")
    global BASE
    BASE = f"http://{a.host}:9180"
    if a.command == "deploy" and not a.local:
        ap.error("deploy needs LOCAL_TITLE_DIR (e.g. OUT/PPSA26344)")
    {"status": cmd_status, "deploy": cmd_deploy, "on": cmd_on, "off": cmd_off}[a.command](a)


if __name__ == "__main__":
    main()
