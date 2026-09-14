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
import importer


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
        self.assertEqual("水波刀", payload["static"]["moveZh"]["Aqua Cutter"])
        self.assertEqual("雷丘进化石X", payload["static"]["itemZh"]["Raichunite X"])
        self.assertEqual("尾甲", payload["static"]["abilityZh"]["Armor Tail"])
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
        self.assertEqual(1, bootstrap["meta"]["translationCoverage"]["missingTotal"])
        self.assertEqual(1, bootstrap["meta"]["translationCoverage"]["ability"]["missing"])

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
        expected_meta = db.load_bootstrap()["meta"]
        self.assertEqual({"meta": expected_meta}, refreshed.get_json())
        self.assertEqual("no-store", refreshed.headers["Cache-Control"])

    def test_unknown_sprite_returns_local_placeholder(self):
        client = app_module.app.test_client()
        response = client.get(f"/api/sprites/{'0' * 24}")
        self.assertEqual(200, response.status_code)
        self.assertEqual("image/svg+xml", response.mimetype)
        self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_refresh_prunes_stale_sprite_mappings(self):
        self.save_fixture()
        with db.database() as connection:
            self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM sprite_assets").fetchone()[0])
        self.store["catalog"][0]["sprite"] = ""
        self.store["calculator"][0]["sprite"] = ""
        db.save_snapshot(self.store, {"fixture": True})
        with db.database() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM sprite_assets").fetchone()[0])

    def test_existing_snapshot_uses_new_pokemon_translation_without_reimport(self):
        self.store["catalog"][0].update(name="Aegislash", displayName="Aegislash")
        self.store["calculator"][0].update(name="Aegislash [Blade Forme]", baseName="Aegislash Shield Forme", displayName="Aegislash [Blade Forme]", baseDisplayName="Aegislash Shield Forme")
        db.save_snapshot(self.store, {"fixture": True})
        self.assertEqual("坚盾剑怪", db.load_usage("Doubles")["catalog"][0]["displayName"])
        calculator = db.load_calculator()["calculator"][0]
        self.assertEqual("坚盾剑怪（刀剑形态）", calculator["displayName"])
        self.assertEqual("坚盾剑怪（盾牌形态）", calculator["baseDisplayName"])

    def test_season_move_pool_keeps_all_attacks_and_excludes_status_moves(self):
        self.store["calculator"][0]["learnableMoves"] = ["Earthquake", "Iron Head", "Grass Knot", "Protect"]
        self.store["moves"] = [
            {"name": "Earthquake", "type": "地面", "category": "物理", "power": 100},
            {"name": "Iron Head", "type": "钢", "category": "物理", "power": 80},
            {"name": "Grass Knot", "type": "草", "category": "特殊", "power": None},
            {"name": "Protect", "type": "一般", "category": "变化", "power": None},
        ]
        self.save_fixture()
        bootstrap = db.load_bootstrap()
        moves = {move["name"]: move for move in bootstrap["static"]["moves"]}
        self.assertEqual({"Earthquake", "Iron Head", "Grass Knot"}, set(moves))
        self.assertIsNone(moves["Grass Knot"]["power"])
        self.assertEqual(
            {"complete": True, "learnable": 4, "metadata": 4, "missing": 0, "attacking": 3, "formulaReady": 2, "conditional": 1},
            bootstrap["meta"]["movePoolCoverage"],
        )

    def test_move_catalog_matches_champions_names_and_smart_apostrophes(self):
        calculator = [{"learnableMoves": ["Iron Head", "Kowtow Cleave", "King's Shield"]}]
        rows = [
            {"name": "Iron Head", "type": "Steel", "category": "Physical", "power": 80},
            {"name": "Kowtow Cleave", "type": "Dark", "category": "Physical", "power": 85},
            {"name": "King’s Shield", "type": "Steel", "category": "Status", "power": None},
        ]
        catalog = importer.build_move_catalog(calculator, rows, {"typeZh": {"Steel": "钢", "Dark": "恶"}})
        self.assertEqual(["Iron Head", "King's Shield", "Kowtow Cleave"], [move["name"] for move in catalog])
        self.assertEqual(80, next(move["power"] for move in catalog if move["name"] == "Iron Head"))
        self.assertEqual(85, next(move["power"] for move in catalog if move["name"] == "Kowtow Cleave"))

    def test_unknown_item_placeholders_are_not_imported(self):
        self.assertTrue(importer.valid_item_name("Life Orb"))
        self.assertFalse(importer.valid_item_name("Unknown Item 152"))
        battle = importer.compact_battle({
            "position": 1,
            "top": {"held_item": {"name": "Unknown Item 152"}},
            "values": {"held_item": ["Unknown Item 152", "Life Orb"]},
        })
        self.assertIsNone(battle["top"]["held_item"])
        self.assertEqual(["Life Orb"], battle["values"]["held_item"])

    def test_pokecham_season_and_localized_battle_adapter(self):
        home = r'latestSeasonByFormat\":{\"single\":\"M-6\",\"double\":\"M-6\"}'
        self.assertEqual("M-6", importer._pokecham_season(home))
        html = """
        <span>MOVES</span><span>Moves</span><ul>
          <li><span class="min-w-0 flex-1 truncate text-xs">Weather Ball</span></li>
        </ul>
        <span>ITEMS</span><span>Items</span><ul>
          <li><span class="min-w-0 flex-1 truncate text-xs">Life Orb</span></li>
        </ul>
        <span>ABILITY</span><ul>
          <li><span class="min-w-0 flex-1 truncate text-xs">Forecast</span></li>
        </ul>
        <span>NATURE</span><ul>
          <li><span class="min-w-0 flex-1 truncate text-xs">Modest</span></li>
        </ul>
        <span>PARTNER</span>
        """
        snapshot = {
            "pokemonSlug": "castform", "rank": 7,
            "moves": [{"name": "ウェザーボール", "percentage": 88.5}],
            "items": [{"name": "いのちのたま", "percentage": 50}],
            "abilities": [{"name": "てんきや", "percentage": 100}],
            "natures": [{"name": "ひかえめ", "percentage": 41.2}],
            "evs": [{"percentage": 35, "hp": 2, "atk": 0, "def": 0, "spAtk": 32, "spDef": 0, "speed": 32}],
        }
        payload = {"slug": "castform", "variants": {"M-6:single": snapshot}}
        name_maps = importer._pokecham_name_maps(payload, html, "M-6")
        battle = importer._pokecham_battle(snapshot, name_maps)
        self.assertEqual("Weather Ball", battle["top"]["move"]["name"])
        self.assertEqual("88.5%", battle["top"]["move"]["percentage"])
        self.assertEqual("Life Orb", battle["top"]["held_item"]["name"])
        self.assertEqual("Modest", battle["top"]["stat_alignment"]["name"])
        self.assertEqual(32, battle["top"]["stat_points"]["sp_atk_points"])

    def test_pokecham_slug_aliases_cover_ranked_forms(self):
        available = {
            "aegislash-shield-forme", "basculegion-male", "floette-form-5",
            "alolan-ninetales", "hisuian-arcanine",
        }
        self.assertEqual("aegislash-shield-forme", importer._pokecham_catalog_slug("aegislash", available))
        self.assertEqual("basculegion-male", importer._pokecham_catalog_slug("basculegion", available))
        self.assertEqual("floette-form-5", importer._pokecham_catalog_slug("floette-eternal", available))
        self.assertEqual("alolan-ninetales", importer._pokecham_catalog_slug("ninetales-alola", available))
        self.assertEqual("hisuian-arcanine", importer._pokecham_catalog_slug("arcanine-hisui", available))

    def test_refresh_switches_to_pokecham_after_primary_failure(self):
        expected = {"season": "M-6", "source": "PokéChamp DB（备用镜像）"}
        with patch.object(importer, "refresh_from_battle_data", side_effect=TimeoutError("timeout")), \
             patch.object(importer, "refresh_from_pokecham", return_value=expected):
            self.assertEqual(expected, importer.refresh())


if __name__ == "__main__":
    unittest.main()
