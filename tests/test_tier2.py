"""Tier 2 (data accuracy) checks. Standard library only, no network:

    python3 -m unittest tests/test_tier2.py
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import build_game_index as gi  # noqa: E402
import fetch  # noqa: E402
import identify  # noqa: E402
import seed_identity  # noqa: E402
import slots  # noqa: E402


def entry(title, regions, ids, aka=()):
    return {"title": title, "regions": set(regions), "ids": set(ids), "sha1": set(), **({"aka": set(aka)} if aka else {})}


class Grouping(unittest.TestCase):
    def group(self, platform, entries, previous=()):
        index = {gi.game_key(e["title"]): e for e in entries}
        return gi.group_regions(platform, index, set(previous))

    def test_regional_releases_merge(self):
        out = self.group("gc", [
            entry("Animal Crossing", ["USA"], ["GAFE", "GAFE01"]),
            entry("Doubutsu no Mori +", ["Japan"], ["GAFJ", "GAFJ01"]),
            entry("Animal Crossing", ["Europe"], ["GAFP"]) | {"title": "Animal Crossing (PAL)"},
        ])
        self.assertIn("animal-crossing", out)
        self.assertEqual(len(out), 1)
        self.assertIn("Doubutsu no Mori +", out["animal-crossing"]["aka"])

    def test_reused_codes_stay_apart(self):
        # Wii RHG*: two unrelated games sharing a product code's game part.
        out = self.group("wii", [
            entry("Agent Hugo - Lemoon Twist", ["Europe"], ["RHGP", "RHGP6Z"]),
            entry("Guitar Hero III Custom", ["USA"], ["RHGE", "RHGE52"]),
        ])
        self.assertEqual(len(out), 2)
        # GBA AQD*: Spanish title vs an unrelated US/EU game.
        out = self.group("gba", [
            entry("Crouching Tiger, Hidden Dragon", ["USA", "Europe"], ["AQDE", "AQDP"]),
            entry("Disney Princesas", ["Spain"], ["AQDS"]),
        ])
        self.assertEqual(len(out), 2)
        # No region on one side (Wii RNV*): not a regional variant.
        out = self.group("wii", [
            entry("Anubis II", ["Europe"], ["RNVP", "RNVPUG"]),
            entry("Super Mario Galaxy: Nvidia Shield TV", [], ["RNVW", "RNVW01"]),
        ])
        self.assertEqual(len(out), 2)

    def test_never_two_releases_for_one_region(self):
        out = self.group("nds", [
            entry("Pokemon Ranger", ["USA"], ["YP3E"]),
            entry("Pokemon Dash", ["USA"], ["YP3F"]),  # shares a word, same region: not one game
        ])
        self.assertEqual(len(out), 2)

    def test_previous_key_kept(self):
        es = [entry("Animal Crossing", ["USA"], ["GAFE"]), entry("Doubutsu no Mori +", ["Japan"], ["GAFJ"])]
        self.assertIn("doubutsu-no-mori", self.group("gc", es, previous={"doubutsu-no-mori"}))
        self.assertIn("animal-crossing", self.group("gc", es))

    def test_unlicensed_dropped(self):
        self.assertFalse(gi.licensed({"name": "AceKard 2 (World)", "roms": ["AceKard 2 (World) (Unl).nds"], "region": ""}, "metadat/x"))
        self.assertFalse(gi.licensed({"name": "Guitar Hero III Custom: Slipknot", "roms": [], "region": "USA"}, "dat/Nintendo - Wii.dat"))
        self.assertFalse(gi.licensed({"name": "Homebrew Browser", "roms": [], "region": ""}, "dat/Nintendo - Wii.dat"))
        self.assertTrue(gi.licensed({"name": "Custom Robo (USA)", "roms": [], "region": "USA"}, "metadat/redump/Nintendo - GameCube.dat"))
        self.assertTrue(gi.licensed({"name": "Mario Kart Wii (USA)", "roms": [], "region": "USA"}, "dat/Nintendo - Wii.dat"))

    def test_bare_code_needs_agreeing_title(self):
        index = {}
        gi.add(index, "wii", "A Very Merry Wii Music Christmas", "USA", {"WMXE", "WMXE01"}, set())
        gi.add(index, "wii", "Max & the Magic Marker", "USA", {"WMXE"}, set())
        index.pop("_id_owner")
        self.assertEqual(len(index), 2)
        index = {}
        gi.add(index, "gc", "Doubutsu no Mori +", "Japan", {"GAFJ", "DLDOLGAFJJPN"}, set())
        gi.add(index, "gc", "Doubutsu no Mori +", "Japan", {"GAFJ", "GAFJ01"}, set(), alias=False)
        index.pop("_id_owner")
        self.assertEqual(list(index), ["doubutsu-no-mori"])
        self.assertIn("GAFJ01", index["doubutsu-no-mori"]["ids"])


GAMES = {
    "gc": [
        {"key": "army-men-sarge-s-war", "title": "Army Men - Sarge's War", "regions": ["USA"], "ids": ["GAME"], "sha1": []},
        {"key": "super-smash-bros-melee", "title": "Super Smash Bros. Melee", "regions": ["USA"], "ids": ["GALE", "GALE01"],
         "sha1": ["a" * 40]},
        {"key": "pikmin", "title": "Pikmin", "regions": ["USA"], "ids": ["GPIE"], "sha1": ["b" * 40]},
    ],
    "ps2": [{"key": "some-ps2-game", "title": "Some PS2 Game", "regions": ["USA"], "ids": ["SLUS20486"], "sha1": []}],
    "arcade": [{"key": "frogger", "title": "Frogger", "regions": [], "ids": ["FROGGER"], "sha1": []}],
    "naomi": [{"key": "mvsc2", "title": "Marvel Vs. Capcom 2", "regions": [], "ids": ["MVSC2"], "sha1": []}],
    "switch": [{"key": "botw", "title": "The Legend of Zelda: Breath of the Wild", "regions": [], "ids": ["01007EF00011E000"], "sha1": []}],
}


class Detection(unittest.TestCase):
    def setUp(self):
        self._index, self._platforms = identify.index, identify.platforms

        def index(plat):
            ids, hashes = {}, {}
            for g in GAMES.get(plat, []):
                for i in g["ids"]:
                    ids.setdefault(i, []).append(g)
                for h in g["sha1"]:
                    hashes.setdefault(h, []).append(g)
            return ids, hashes

        identify.index, identify.platforms = index, lambda: sorted(GAMES)

    def tearDown(self):
        identify.index, identify.platforms = self._index, self._platforms

    def found(self, versions=(), paths=(), contents=()):
        f = identify.empty()
        for v in versions:
            identify.merge(f, identify.from_token(v, is_version=True))
        for p in paths:
            identify.merge(f, identify.from_path(p))
        for path, text in contents:
            identify.merge(f, identify.from_content(text, path.endswith(".sha1"), path))
        return f

    def test_bare_directory_names_are_noise(self):
        f = self.found(paths=["files/GAME01/data.bin", "DATA01/x.bin", "SAVE01/y"])
        self.assertIsNone(identify.match(f))

    def test_config_shapes_count(self):
        self.assertEqual(identify.match(self.found(paths=["config/GALE01/symbols.txt"]))["key"], "super-smash-bros-melee")
        self.assertEqual(identify.match(self.found(paths=["GALE01.yaml"]))["key"], "super-smash-bros-melee")
        self.assertEqual(identify.match(self.found(paths=["orig/GALE01.sha1"]))["key"], "super-smash-bros-melee")
        self.assertEqual(identify.match(self.found(paths=["melee.SLUS_204.86.yaml"]))["platform"], "ps2")

    def test_weak_path_needs_corroboration(self):
        f = self.found(paths=["orig/GALE01/sys/main.dol"])
        self.assertIsNone(identify.match(f))
        f = self.found(paths=["orig/GALE01/sys/main.dol"], contents=[("config/build.sha1", "a" * 40)])
        self.assertEqual(identify.match(f)["key"], "super-smash-bros-melee")

    def test_vset_needs_arcade_evidence(self):
        self.assertIsNone(identify.match(self.found(versions=["frogger"])))
        self.assertEqual(identify.match(self.found(versions=["frogger"]), "arcade")["key"], "frogger")
        f = self.found(versions=["mvsc2"], contents=[("config.json", '{"set": "mvsc2"}')])
        self.assertEqual(identify.match(f)["platform"], "naomi")

    def test_multi_platform_most_support_wins(self):
        f = self.found(versions=["GALE01"], paths=["config/GALE01/x", "tools/../x"],
                       contents=[("config/GALE01/build.sha1", "a" * 40), ("configure.py", 'X = "SLUS_204.86"')])
        hit = identify.match(f)
        self.assertEqual((hit["platform"], hit["key"]), ("gc", "super-smash-bros-melee"))

    def test_sha1_for_another_game(self):
        # Two hashes, two games: the one named after the repo's own build wins.
        f = self.found(versions=["GALE01"], contents=[("config/GALE01/build.sha1", "a" * 40), ("other/pikmin.sha1", "b" * 40)])
        self.assertEqual(identify.match(f, version_ids=["GALE01"])["key"], "super-smash-bros-melee")
        # Neither file named after a build: only a hash another identifier agrees with counts.
        f = self.found(paths=["config/GPIE01/x"], contents=[("a.sha1", "a" * 40), ("b.sha1", "b" * 40)])
        self.assertEqual(identify.match(f, version_ids=["us"])["key"], "pikmin")

    def test_switch_title_ids(self):
        self.assertEqual(identify.match(self.found(versions=["01007EF00011E000"]))["key"], "botw")
        f = self.found(contents=[("config.toml", "title_id = 0x01007ef00011e800")])  # the update -> its base game
        self.assertEqual(identify.match(f)["platform"], "switch")
        self.assertIn("01007EF00011E000", identify.build_ids("01007EF00011E000"))


class Agrees(unittest.TestCase):
    def test_cases(self):
        a = seed_identity.agrees
        self.assertFalse(a({"title": "Mario Party 4", "by": "disc"}, "Mario"))
        self.assertFalse(a({"title": "Pikmin 2", "by": "disc"}, "Pikmin"))
        self.assertFalse(a({"title": "Super Mario Galaxy", "by": "disc"}, "Super Mario Galaxy 2"))
        self.assertTrue(a({"title": "Doubutsu no Mori e+", "by": "disc"}, "どうぶつの森 e+"))
        self.assertFalse(a({"title": "Doubutsu no Mori e+", "by": "code"}, "どうぶつの森 e+"))
        self.assertTrue(a({"title": "The Legend of Zelda - The Wind Waker", "by": "code"}, "The Legend of Zelda: The Wind Waker"))
        self.assertTrue(a({"title": "Nickelodeon SpongeBob SquarePants - Battle for Bikini Bottom", "by": "disc"},
                          "Battle for Bikini Bottom (Fork: AI)"))
        self.assertFalse(a({"title": "Dragon Ball Z - The Legacy of Goku II", "by": "code"}, "Dragonball Z: Buu's Fury"))
        self.assertTrue(a({"title": "Kingdom Hearts - 358-2 Days", "by": "code"}, "Kingdom Hearts 358/2 Days"))
        self.assertTrue(a({"title": "Dragon Quest IV - Chapters of the Chosen", "by": "code"}, "Dragon Quest IV"))
        self.assertFalse(a({"title": "Army Men - Sarge's War", "by": "disc"}, "Wii Menu"))


class Reports(unittest.TestCase):
    def test_version_ids(self):
        self.assertEqual(fetch.safe_version("GALE01"), "GALE01")
        self.assertEqual(fetch.safe_version("SLUS_204.86"), "SLUS_204.86")
        self.assertEqual(fetch.safe_version("history"), "history-build")
        self.assertEqual(fetch.safe_version("History"), "history-build")
        self.assertEqual(fetch.safe_version(".."), "default")
        self.assertEqual(fetch.safe_version("."), "default")
        self.assertEqual(fetch.safe_version("../etc"), "etc")
        self.assertEqual(fetch.safe_version("Ver 1.0 日本"), "Ver-1.0")
        self.assertRegex(fetch.safe_version("a/b cé"), r"^[A-Za-z0-9_.-]+$")

    def test_artifact_name_collisions(self):
        arts = [{"name": n, "id": i, "created_at": "2026-01-01", "size_in_bytes": 1,
                 "workflow_run": {"head_branch": "main"}} for i, n in enumerate(["us report", "us_report", "history_report"])]
        orig = fetch.gh
        fetch.gh = lambda path, raw=False: {"artifacts": arts}
        try:
            ids = [v for v, _ in fetch.artifact_versions("o/r", "main")]
        finally:
            fetch.gh = orig
        self.assertEqual(sorted(ids), ["history-build", "us", "us-2"])

    def test_linked_never_above_matched(self):
        r = fetch.from_objdiff({"measures": {"total_code": 1000, "matched_code": 300, "complete_code": 400}, "units": []})
        self.assertEqual(r["measures"]["matched_code"], 400)
        raw = {"id": "us", "commit": {"timestamp": "2026-01-01T00:00:00Z"},
               "measures": {"total_code": 1000, "matched_code": 300, "linked_code": 400}}
        import build
        v = build.Version(raw)
        self.assertGreaterEqual(v.code_pct, v.linked_pct)

    def test_legacy_complete_units(self):
        r = fetch.from_objdiff({"measures": {"total_code": 1000, "matched_code": 600}, "units": [
            {"name": "a", "complete": True, "measures": {"total_code": 300, "fuzzy_match_percent": 100}},
            {"name": "b", "metadata": {"complete": True}, "measures": {"total_code": 200}},
            {"name": "c", "measures": {"total_code": 500}},
        ]})
        self.assertEqual(r["measures"]["linked_code"], 500)
        self.assertEqual([u["is_linked"] for u in r["units"]], [True, True, False])

    def test_naomi_interior_and_linked(self):
        proof = {"mapping": {"reviewed_code_bytes": 300, "reviewed_data_bytes": 100}, "units": [
            {"id": "a", "credited": True, "sections": [
                {"kind": "code", "size": 120, "interior": [{"kind": "data", "size": 20}]}]},
            {"id": "b", "credited": False, "function_bytes": 50, "pool_bytes": 0, "sections": [
                {"kind": "code", "size": 110, "equal_bytes": 50, "interior": [{"kind": "data", "size": 10}]}]},
        ]}
        r = fetch.from_naomi_proof(proof)
        self.assertEqual([u["total_code"] for u in r["units"][:2]], [100, 100])
        self.assertEqual(r["measures"]["matched_code"], 150)
        self.assertEqual(r["measures"]["linked_code"], 100)
        self.assertEqual(r["measures"]["linked_data"], 20)

    def test_history_follows_one_build(self):
        with tempfile.TemporaryDirectory() as d:
            orig = fetch.HISTORY
            fetch.HISTORY = Path(d)
            try:
                def ver(vid, sha, ts, pct):
                    return {"id": vid, "commit": {"sha": sha, "timestamp": ts, "message": ""},
                            "measures": {"total_code": 100, "matched_code": pct, "linked_code": 0, "fuzzy_match_percent": pct,
                                         "total_data": 0, "matched_data": 0, "linked_data": 0}}
                a1 = ver("GALE01", "s1", "2026-01-01T00:00:00Z", 10)
                fetch.append_history("p", a1, [a1])
                # The other build is now further along and ranks first; the series stays on GALE01.
                j1, a2 = ver("GALJ01", "s2", "2026-01-02T00:00:00Z", 50), ver("GALE01", "s3", "2026-01-02T00:00:00Z", 11)
                fetch.append_history("p", j1, [j1, a2])
                hist = json.loads((Path(d) / "p.json").read_text())
                self.assertEqual([(h["version"], h["commit_sha"]) for h in hist], [("GALE01", "s1"), ("GALE01", "s3")])
            finally:
                fetch.HISTORY = orig


class Takeover(unittest.TestCase):
    def test_streak_survives_one_missed_run(self):
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        st = None
        for i in range(29):  # every 6 hours for a week, one run behind
            st = slots._run(st, i != 10, (t0 + timedelta(hours=6 * i)).isoformat())
            self.assertIsNotNone(st)
        self.assertTrue(slots._ready(st, t0 + timedelta(days=7)))
        st = slots._run(slots._run(st, False, t0.isoformat()), False, t0.isoformat())
        self.assertIsNone(st)  # two in a row end it

    def test_live_beats_archived_leader(self):
        def proj(pct, archived):
            return SimpleNamespace(v=SimpleNamespace(code_pct=pct, archived=archived))
        self.assertTrue(slots.beats(proj(40.0, False), proj(40.0, True)))
        self.assertFalse(slots.beats(proj(40.5, False), proj(40.0, False)))
        self.assertTrue(slots.beats(proj(41.0, False), proj(40.0, False)))


if __name__ == "__main__":
    unittest.main()
