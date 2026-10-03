# stalker-sync

**Make S.T.A.L.K.E.R. Anomaly / G.A.M.M.A. installs identical across friends, file for file, so co-op mods like xrRazom stop failing with "mod config mismatch".**

One player is the *reference*. Everyone else scans their install and sends the reference a small file. The reference sends back a patch zip containing only the files that differ. Applying it fixes changed files, adds missing ones and removes stale leftovers. Everything it replaces or removes is backed up first.

- One Python file with no dependencies (Python 3.8+). Works on Windows and on Linux (MO2 under Wine, Proton, Lutris or Bottles).
- Double-click menu for beginners, plus a full command line.
- Never touches your load order or `modlist.txt`. Never deletes anything; removed files go to a backup folder.
- Works with any Mod Organizer 2 setup for Anomaly, not just G.A.M.M.A., and with plain installs that don't use MO2.

> **New to this?** Read [DISCORD_GUIDE.md](DISCORD_GUIDE.md). It's a step-by-step version you can paste into your Discord.

---

## Why installs drift apart

Even when everyone runs the same modpack installer, the results differ:

1. **Mods download live from ModDB and GitHub.** When an author re-uploads a mod, people who installed on different days get different versions.
2. **Stale leftover files.** Updating a mod by unzipping over the old one leaves behind files the new version removed. Anomaly loads *every* `mod_system_*.ltx` and `mod_*.xml` it finds. One leftover file changes your game even though your mod list looks identical, which is why comparing mod lists or MO2 CSV exports doesn't solve this.
3. **Different install guides.** Linux guides often use different installers, and Linux filesystems are case-sensitive, so `Items.ltx` and `items.ltx` can both exist.
4. **Manual changes.** A mod ticked or unticked in MO2, a hand-edited `.ltx`, files sitting in MO2's *Overwrite*, or a mod unzipped straight into the Anomaly folder.

What matters is not *which mods you have* but *which file the game actually loads for every path*. MO2 stacks things in this order:

1. Anomaly folder, at the bottom.
2. Enabled mods, by priority. A mod lower in MO2's left pane wins.
3. Overwrite, on top.

stalker-sync rebuilds exactly that stack and compares the result.

### What xrRazom checks when you join

| Check | Rejection message in the log | Fix |
|---|---|---|
| protocol | `protocol version mismatch` | Everyone installs the same xrRazom release |
| config | `config mismatch (peer=… host=…)` | stalker-sync |
| world | `world (all.spawn) mismatch` | stalker-sync with `--full`, or the same Anomaly and level mods |
| task-ltx | `task-ltx mismatch` | stalker-sync |

Each game start writes lines like `[xrRazom] config fingerprint: c8778075` to `Anomaly/appdata/logs/xray_<name>.log`. When everyone's numbers match, xrRazom accepts the join. Scripts (`.script`) aren't part of the fingerprint, but they change how the game behaves, so stalker-sync compares them too.

---

## Quick start (command line)

Put `stalker_sync.py` in your G.A.M.M.A. folder, the one with `ModOrganizer.exe`. It finds everything else from there. Open a terminal in that folder.

**0. Check whether you're already in sync.** Each player launches the game to the main menu once, then runs:

```bash
python stalker_sync.py fingerprint
```

If every player's `config` and `spawn` numbers match, you're done.

**1. Everyone scans their install.**

```bash
python stalker_sync.py scan --label alice -o alice.json
```

The first scan takes about a minute. Later scans take a few seconds because of a cache. Everyone sends their `.json` to the reference player. It's small and contains only file paths and checksums.

**2. Compare two scans (optional).**

```bash
python stalker_sync.py diff alice.json bob.json
```

Pass the reference first. Example output:

```
xrRazom config fingerprint: c8778075 vs 1f00ab3e  -> DIFFERENT

Mod list differences (fix these in Mod Organizer first, then re-scan):
  - enable / install:  335- Auto-Stacking Items - Zatura

File differences: 0 missing, 1 different, 1 extra  (total 2), grouped by mod:

  [G.A.M.M.A. Artefacts Reinvention]  1 file(s)
     extra     gamedata/configs/mod_system_grok_artifacts.ltx   (not in reference - stale file?)

  [277- Tommy Gun - Firebreath]  1 file(s)
     different gamedata/configs/items/weapons/w_tommy.ltx
```

**3. The reference player builds a patch for each friend.**

