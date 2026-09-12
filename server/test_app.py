import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_TEMP = tempfile.TemporaryDirectory()
os.environ["AUTO_REFRESH"] = "0"
os.environ["DATABASE_PATH"] = str(Path(MODULE_TEMP.name) / "module.sqlite3")
os.environ["SPRITE_DIR"] = str(Path(MODULE_TEMP.name) / "sprites")

import app as app_module
import db


def fixture_store():
    sprite = "https://championsbattledata.com/pokemon_champions_assets/pokemon/Garchomp.png"
    top = {
        "move": {"pokemon":"Garchomp", "category":"move", "name":"Earthquake", "percentage":"92.0%", "percentage_value":92},
        "held_item": {"pokemon":"Garchomp", "category":"held_item", "name":"Life Orb", "percentage":"40.0%"},
        "ability": {"pokemon":"Garchomp", "category":"ability", "name":"Rough Skin", "percentage":"100.0%"},
        "stat_alignment": {"pokemon":"Garchomp", "category":"stat_alignment", "name":"Jolly", "percentage":"60.0%"},
        "stat_points": {"pokemon":"Garchomp", "category":"stat_points", "percentage":"50.0%", "hp_points":2, "attack_points":32, "defense_points":0, "sp_atk_points":0, "sp_def_points":0, "speed_points":32},
    }
    battle = {
        "position": 1, "top": top,
        "values": {
            "move": ["Earthquake", "Protect", "Dragon Claw", "Rock Slide", "Iron Head"],
            "held_item": ["Life Orb", "Choice Band", "Yache Berry", "Focus Sash"],
            "ability": ["Rough Skin", "Sand Veil", "Unused"],
            "teammate": ["Raichu"],
        },
    }
    return {
        "meta": {"season":"TEST", "generatedAt":"2026-09-12T00:00:00Z", "dataVersion":"test-v1", "source":"fixture"},
        "catalog": [{"slug":"garchomp","name":"Garchomp","displayName":"烈咬陆鲨","sprite":sprite,"types":["龙","地面"],"stats":{"hp":108,"atk":130,"def":95,"spa":80,"spd":85,"spe":102},"battles":{"Doubles":battle,"Singles":None}}],
        "calculator": [{"slug":"garchomp","name":"Garchomp","baseName":"Garchomp","displayName":"烈咬陆鲨","baseDisplayName":"烈咬陆鲨","searchText":"garchomp 烈咬陆鲨","sprite":sprite,"types":["龙","地面"],"stats":{"hp":108,"atk":130,"def":95,"spa":80,"spd":85,"spe":102},"learnableMoves":["Earthquake"]}],
        "items": ["No Item", "Life Orb"],
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        db.init_database()
        self.store = fixture_store()

    def tearDown(self):
        self.temp_dir.cleanup()

    def save_fixture(self):
        db.save_snapshot(self.store, {"fixture": True}, {"garchomp": "烈咬陆鲨"})

    def test_static_seed_and_order(self):
        payload = db.load_bootstrap()
        self.assertEqual(18, len(payload["static"]["types"]))
        self.assertEqual(["hp", "atk", "def", "spa", "spd", "spe"], payload["static"]["statKeys"])
        self.assertNotIn("catalog", payload)
        self.assertIsNone(payload["meta"])

    def test_split_snapshot_contracts(self):
        self.save_fixture()
        bootstrap = db.load_bootstrap()
        usage = db.load_usage("Doubles")
        calculator = db.load_calculator()
        self.assertEqual("TEST", bootstrap["meta"]["season"])
        self.assertEqual(1, bootstrap["meta"]["rosterCount"])
        self.assertEqual(1, len(usage["catalog"]))
        self.assertEqual(["Earthquake", "Protect", "Dragon Claw", "Rock Slide"], usage["catalog"][0]["battles"]["Doubles"]["values"]["move"])
        self.assertNotIn("pokemon", usage["catalog"][0]["battles"]["Doubles"]["top"]["move"])
        self.assertNotIn("stats", usage["catalog"][0])
        self.assertTrue(usage["catalog"][0]["sprite"].startswith("/api/sprites/"))
        self.assertEqual(["Earthquake"], calculator["calculator"][0]["learnableMoves"])
        self.assertNotIn("aliases", calculator)

    def test_usage_rejects_unknown_format(self):
        with self.assertRaises(ValueError):
            db.load_usage("Triples")

    def test_http_compression_etag_and_refresh_contract(self):
        self.save_fixture()
        client = app_module.app.test_client()
        first = client.get("/api/bootstrap", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(200, first.status_code)
        self.assertEqual("gzip", first.headers.get("Content-Encoding"))
        self.assertIn("no-cache", first.headers["Cache-Control"])
        etag = first.headers["ETag"]
        second = client.get("/api/bootstrap", headers={"If-None-Match": etag, "Accept-Encoding": "gzip"})
        self.assertEqual(304, second.status_code)
        usage = client.get("/api/usage?format=Doubles")
        self.assertEqual(1, len(usage.get_json()["catalog"]))
        self.assertEqual(400, client.get("/api/usage?format=Triples").status_code)
        calculator = client.get("/api/calculator")
        self.assertEqual(1, len(calculator.get_json()["calculator"]))
        with patch.object(app_module, "refresh", return_value=self.store["meta"]), patch.object(app_module, "schedule_warmup"):
            refreshed = client.post("/api/refresh")
        expected_meta = {**self.store["meta"], "rosterCount": 1}
        self.assertEqual({"meta": expected_meta}, refreshed.get_json())
        self.assertEqual("no-store", refreshed.headers["Cache-Control"])

    def test_unknown_sprite_returns_local_placeholder(self):
        client = app_module.app.test_client()
        response = client.get(f"/api/sprites/{'0' * 24}")
        self.assertEqual(200, response.status_code)
        self.assertEqual("image/svg+xml", response.mimetype)
        self.assertEqual("no-store", response.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
