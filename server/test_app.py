import json
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
import stat_model


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
        "catalog": [{"slug":"garchomp","name":"Garchomp","displayName":"烈咬陆鲨","sprite":sprite,"types":["龙","地面"],"baseStats":{"hp":108,"atk":130,"def":95,"spa":80,"spd":85,"spe":102},"stats":{"hp":183,"atk":150,"def":115,"spa":100,"spd":105,"spe":122},"battles":{"Doubles":battle,"Singles":None}}],
        "calculator": [{"slug":"garchomp","name":"Garchomp","baseName":"Garchomp","displayName":"烈咬陆鲨","baseDisplayName":"烈咬陆鲨","searchText":"garchomp 烈咬陆鲨","sprite":sprite,"types":["龙","地面"],"baseStats":{"hp":108,"atk":130,"def":95,"spa":80,"spd":85,"spe":102},"stats":{"hp":183,"atk":150,"def":115,"spa":100,"spd":105,"spe":122},"abilities":["Rough Skin","Sand Veil"],"learnableMoves":["Earthquake"]}],
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

    def test_type_palette_is_readable_on_dark_theme(self):
        def channels(hex_color):
            return [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]

        def luminance(hex_color):
            normalized = [value / 255 for value in channels(hex_color)]
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in normalized]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        surface_channels = channels("#0d1220")
        colors = db.load_bootstrap()["static"]["typeColors"]
        self.assertEqual(18, len(colors))
        for type_name, color in colors.items():
            color_channels = channels(color)
            badge_channels = [round(foreground * 0.14 + background * 0.86) for foreground, background in zip(color_channels, surface_channels)]
            badge_color = "#" + "".join(f"{value:02x}" for value in badge_channels)
            contrast = (luminance(color) + 0.05) / (luminance(badge_color) + 0.05)
            self.assertGreaterEqual(contrast, 4.5, f"{type_name}属性标签对比度不足: {contrast:.2f}:1")

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
        self.assertEqual(130, calculator["calculator"][0]["baseStats"]["atk"])
        self.assertEqual(150, calculator["calculator"][0]["stats"]["atk"])
        self.assertTrue(bootstrap["meta"]["statCoverage"]["complete"])
        self.assertNotIn("aliases", calculator)
        self.assertEqual(1, bootstrap["meta"]["translationCoverage"]["missingTotal"])
        self.assertEqual(1, bootstrap["meta"]["translationCoverage"]["ability"]["missing"])

    def test_reference_lists_search_only_names_and_return_chinese(self):
        self.store["moves"] = [
            {"name": "Earthquake", "type": "地面", "category": "物理", "power": 100},
        ]
        self.save_fixture()
        pokemon = db.load_reference("pokemon", "garch")
        self.assertEqual(1, pokemon["total"])
        self.assertEqual("烈咬陆鲨", pokemon["items"][0]["name"])
        self.assertEqual(1, db.load_reference("pokemon", "烈咬")["total"])
        self.assertEqual(0, db.load_reference("pokemon", "龙")["total"])
        items = db.load_reference("item", "life orb")
        self.assertEqual(["生命宝珠"], [entry["name"] for entry in items["items"]])
        self.assertEqual(0, db.load_reference("item", "提升招式")["total"])
        abilities = db.load_reference("ability", "rough skin")
        self.assertEqual(["粗糙皮肤"], [entry["name"] for entry in abilities["items"]])

    def test_reference_details_include_moves_stats_descriptions_and_relations(self):
        self.store["moves"] = [
            {"name": "Earthquake", "type": "地面", "category": "物理", "power": 100},
        ]
        self.save_fixture()
        pokemon = db.load_reference_detail("pokemon", "garchomp")["detail"]
        self.assertEqual(130, pokemon["baseStats"]["atk"])
        self.assertEqual(150, pokemon["stats"]["atk"])
        self.assertEqual("地震", pokemon["moves"][0]["name"])
        self.assertEqual({"粗糙皮肤", "沙隐"}, {entry["name"] for entry in pokemon["abilities"]})
        item = db.load_reference_detail("item", "life-orb")["detail"]
        self.assertTrue(item["descriptionZh"])
        self.assertEqual("garchomp", item["pokemon"][0]["slug"])
        self.assertEqual(1, item["pokemon"][0]["rank"])
        ability = db.load_reference_detail("ability", "rough-skin")["detail"]
        self.assertTrue(ability["descriptionZh"])
        self.assertEqual("garchomp", ability["pokemon"][0]["slug"])

    def test_reference_http_contract_errors_etag_and_pagination(self):
        self.save_fixture()
        client = app_module.app.test_client()
        first = client.get("/api/reference?kind=pokemon&limit=1", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(200, first.status_code)
        self.assertEqual(1, first.get_json()["total"])
        self.assertIn("max-age=300", first.headers["Cache-Control"])
        second = client.get("/api/reference?kind=pokemon&limit=1", headers={"If-None-Match": first.headers["ETag"]})
        self.assertEqual(304, second.status_code)
        self.assertEqual(400, client.get("/api/reference?kind=move").status_code)
        self.assertEqual(400, client.get("/api/reference?limit=nope").status_code)
        self.assertEqual(404, client.get("/api/reference/pokemon/missing").status_code)
        detail = client.get("/api/reference/ability/rough-skin")
        self.assertEqual("粗糙皮肤", detail.get_json()["detail"]["name"])

    def test_v4_database_is_migrated_without_deleting_data(self):
        legacy = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = __import__("sqlite3").connect(legacy)
        connection.executescript("""
          CREATE TABLE items(name TEXT PRIMARY KEY,name_zh TEXT NOT NULL,implemented INTEGER NOT NULL DEFAULT 1);
          INSERT INTO items VALUES('Life Orb','生命宝珠',1);
          CREATE TABLE pokemon_forms(
            id INTEGER PRIMARY KEY AUTOINCREMENT,season_id INTEGER NOT NULL,slug TEXT NOT NULL,
            name TEXT NOT NULL,base_name TEXT NOT NULL,display_name TEXT NOT NULL,
            base_display_name TEXT NOT NULL,search_text TEXT NOT NULL,sprite TEXT,
            types_json TEXT NOT NULL,stats_json TEXT NOT NULL,learnable_moves_json TEXT NOT NULL,
            UNIQUE(season_id,slug)
          );
        """)
        connection.close()
        db.DB_PATH = legacy
        db.init_database()
        with db.database() as migrated:
            item_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(items)")}
            form_columns = {row["name"] for row in migrated.execute("PRAGMA table_info(pokemon_forms)")}
            self.assertTrue({"slug", "description_zh"}.issubset(item_columns))
            self.assertTrue({"abilities_json", "base_stats_json"}.issubset(form_columns))
            self.assertEqual("生命宝珠", migrated.execute("SELECT name_zh FROM items WHERE name='Life Orb'").fetchone()[0])

    def test_champions_stat_model_round_trip_and_golisopod_values(self):
        base = {"hp":75, "atk":125, "def":140, "spa":60, "spd":90, "spe":40}
        level_50 = {"hp":150, "atk":145, "def":160, "spa":80, "spd":110, "spe":60}
        self.assertEqual(level_50, stat_model.level_50_neutral_from_base(base))
        self.assertEqual(base, stat_model.base_from_level_50_neutral(level_50))
        self.assertTrue(stat_model.audit_stat_pair(base, level_50))
        self.assertFalse(stat_model.audit_stat_pair(base, {**level_50, "def": 159}))

    def test_stat_model_rejects_malformed_source_values(self):
        with self.assertRaises(ValueError):
            stat_model.source_level_50_stats({"hp": 150})
        malformed = {"hp":150, "attack":145.5, "defense":160, "sp_attack":80, "sp_defense":110, "speed":60}
        with self.assertRaises(ValueError):
            stat_model.source_level_50_stats(malformed)
        with self.assertRaises(ValueError):
            stat_model.base_from_level_50_neutral(
                {"hp": 10, "atk": 10, "def": 10, "spa": 10, "spd": 10, "spe": 10}
            )

    def test_existing_season_backfills_reference_implementation_flags(self):
        self.save_fixture()
        with db.database() as connection:
            connection.execute("UPDATE pokemon_forms SET abilities_json='[]'")
            connection.execute("UPDATE abilities SET implemented=0")
            connection.execute("UPDATE items SET implemented=0")
            connection.commit()
        db.init_database()
        with db.database() as connection:
            abilities = json.loads(connection.execute(
                "SELECT abilities_json FROM pokemon_forms WHERE slug='garchomp'"
            ).fetchone()[0])
            self.assertIn("Rough Skin", abilities)
            self.assertEqual(1, connection.execute(
                "SELECT implemented FROM abilities WHERE name='Rough Skin'"
            ).fetchone()[0])
            self.assertEqual(1, connection.execute(
                "SELECT implemented FROM items WHERE name='Life Orb'"
            ).fetchone()[0])

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

    def test_unknown_api_returns_json_instead_of_html(self):
        response = app_module.app.test_client().get("/api/not-a-real-route")
        self.assertEqual(404, response.status_code)
        self.assertEqual("application/json", response.content_type)
        self.assertEqual({"error": "接口不存在"}, response.get_json())

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