```bash
python stalker_sync.py pack alice.json bob.json
```

This writes `stalker-sync-patch-bob.zip`. Don't change your install between `scan` and `pack`; the tool refuses if files changed.

**4. The friend previews the patch, then applies it.**

```bash
python stalker_sync.py apply --dry-run stalker-sync-patch-bob.zip
```

```bash
python stalker_sync.py apply stalker-sync-patch-bob.zip
```

Applying does three things:
- New and changed files go into MO2's **Overwrite** folder. Overwrite always wins, so load order doesn't matter. Keep that folder, and always start the game through MO2.
- Extra files are moved out of *every* mod that has them, into `stalker-sync-backup/<date>/`.
- Every patched file is re-checked at the end.

Finally, the friend scans again, or everyone compares `fingerprint` numbers. The installs should now match.

---

## Commands

| Command | Who | What |
|---|---|---|
| *(no arguments)* | anyone | Interactive menu. This is what runs when you double-click the file |
| `fingerprint` | anyone | Show xrRazom config and spawn fingerprints from your newest game log |
| `scan` | everyone | Hash the files the game loads and write a manifest `.json` |
| `diff REF MINE` | anyone | Report differences. Exit code 0 means identical, 1 means different |
| `pack REF FRIEND` | reference | Build a patch zip from your install |
| `apply PATCH` | friend | Apply a patch zip. Add `--dry-run` to preview |

Options:

- `--gamma DIR`: your MO2 / G.A.M.M.A. folder. Optional if the script sits inside it. The Anomaly path and active profile are read from `ModOrganizer.ini`, and Wine `Z:\` paths are translated on Linux.
- `--anomaly DIR`: override the Anomaly folder. On its own, it handles installs without MO2; patches are then written straight into the game folder, with backups.
- `--profile NAME`: an MO2 profile other than the selected one.
- `scan --full`: also hash textures, sounds, models and the base level archives. Much slower; use it when the configs match but the world or visuals still differ.
- `scan --ignore GLOB`: skip paths you keep different on purpose. All players must use the same patterns.
- `diff --limit 0`: list every file. `diff --json` gives machine-readable output.

### What gets scanned

The scan covers files under `gamedata/`, `bin/`, `db/` and `MT/` with these extensions: `.ltx .xml .script .lua .ini .spawn .db* .xdb* .exe .dll`. It reads them from three places:
- the Anomaly folder (`gamedata`, `bin`, `MT`, and `db/mods`, `db/configs`, `db/patches` and `db/addons`, but not the multi-GB level archives)
- every enabled mod in the active MO2 profile
- MO2's Overwrite folder

Logs, crash dumps, shader caches and ReShade presets are always skipped. Paths are compared ignoring upper/lower case, as the game does.

---

## Tips and gotchas

- **Everyone must install co-op mods and engine patches the same way.** A mod unzipped into the Anomaly folder sits at the *lowest* priority, under every MO2 mod. The same mod added in MO2 gets whatever priority you give it. Mixing the two methods between players gives different files. In `diff` output this shows up as one side owned by `@anomaly` and the other by the mod's name.
- **MCM settings** are saved to `gamedata/configs/axr_options.ltx`, usually in Overwrite. They're personal settings, and xrRazom already syncs the relevant ones from the host. If this file shows up as a difference, everyone can scan with `--ignore "gamedata/configs/axr_options.ltx"`.
- **Undoing a patch:** move files from `stalker-sync-backup/<date>/<mod name>/…` back to `mods/<mod name>/…`, and delete the patched files from `overwrite/`. The `files/` folder inside the zip lists every file the patch wrote.
- **After a modpack update, sync again.** Updates can bring back stale files or replace patched ones.
- **Large patches:** if the reference's install differs a lot, for example when another player is on an older modpack version, the zip can get big. Updating the modpack first is usually faster.

## FAQ

**Is it safe?** It only reads files, except when you run `apply`. `apply` only writes inside the MO2 Overwrite folder and only moves files into a backup folder. The patch format refuses paths outside the game folders.

**Does it upload anything?** No. You share the files yourself.

**Does it work with mods besides xrRazom?** Yes. It works with any multiplayer mod or shared setup, or when you just want a friend's exact install.

## Development

```bash
python -m unittest discover tests
```

The tests build two fake MO2 installs and check priority resolution, scan, diff, pack, apply, case-insensitive stale-file removal, and safety against unsafe paths in patches.

## License

MIT
