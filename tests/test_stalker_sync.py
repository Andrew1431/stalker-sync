"""End-to-end test on two fake MO2 installs. Run: python -m unittest discover tests"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stalker_sync as ss  # noqa: E402


def make_install(root: Path, files: dict, modlist: list):
    gamma, anomaly = root / "gamma", root / "anomaly"
    (anomaly / "appdata" / "logs").mkdir(parents=True)
    prof = gamma / "profiles" / "G.A.M.M.A"
    prof.mkdir(parents=True)
    (gamma / "overwrite").mkdir()
    (gamma / "ModOrganizer.ini").write_text(
        f"[General]\ngamePath=@ByteArray({anomaly})\nselected_profile=@ByteArray(G.A.M.M.A)\n")
    (prof / "modlist.txt").write_text("# MO2\n" + "\n".join(modlist) + "\n")
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return ss.Install(gamma, None, None)


BASE = {
    "anomaly/gamedata/configs/system.ltx": b"base",
    "anomaly/db/levels/level_x.db0": b"huge level, skipped by default",
    "anomaly/bin/AnomalyDX11.exe": b"exe",
    "gamma/mods/A/gamedata/configs/items.ltx": b"A items",
    "gamma/mods/A/gamedata/scripts/a.script": b"A script v1",
    "gamma/mods/A/readme.txt": b"not game data",
    "gamma/mods/B/gamedata/configs/items.ltx": b"B items wins",
    "gamma/mods/B/gamedata/textures/x.dds": b"texture, skipped by default",
}
MODLIST = ["+B", "+A", "+Sep_separator", "-Disabled"]


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self, inst, label):
        return ss.scan(inst, False, [], ss.HashCache(None), label)

    def test_priority_and_scope(self):
        inst = make_install(self.tmp / "h", BASE, MODLIST)
        m = self.scan(inst, "host")
        f = m["files"]
        self.assertEqual(f["gamedata/configs/items.ltx"]["owner"], "B")
        self.assertEqual(f["gamedata/configs/items.ltx"]["providers"], ["B", "A"])
        self.assertNotIn("gamedata/textures/x.dds", f)
        self.assertNotIn("db/levels/level_x.db0", f)
        self.assertNotIn("readme.txt", f)
        self.assertEqual(m["mods"], ["B", "A"])

    def test_overwrite_wins(self):
        files = dict(BASE, **{"gamma/overwrite/gamedata/configs/items.ltx": b"ow"})
        m = self.scan(make_install(self.tmp / "h", files, MODLIST), "host")
        self.assertEqual(m["files"]["gamedata/configs/items.ltx"]["owner"], "@overwrite")

    def test_full_sync_cycle(self):
        host = make_install(self.tmp / "h", BASE, MODLIST)
        friend_files = dict(BASE)
        friend_files["gamma/mods/A/gamedata/scripts/a.script"] = b"A script v0 (old)"
        del friend_files["gamma/mods/B/gamedata/configs/items.ltx"]          # missing -> A's copy wins
        friend_files["gamma/mods/A/gamedata/configs/mod_system_stale.ltx"] = b"stale"  # DINEY case
        friend_files["anomaly/gamedata/configs/MOD_SYSTEM_STALE.LTX"] = b"stale2"     # 2nd copy, other case
        friend = make_install(self.tmp / "f", friend_files, MODLIST)

        hm, fm = self.scan(host, "host"), self.scan(friend, "friend")
        d = ss.compute_diff(hm, fm)
        self.assertEqual(d["changed"], ["gamedata/configs/items.ltx", "gamedata/scripts/a.script"])
        self.assertEqual(d["extra"], ["gamedata/configs/mod_system_stale.ltx"])

        hp, fp = self.tmp / "host.json", self.tmp / "friend.json"
        hp.write_text(json.dumps(hm)); fp.write_text(json.dumps(fm))
        zp = self.tmp / "patch.zip"
        ss.build_patch(host, ss.load_manifest(str(hp)), ss.load_manifest(str(fp)), zp)

        ss.apply_patch(friend, zp, dry_run=True, backup_root=self.tmp / "bk")
        self.assertNotEqual(ss.compute_diff(hm, self.scan(friend, "friend")), ss.compute_diff(hm, hm))

        ss.apply_patch(friend, zp, dry_run=False, backup_root=self.tmp / "bk")
        d2 = ss.compute_diff(hm, self.scan(friend, "friend"))
        self.assertEqual((d2["missing"], d2["changed"], d2["extra"]), ([], [], []))
        backed = sorted(p.name for p in (self.tmp / "bk").rglob("*") if p.is_file())
        self.assertEqual(backed, ["MOD_SYSTEM_STALE.LTX", "mod_system_stale.ltx"])

    def test_rejects_unsafe_paths(self):
        for bad in ("../evil.ltx", "/etc/x", "C:/x", "readme.txt"):
            with self.assertRaises(SystemExit):
                ss.safe_rel(bad)

    def test_fingerprint_parse(self):
        inst = make_install(self.tmp / "h", BASE, MODLIST)
        (inst.logs_dir() / "xray_me.log").write_text(
            "x\n[xrRazom] config fingerprint: C8778075 (1206 files in manifest)\n"
            "[xrRazom] spawn fingerprint: a56342f5 (version 10)\n")
        fp = ss.latest_fingerprints(inst.logs_dir())
        self.assertEqual(fp["config"]["value"], "c8778075")
        self.assertEqual(fp["spawn"]["value"], "a56342f5")


if __name__ == "__main__":
    unittest.main()
