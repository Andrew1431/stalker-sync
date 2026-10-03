#!/usr/bin/env python3
"""stalker-sync: make several S.T.A.L.K.E.R. Anomaly / G.A.M.M.A. installs byte-identical.

Commands:
  fingerprint  print the xrRazom config/spawn fingerprints from your latest game log
  scan         hash the files the game actually loads -> manifest.json
  diff         compare two manifests (host vs you), grouped by mod
  pack         (host) build a patch zip that turns a friend's install into yours
  apply        (friend) apply that patch zip; replaced/removed files are backed up

Only the Python 3.8+ standard library is used. See README.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

TOOL_VERSION = "1.0.0"
MANIFEST_FORMAT = 1
PATCH_INFO = "stalker-sync-patch.json"

# Only these top-level folders of a mod / of the game root are part of the game.
GAME_TOP_DIRS = {"gamedata", "bin", "db", "mt"}
# Sub-trees of the Anomaly base install worth hashing by default. The level
# archives (db/levels, db/textures, ...) are many GB and never differ between
# correct installs; use --full to include them anyway.
BASE_DEFAULT_PREFIXES = ("gamedata/", "bin/", "mt/", "db/mods/", "db/configs/", "db/patches/", "db/addons/")

# "gameplay" scope: everything that feeds xrRazom's join fingerprints or changes
# game logic. Textures, sounds and models are left out unless --full is given.
GAMEPLAY_EXTS = {
    ".ltx", ".xml", ".script", ".lua", ".spawn", ".ini",
    ".exe", ".dll", ".db0", ".db1", ".db2", ".db3", ".db4", ".db5", ".db6", ".db7", ".db8", ".db9",
    ".xdb0", ".xdb1", ".xdb2", ".xdb3", ".xdb4", ".xdb5", ".xdb6", ".xdb7", ".xdb8", ".xdb9",
}
# Never relevant, even with --full (logs, caches, shader caches, reshade presets...).
ALWAYS_IGNORE = [
    "*.log", "*.bak", "*.bkp", "*.mdmp", "*.dmp", "*.tmp",
    "bin/*.ini", "bin/*.ini.back", "bin/dxgi.log", "bin/reshade-shaders/*",
    "gamedata/shaders_cache/*", "gamedata/shader_cache/*",
]

FINGERPRINT_RE = re.compile(r"\[xrRazom\]\s+(config|spawn) fingerprint:\s*([0-9a-fA-F]+)\s*(\(.*\))?")


# --------------------------------------------------------------------------- helpers

def die(msg: str) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(2)


def norm_key(rel: str) -> str:
    """Game paths are case-insensitive (the engine runs on Windows / Wine)."""
    return rel.replace("\\", "/").strip("/").lower()


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def windows_path_to_local(p: str) -> Path:
    """MO2 stores Windows paths. Under Wine, Z:\\ maps to the Linux root."""
    p = p.replace("\\\\", "\\")
    if os.name != "nt":
        m = re.match(r"^([A-Za-z]):[\\/](.*)$", p)
        if m:
            if m.group(1).upper() != "Z":
                die(f"cannot map Wine drive path {p!r}; pass --anomaly explicitly")
            return Path("/" + m.group(2).replace("\\", "/"))
    return Path(p)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# --------------------------------------------------------------------------- install layout

class Install:
    """Where things live: a Mod Organizer 2 instance (G.A.M.M.A.) and/or a plain Anomaly folder."""

    def __init__(self, gamma: Path | None, anomaly: Path | None, profile: str | None):
        self.gamma = gamma
        self.anomaly = anomaly
        self.profile = profile
        self.mo2_ini: dict[str, str] = {}
        if gamma:
            ini = gamma / "ModOrganizer.ini"
            if not ini.is_file():
                die(f"{ini} not found - is --gamma pointing at the folder with ModOrganizer.exe?")
            for line in read_text(ini).splitlines():
                if "=" in line and not line.startswith(("[", ";")):
                    k, v = line.split("=", 1)
                    self.mo2_ini.setdefault(k.strip(), v.strip())
            if not self.anomaly:
                gp = self.mo2_ini.get("gamePath", "")
                gp = re.sub(r"^@ByteArray\((.*)\)$", r"\1", gp)
                if not gp:
                    die("gamePath missing from ModOrganizer.ini; pass --anomaly")
                self.anomaly = windows_path_to_local(gp)
            if not self.profile:
                sp = re.sub(r"^@ByteArray\((.*)\)$", r"\1", self.mo2_ini.get("selected_profile", ""))
                self.profile = sp or "G.A.M.M.A"
        if not self.anomaly or not self.anomaly.is_dir():
            die(f"Anomaly folder not found: {self.anomaly}")

    @property
    def is_mo2(self) -> bool:
        return self.gamma is not None

    @property
    def mods_dir(self) -> Path:
        return self.gamma / "mods"

    @property
    def overwrite_dir(self) -> Path:
        return self.gamma / "overwrite"

    def enabled_mods(self) -> list[str]:
        """Enabled mods, highest priority first (the order of MO2's modlist.txt)."""
        if not self.is_mo2:
            return []
        modlist = self.gamma / "profiles" / self.profile / "modlist.txt"
        if not modlist.is_file():
            die(f"{modlist} not found - check --profile")
        mods = []
        for line in read_text(modlist).splitlines():
            if line.startswith("+"):
                name = line[1:].strip()
                if name.endswith("_separator"):
                    continue
                if not (self.mods_dir / name).is_dir():
                    print(f"warning: enabled mod has no folder: {name}", file=sys.stderr)
                    continue
                mods.append(name)
        return mods

    def sources(self) -> list[tuple[str, Path, bool]]:
        """(label, folder, is_base) in LOWEST -> HIGHEST priority order."""
        out: list[tuple[str, Path, bool]] = [("@anomaly", self.anomaly, True)]
        if self.is_mo2:
            for name in reversed(self.enabled_mods()):
                out.append((name, self.mods_dir / name, False))
            out.append(("@overwrite", self.overwrite_dir, False))
        return out

    def logs_dir(self) -> Path:
        return self.anomaly / "appdata" / "logs"


# --------------------------------------------------------------------------- hashing

class HashCache:
    """Remembers hashes by (path, size, mtime) so re-scans of 80 GB take seconds."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, list] = {}
        self.dirty = False
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text("utf-8"))
            except (ValueError, OSError):
                self.data = {}

    def get(self, p: Path, st: os.stat_result) -> str:
        key = str(p)
        hit = self.data.get(key)
        if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
            return hit[2]
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self.data[key] = [st.st_size, st.st_mtime_ns, digest]
        self.dirty = True
        return digest

    def save(self) -> None:
        if self.path and self.dirty:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), "utf-8")
            os.replace(tmp, self.path)


def wanted(key: str, is_base: bool, full: bool, extra_ignore: list[str]) -> bool:
    top = key.split("/", 1)[0]
    if top not in GAME_TOP_DIRS or "/" not in key:
        return False
    for pat in ALWAYS_IGNORE + extra_ignore:
        if fnmatch.fnmatch(key, pat.lower()):
            return False
    if is_base and not full and not key.startswith(BASE_DEFAULT_PREFIXES):
        return False
    if full:
        return True
    return os.path.splitext(key)[1] in GAMEPLAY_EXTS


def walk_source(folder: Path):
    """Yield (relative path with original casing, absolute path) under the game dirs of a source."""
    if not folder.is_dir():
        return
    for top in os.scandir(folder):
        if not top.is_dir() or top.name.lower() not in GAME_TOP_DIRS:
            continue
        for dirpath, dirnames, filenames in os.walk(top.path):
            dirnames.sort()
            for fn in sorted(filenames):
                ab = Path(dirpath) / fn
                yield ab.relative_to(folder).as_posix(), ab


def resolve(inst: Install, full: bool, extra_ignore: list[str]) -> dict[str, dict]:
    """Build the virtual file system MO2 would present: key -> winning file + who else provides it."""
    vfs: dict[str, dict] = {}
    for label, folder, is_base in inst.sources():
        for rel, ab in walk_source(folder):
            key = norm_key(rel)
            if not wanted(key, is_base, full, extra_ignore):
                continue
            entry = vfs.get(key)
            if entry is None:
                vfs[key] = {"path": rel, "abs": ab, "owner": label, "providers": [label]}
            else:  # later source = higher priority, it wins
                entry["providers"].append(label)
                entry.update(path=rel, abs=ab, owner=label)
    return vfs


def scan(inst: Install, full: bool, extra_ignore: list[str], cache: HashCache, label: str) -> dict:
    vfs = resolve(inst, full, extra_ignore)
    files = {}
    total = len(vfs)
    for i, key in enumerate(sorted(vfs), 1):
        e = vfs[key]
        st = e["abs"].stat()
        files[key] = {
            "path": e["path"],
            "sha1": cache.get(e["abs"], st),
            "size": st.st_size,
            "owner": e["owner"],
            "providers": list(reversed(e["providers"])),  # highest priority first
        }
        if sys.stderr.isatty() and (i % 500 == 0 or i == total):
            print(f"\r  hashed {i}/{total}", end="", file=sys.stderr, flush=True)
    if sys.stderr.isatty() and total:
        print(file=sys.stderr)
    cache.save()
    return {
        "tool": "stalker-sync",
        "tool_version": TOOL_VERSION,
        "format": MANIFEST_FORMAT,
        "label": label,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "layout": "mo2" if inst.is_mo2 else "flat",
        "profile": inst.profile,
        "scope": "full" if full else "gameplay",
        "ignore": extra_ignore,
        "mods": inst.enabled_mods(),
        "fingerprints": latest_fingerprints(inst.logs_dir()),
        "files": files,
    }


# --------------------------------------------------------------------------- xrRazom fingerprints

def latest_fingerprints(logs: Path) -> dict:
    if not logs.is_dir():
        return {}
    candidates = sorted(logs.glob("xray_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for log in candidates:
        found = {}
        for line in read_text(log).splitlines():
            m = FINGERPRINT_RE.search(line)
            if m:
                found[m.group(1)] = {"value": m.group(2).lower(), "detail": (m.group(3) or "").strip("() ")}
        if found:
            found["log"] = log.name
            found["log_time"] = _dt.datetime.fromtimestamp(log.stat().st_mtime).isoformat(timespec="seconds")
            return found
    return {}


# --------------------------------------------------------------------------- diff

def load_manifest(path: str) -> dict:
    try:
        m = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        die(f"cannot read manifest {path}: {e}")
    if m.get("tool") != "stalker-sync":
        die(f"{path} is not a stalker-sync manifest")
    if m.get("format", 0) > MANIFEST_FORMAT:
        die(f"{path} was made by a newer stalker-sync; please update")
    return m


def compute_diff(host: dict, mine: dict) -> dict:
    hf, mf = host["files"], mine["files"]
    missing = sorted(k for k in hf if k not in mf)
    extra = sorted(k for k in mf if k not in hf)
    changed = sorted(k for k in hf if k in mf and hf[k]["sha1"] != mf[k]["sha1"])
    hm, mm = host.get("mods", []), mine.get("mods", [])
    return {
        "missing": missing,
        "extra": extra,
        "changed": changed,
        "mods_only_host": [m for m in hm if m not in set(mm)],
        "mods_only_mine": [m for m in mm if m not in set(hm)],
        "scope_mismatch": host.get("scope") != mine.get("scope"),
    }


def print_report(host: dict, mine: dict, d: dict, limit: int) -> None:
    hf, mf = host["files"], mine["files"]
    hl, ml = host.get("label", "host"), mine.get("label", "you")
    print(f"stalker-sync diff: {hl} (reference)  vs  {ml}")
    print(f"  scanned files: {len(hf)} vs {len(mf)}   scope: {host.get('scope')} vs {mine.get('scope')}")
    if d["scope_mismatch"]:
        print("  WARNING: manifests were made with different scopes; rescan both the same way.")

    hfp, mfp = host.get("fingerprints", {}), mine.get("fingerprints", {})
    for kind in ("config", "spawn"):
        a, b = hfp.get(kind, {}).get("value"), mfp.get(kind, {}).get("value")
        if a or b:
            state = "MATCH" if a == b else "DIFFERENT"
            print(f"  xrRazom {kind} fingerprint: {a or '?'} vs {b or '?'}  -> {state}")
    print()

    if d["mods_only_host"] or d["mods_only_mine"]:
        print("Mod list differences (fix these in Mod Organizer first, then re-scan):")
        for m in d["mods_only_host"]:
            print(f"  - enable / install:  {m}")
        for m in d["mods_only_mine"]:
            print(f"  - disable:           {m}   (the reference does not use it)")
        print()

    groups: dict[str, list[str]] = {}
    for k in d["missing"]:
        groups.setdefault(hf[k]["owner"], []).append(f"missing   {hf[k]['path']}")
    for k in d["changed"]:
        owner = hf[k]["owner"]
        note = "" if mf[k]["owner"] == owner else f"   (yours comes from {mf[k]['owner']})"
        groups.setdefault(owner, []).append(f"different {hf[k]['path']}{note}")
    for k in d["extra"]:
        groups.setdefault(mf[k]["owner"], []).append(f"extra     {mf[k]['path']}   (not in reference - stale file?)")

    if not groups:
        print("Installs are identical for the scanned scope.")
        return
    total = len(d["missing"]) + len(d["changed"]) + len(d["extra"])
    print(f"File differences: {len(d['missing'])} missing, {len(d['changed'])} different, "
          f"{len(d['extra'])} extra  (total {total}), grouped by mod:")
    for owner in sorted(groups, key=lambda o: (-len(groups[o]), o.lower())):
        lines = groups[owner]
        print(f"\n  [{owner}]  {len(lines)} file(s)")
        for line in lines[:limit] if limit else lines:
            print(f"     {line}")
        if limit and len(lines) > limit:
            print(f"     ... and {len(lines) - limit} more (use --limit 0 to show all)")
    print("\nNext step: the reference player runs `pack` with your manifest and sends you the zip.")


# --------------------------------------------------------------------------- pack / apply

def build_patch(inst: Install, host: dict, mine: dict, out: Path) -> dict:
    d = compute_diff(host, mine)
    if d["scope_mismatch"]:
        die("manifests use different scopes; rescan both with the same options")
    vfs = resolve(inst, host["scope"] == "full", host.get("ignore", []))
    to_send = d["missing"] + d["changed"]
    info = {
        "tool": "stalker-sync", "tool_version": TOOL_VERSION,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "from": host.get("label"), "to": mine.get("label"),
        "scope": host["scope"], "ignore": host.get("ignore", []),
        "put": [], "remove": [],
        "mods_only_host": d["mods_only_host"], "mods_only_mine": d["mods_only_mine"],
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for k in to_send:
            e = vfs.get(k)
            if e is None:
                die(f"{host['files'][k]['path']} is in your manifest but no longer on disk - rescan first")
            want = host["files"][k]["sha1"]
            if HashCache(None).get(e["abs"], e["abs"].stat()) != want:
                die(f"{e['path']} changed since your manifest was made - rescan first")
            z.write(e["abs"], "files/" + e["path"])
            info["put"].append({"path": e["path"], "sha1": want, "owner": host["files"][k]["owner"]})
        for k in d["extra"]:
            info["remove"].append({"path": mine["files"][k]["path"], "sha1": mine["files"][k]["sha1"]})
        z.writestr(PATCH_INFO, json.dumps(info, indent=1))
    return info


def safe_rel(rel: str) -> str:
    p = rel.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or any(part == ".." for part in p.split("/")):
        die(f"refusing unsafe path in patch: {rel!r}")
    if norm_key(p).split("/", 1)[0] not in GAME_TOP_DIRS:
        die(f"refusing path outside game folders: {rel!r}")
    return p


def apply_patch(inst: Install, zip_path: Path, dry_run: bool, backup_root: Path) -> None:
    with zipfile.ZipFile(zip_path) as z:
        try:
            info = json.loads(z.read(PATCH_INFO))
        except KeyError:
            die(f"{zip_path} is not a stalker-sync patch")

        if info["mods_only_host"] or info["mods_only_mine"]:
            print("Note - mod list differs from the reference:")
            for m in info["mods_only_host"]:
                print(f"  reference has enabled: {m}")
            for m in info["mods_only_mine"]:
                print(f"  you have enabled:      {m}")
            print("  The patch still makes the files match, but consider matching the mod list too.\n")

        # Where new/replaced files go. In MO2, overwrite/ always has top priority,
        # so whatever we put there is what the game sees, regardless of load order.
        dest_root = inst.overwrite_dir if inst.is_mo2 else inst.anomaly
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_root / stamp
        vfs = resolve(inst, info["scope"] == "full", info.get("ignore", []))
        moved = 0

        def backup_file(src: Path, label: str, rel: str) -> None:
            nonlocal moved
            tgt = backup / label.lstrip("@") / rel
            print(f"  backup  [{label}] {rel}")
            if not dry_run:
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(tgt))
            moved += 1

        def providers_on_disk(key: str):
            """Every physical copy of a game path across all sources (not just the winner)."""
            for label, folder, _ in inst.sources():
                for rel, ab in _find_case_insensitive(folder, key):
                    yield label, rel, ab

        print(f"{'DRY RUN - ' if dry_run else ''}applying {zip_path.name} "
              f"({len(info['put'])} to write, {len(info['remove'])} to remove)")
        for item in info["remove"]:
            rel = safe_rel(item["path"])
            for label, prel, ab in list(providers_on_disk(norm_key(rel))):
                backup_file(ab, label, prel)

        for item in info["put"]:
            rel = safe_rel(item["path"])
            key = norm_key(rel)
            # If an existing non-winning casing variant sits in overwrite/ (or the flat
            # game dir), move it aside so we don't end up with two copies.
            for prel, ab in _find_case_insensitive(dest_root, key):
                if prel != rel:
                    backup_file(ab, "@overwrite" if inst.is_mo2 else "@anomaly", prel)
            target = dest_root / rel
            if target.exists():
                backup_file(target, "@overwrite" if inst.is_mo2 else "@anomaly", rel)
            print(f"  write   {rel}")
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open("files/" + item["path"]) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

        if dry_run:
            print("\nDry run only - nothing changed. Re-run without --dry-run to apply.")
            return

    # Verify: the resolved view must now contain exactly the reference hashes.
    vfs = resolve(inst, info["scope"] == "full", info.get("ignore", []))
    bad = []
    for item in info["put"]:
        e = vfs.get(norm_key(item["path"]))
        if e is None or HashCache(None).get(e["abs"], e["abs"].stat()) != item["sha1"]:
            bad.append(item["path"])
    for item in info["remove"]:
        if norm_key(item["path"]) in vfs:
            bad.append(item["path"])
    if moved:
        print(f"\nBacked up {moved} file(s) to {backup}")
    if bad:
        print(f"\nWARNING: {len(bad)} file(s) did not verify:")
        for b in bad[:50]:
            print(f"  {b}")
        sys.exit(1)
    print("Done. All patched files verified. Re-scan and diff to confirm a full match.")
    if inst.is_mo2:
        print("Fixes live in MO2's 'Overwrite' entry - keep it, and always launch through MO2.")


def _find_case_insensitive(root: Path, key: str):
    """Locate files under root whose path matches key ignoring case (Linux filesystems are case-sensitive)."""
    cur = [(root, "")]
    parts = key.split("/")
    for i, part in enumerate(parts):
        nxt = []
        for folder, rel in cur:
            if not folder.is_dir():
                continue
            for ent in os.scandir(folder):
                if ent.name.lower() == part:
                    last = i == len(parts) - 1
                    if last and ent.is_file():
                        nxt.append((Path(ent.path), rel + ent.name))
                    elif not last and ent.is_dir():
                        nxt.append((Path(ent.path), rel + ent.name + "/"))
        cur = nxt
    return [(rel, p) for p, rel in cur]


# --------------------------------------------------------------------------- CLI

def add_install_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--gamma", type=Path, help="G.A.M.M.A. / Mod Organizer 2 folder (contains ModOrganizer.exe)")
    p.add_argument("--anomaly", type=Path, help="Anomaly folder (auto-detected from ModOrganizer.ini if --gamma given)")
    p.add_argument("--profile", help="MO2 profile name (default: the selected one, usually G.A.M.M.A)")


def find_mo2() -> Path | None:
    """Look for ModOrganizer.ini next to this script, in the current folder, or their parents."""
    starts = [Path(__file__).resolve().parent, Path.cwd()]
    for start in starts:
        for d in [start, *start.parents]:
            if (d / "ModOrganizer.ini").is_file():
                return d
    return None


def make_install(a) -> Install:
    if not a.gamma and not a.anomaly:
        a.gamma = find_mo2()
        if not a.gamma:
            die("could not find your G.A.M.M.A. folder. Put stalker_sync.py in the folder that contains "
                "ModOrganizer.exe, or pass --gamma <that folder> (or --anomaly for a non-MO2 install)")
        print(f"Using Mod Organizer folder: {a.gamma}", file=sys.stderr)
    return Install(a.gamma, a.anomaly, a.profile)


def ask_path(prompt: str, must_exist: bool = True) -> str:
    while True:
        p = input(prompt).strip().strip('"').strip("'").strip()
        if p and (not must_exist or Path(p).is_file()):
            return p
        print("  File not found - tip: drag the file onto this window, then press Enter.")


def interactive() -> None:
    """Menu for people who double-click the script instead of using a terminal."""
    print(f"stalker-sync {TOOL_VERSION} - keep G.A.M.M.A. / Anomaly installs identical\n")
    print("  1) Quick check: show my xrRazom fingerprints (launch the game once first)")
    print("  2) Scan my install  -> makes <name>.json to send to the reference player")
    print("  3) Compare two scans")
    print("  4) [reference player] Build a patch for a friend from their .json")
    print("  5) [friend] Apply a patch zip I received")
    choice = input("\nPick 1-5: ").strip()
    if choice == "1":
        argv = ["fingerprint"]
    elif choice == "2":
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", input("Your name (no spaces): ").strip() or "player")
        argv = ["scan", "--label", name, "-o", f"{name}.json"]
    elif choice == "3":
        argv = ["diff", ask_path("Reference .json: "), ask_path("Other .json: ")]
    elif choice == "4":
        argv = ["pack", ask_path("YOUR .json (from option 2): "), ask_path("Friend's .json: ")]
    elif choice == "5":
        z = ask_path("Patch .zip: ")
        main(["apply", "--dry-run", z])
        if input("\nApply these changes for real? Type yes: ").strip().lower() != "yes":
            print("Cancelled - nothing changed.")
            return
        argv = ["apply", z]
    else:
        print("Nothing selected.")
        return
    main(argv)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="stalker-sync", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"stalker-sync {TOOL_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fingerprint", help="show xrRazom fingerprints from the latest game log")
    add_install_args(p)

    p = sub.add_parser("scan", help="hash your install into a manifest")
    add_install_args(p)
    p.add_argument("-o", "--out", default="manifest.json")
    p.add_argument("--label", default=os.environ.get("USERNAME") or os.environ.get("USER") or "player",
                   help="your name, shown in reports")
    p.add_argument("--full", action="store_true", help="also hash textures/sounds/models and base level archives (slow)")
    p.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                   help="extra path glob to skip, e.g. 'gamedata/configs/axr_options.ltx' (repeatable)")
    p.add_argument("--cache", default=".stalker-sync-cache.json", help="hash cache file ('' to disable)")

    p = sub.add_parser("diff", help="compare reference manifest with yours")
    p.add_argument("reference")
    p.add_argument("mine")
    p.add_argument("--limit", type=int, default=25, help="max files listed per mod (0 = all)")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("pack", help="(reference player) build a patch zip for a friend")
    add_install_args(p)
    p.add_argument("reference", help="YOUR manifest (made by scan, unchanged since)")
    p.add_argument("friend", help="the friend's manifest")
    p.add_argument("-o", "--out", help="output zip (default: stalker-sync-patch-<friend>.zip)")

    p = sub.add_parser("apply", help="(friend) apply a patch zip")
    add_install_args(p)
    p.add_argument("patch")
    p.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    p.add_argument("--backup-dir", type=Path, help="where replaced/removed files go (default: <gamma>/stalker-sync-backup)")

    a = ap.parse_args(argv)

    if a.cmd == "fingerprint":
        inst = make_install(a)
        fp = latest_fingerprints(inst.logs_dir())
        if not fp:
            print(f"No xrRazom fingerprints found in {inst.logs_dir()} - start the game once with xrRazom.")
            return
        print(f"From {fp['log']} ({fp['log_time']}):")
        for kind in ("config", "spawn"):
            if kind in fp:
                print(f"  {kind:6} fingerprint: {fp[kind]['value']}   {fp[kind]['detail']}")
        print("Compare these with your friends. Equal numbers = xrRazom will accept the join.")

    elif a.cmd == "scan":
        inst = make_install(a)
        print(f"Scanning {'MO2 profile ' + repr(inst.profile) if inst.is_mo2 else 'flat install'} "
              f"({'full' if a.full else 'gameplay'} scope)...", file=sys.stderr)
        m = scan(inst, a.full, a.ignore, HashCache(Path(a.cache) if a.cache else None), a.label)
        Path(a.out).write_text(json.dumps(m, indent=1), "utf-8")
        size = sum(f["size"] for f in m["files"].values())
        print(f"Wrote {a.out}: {len(m['files'])} files ({human(size)}), {len(m['mods'])} enabled mods.")
        if m["fingerprints"].get("config"):
            print(f"xrRazom config fingerprint from last game run: {m['fingerprints']['config']['value']}")

    elif a.cmd == "diff":
        host, mine = load_manifest(a.reference), load_manifest(a.mine)
        d = compute_diff(host, mine)
        if a.json:
            print(json.dumps(d, indent=1))
        else:
            print_report(host, mine, d, a.limit)
        sys.exit(0 if not (d["missing"] or d["extra"] or d["changed"]) else 1)

    elif a.cmd == "pack":
        inst = make_install(a)
        host, mine = load_manifest(a.reference), load_manifest(a.friend)
        out = Path(a.out or f"stalker-sync-patch-{re.sub(r'[^A-Za-z0-9_.-]', '_', mine.get('label', 'friend'))}.zip")
        info = build_patch(inst, host, mine, out)
        print(f"Wrote {out} ({human(out.stat().st_size)}): {len(info['put'])} file(s) to write, "
              f"{len(info['remove'])} to remove.")
        if info["mods_only_host"] or info["mods_only_mine"]:
            print("Note: your mod lists differ too - see `diff` output.")

    elif a.cmd == "apply":
        inst = make_install(a)
        backup = a.backup_dir or ((inst.gamma or inst.anomaly) / "stalker-sync-backup")
        apply_patch(inst, Path(a.patch), a.dry_run, backup)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        # Double-clicked: show a menu and keep the window open at the end.
        try:
            os.chdir(Path(__file__).resolve().parent)
            interactive()
        except SystemExit:
            pass
        except Exception as e:  # show the error instead of a vanishing window
            print(f"\nUnexpected error: {e!r}")
        input("\nPress Enter to close...")
