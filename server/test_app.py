import os
import tempfile
import unittest
from pathlib import Path

os.environ["AUTO_REFRESH"] = "0"

import db


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        db.init_database()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_static_seed_and_order(self):
        payload = db.load_bootstrap()
        self.assertEqual(18, len(payload["static"]["types"]))
        self.assertEqual(["hp", "atk", "def", "spa", "spd", "spe"], payload["static"]["statKeys"])
        self.assertEqual(2, payload["static"]["typeChart"]["火"]["草"])
        self.assertEqual("生命宝珠", payload["static"]["itemZh"]["Life Orb"])

    def test_snapshot_round_trip(self):
        store = {
            "meta": {"season":"TEST", "generatedAt":"2026-09-12T00:00:00Z", "dataVersion":"test", "source":"fixture"},
            "catalog": [{"slug":"garchomp","name":"Garchomp","displayName":"烈咬陆鲨","sprite":"sprite.png","types":["龙","地面"],"stats":{"hp":108,"atk":130,"def":95,"spa":80,"spd":85,"spe":102},"battles":{"Doubles":{"position":1,"top":{},"values":{}},"Singles":None}}],
            "calculator": [{"slug":"garchomp","name":"Garchomp","baseName":"Garchomp","displayName":"烈咬陆鲨","baseDisplayName":"烈咬陆鲨","searchText":"garchomp 烈咬陆鲨","sprite":"sprite.png","types":["龙","地面"],"stats":{"hp":108,"atk":130,"def":95,"spa":80,"spd":85,"spe":102},"learnableMoves":["Earthquake"]}],
            "items": ["No Item"],
        }
        db.save_snapshot(store, {"fixture": True}, {"garchomp": "烈咬陆鲨"})
        payload = db.load_bootstrap()
        self.assertEqual("TEST", payload["meta"]["season"])
        self.assertEqual("烈咬陆鲨", payload["catalog"][0]["displayName"])
        self.assertEqual(["Earthquake"], payload["calculator"][0]["learnableMoves"])
        self.assertEqual("烈咬陆鲨", payload["aliases"]["garchomp"])


if __name__ == "__main__":
    unittest.main()
