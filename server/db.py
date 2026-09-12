import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR.parent / "data" / "champions.sqlite3"))
SCHEMA_PATH = BASE_DIR / "schema.sql"
SEED_PATH = BASE_DIR / "seed.json"


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def database():
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def seed_static(connection):
    if connection.execute("SELECT value FROM app_meta WHERE key='static_seed_version'").fetchone():
        return
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    english_by_zh = {value: key for key, value in seed["TYPE_ZH"].items()}
    for order, name_zh in enumerate(seed["TYPES"]):
        connection.execute(
            "INSERT INTO types(name_zh,name_en,color,sort_order) VALUES(?,?,?,?)",
            (name_zh, english_by_zh.get(name_zh), seed["TYPE_COLORS"][name_zh], order),
        )
    for attack in seed["TYPES"]:
        for defense, multiplier in seed["TYPE_CHART"].get(attack, {}).items():
            connection.execute(
                "INSERT INTO type_effectiveness(attack_type,defense_type,multiplier) VALUES(?,?,?)",
                (attack, defense, multiplier),
            )
    translation_groups = {
        "pokemon": seed["NAME_ZH"], "item": seed["ITEM_ZH"],
        "move": seed["MOVE_ZH"], "ability": seed["ABILITY_ZH"],
        "nature": seed["NATURE_ZH"],
    }
    for kind, entries in translation_groups.items():
        connection.executemany(
            "INSERT INTO translations(kind,source_name,zh_name) VALUES(?,?,?)",
            [(kind, source, translated) for source, translated in entries.items()],
        )
    for move in seed["MOVES"]:
        connection.execute(
            "INSERT INTO moves(name,name_zh,type_zh,category,power) VALUES(?,?,?,?,?)",
            (move["name"], seed["MOVE_ZH"].get(move["name"], move["name"]), move["type"], move["category"], move["power"]),
        )
    for item in seed["IMPLEMENTED_ITEMS"]:
        connection.execute(
            "INSERT INTO items(name,name_zh,implemented) VALUES(?,?,1)",
            (item, seed["ITEM_ZH"].get(item, item)),
        )
    for nature in seed["NATURES"]:
        connection.execute(
            "INSERT INTO natures(name,name_zh) VALUES(?,?)",
            (nature, seed["NATURE_ZH"].get(nature, nature)),
        )
    for side, stats in seed["DEFAULT_POINTS"].items():
        for sort_order, stat_key in enumerate(seed["STAT_KEYS"]):
            connection.execute(
                "INSERT INTO stat_defaults(side,stat_key,label,points,sort_order) VALUES(?,?,?,?,?)",
                (side, stat_key, seed["STAT_LABELS"][stat_key], stats[stat_key], sort_order),
            )
    connection.execute("INSERT INTO app_meta(key,value) VALUES('static_seed_version','1')")


def init_database():
    with database() as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        seed_static(connection)
        connection.commit()


def translation_map(connection, kind):
    return {row["source_name"]: row["zh_name"] for row in connection.execute(
        "SELECT source_name,zh_name FROM translations WHERE kind=?", (kind,)
    )}


def static_data(connection):
    types = list(connection.execute("SELECT * FROM types ORDER BY sort_order"))
    chart = {}
    for row in connection.execute("SELECT * FROM type_effectiveness"):
        chart.setdefault(row["attack_type"], {})[row["defense_type"]] = row["multiplier"]
    defaults = {"attacker": {}, "defender": {}}
    labels = {}
    stat_keys = []
    for row in connection.execute("SELECT * FROM stat_defaults ORDER BY sort_order,side"):
        defaults[row["side"]][row["stat_key"]] = row["points"]
        labels[row["stat_key"]] = row["label"]
        if row["stat_key"] not in stat_keys:
            stat_keys.append(row["stat_key"])
    translations = {kind: translation_map(connection, kind) for kind in ("pokemon", "item", "move", "ability", "nature")}
    moves = [dict(row) for row in connection.execute("SELECT name,type_zh AS type,category,power FROM moves ORDER BY rowid")]
    items = [row["name"] for row in connection.execute("SELECT name FROM items WHERE implemented=1 ORDER BY rowid")]
    natures = [row["name"] for row in connection.execute("SELECT name FROM natures ORDER BY rowid")]
    return {
        "types": [row["name_zh"] for row in types],
        "typeZh": {row["name_en"]: row["name_zh"] for row in types if row["name_en"]},
        "typeColors": {row["name_zh"]: row["color"] for row in types},
        "typeChart": chart,
        "nameZh": translations["pokemon"], "itemZh": translations["item"],
        "moveZh": translations["move"], "abilityZh": translations["ability"],
        "natureZh": translations["nature"], "natures": natures,
        "implementedItems": items, "moves": moves,
        "statKeys": stat_keys, "statLabels": labels, "defaultPoints": defaults,
    }


def save_snapshot(store, raw_payload, aliases=None):
    with database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        meta = store["meta"]
        connection.execute("UPDATE seasons SET is_active=0")
        connection.execute(
            """INSERT INTO seasons(code,generated_at,data_version,source,refreshed_at,is_active)
               VALUES(?,?,?,?,?,1)
               ON CONFLICT(code) DO UPDATE SET generated_at=excluded.generated_at,
                 data_version=excluded.data_version,source=excluded.source,
                 refreshed_at=excluded.refreshed_at,is_active=1""",
            (meta["season"], meta.get("generatedAt"), meta.get("dataVersion"), meta["source"], datetime.now(timezone.utc).isoformat()),
        )
        season_id = connection.execute("SELECT id FROM seasons WHERE code=?", (meta["season"],)).fetchone()["id"]
        for table in ("pokemon_species", "pokemon_forms", "season_items", "source_snapshots"):
            connection.execute(f"DELETE FROM {table} WHERE season_id=?", (season_id,))
        connection.executemany(
            """INSERT INTO pokemon_species(season_id,slug,name,display_name,sprite,types_json,stats_json,battles_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            [(season_id, p["slug"], p["name"], p["displayName"], p.get("sprite"), _json(p["types"]), _json(p["stats"]), _json(p["battles"])) for p in store["catalog"]],
        )
        connection.executemany(
            """INSERT INTO pokemon_forms(season_id,slug,name,base_name,display_name,base_display_name,search_text,sprite,types_json,stats_json,learnable_moves_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            [(season_id, p["slug"], p["name"], p["baseName"], p["displayName"], p["baseDisplayName"], p["searchText"], p.get("sprite"), _json(p["types"]), _json(p["stats"]), _json(p["learnableMoves"])) for p in store["calculator"]],
        )
        connection.executemany("INSERT INTO season_items(season_id,item_name) VALUES(?,?)", [(season_id, name) for name in store["items"]])
        connection.execute("INSERT INTO source_snapshots(season_id,payload_json) VALUES(?,?)", (season_id, _json(raw_payload)))
        if aliases:
            connection.execute("DELETE FROM translations WHERE kind='pokemon_api'")
            connection.executemany("INSERT INTO translations(kind,source_name,zh_name) VALUES('pokemon_api',?,?)", aliases.items())
        connection.commit()


def load_bootstrap():
    with database() as connection:
        static = static_data(connection)
        season = connection.execute("SELECT * FROM seasons WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()
        if not season:
            return {"static": static, "meta": None, "aliases": {}, "catalog": [], "calculator": [], "items": static["implementedItems"]}
        sid = season["id"]
        catalog = []
        for row in connection.execute("SELECT * FROM pokemon_species WHERE season_id=? ORDER BY id", (sid,)):
            catalog.append({"name":row["name"],"displayName":row["display_name"],"slug":row["slug"],"sprite":row["sprite"],"types":json.loads(row["types_json"]),"stats":json.loads(row["stats_json"]),"battles":json.loads(row["battles_json"])})
        calculator = []
        for row in connection.execute("SELECT * FROM pokemon_forms WHERE season_id=? ORDER BY display_name", (sid,)):
            calculator.append({"name":row["name"],"baseName":row["base_name"],"displayName":row["display_name"],"baseDisplayName":row["base_display_name"],"searchText":row["search_text"],"slug":row["slug"],"sprite":row["sprite"],"types":json.loads(row["types_json"]),"stats":json.loads(row["stats_json"]),"learnableMoves":json.loads(row["learnable_moves_json"])})
        aliases = {row["source_name"]: row["zh_name"] for row in connection.execute("SELECT source_name,zh_name FROM translations WHERE kind='pokemon_api'")}
        season_items = [row["item_name"] for row in connection.execute("SELECT item_name FROM season_items WHERE season_id=?", (sid,))]
        season_items.sort(key=lambda name: static["itemZh"].get(name, name))
        return {
            "static": static,
            "meta": {"season":season["code"],"generatedAt":season["generated_at"],"dataVersion":season["data_version"],"source":season["source"]},
            "aliases": aliases, "catalog": catalog, "calculator": calculator,
            "items": season_items,
        }


def replace_api_aliases(aliases):
    with database() as connection:
        connection.execute("DELETE FROM translations WHERE kind='pokemon_api'")
        connection.executemany("INSERT INTO translations(kind,source_name,zh_name) VALUES('pokemon_api',?,?)", aliases.items())
        connection.commit()


def health_info():
    with database() as connection:
        season = connection.execute("SELECT code FROM seasons WHERE is_active=1 LIMIT 1").fetchone()
        return {
            "database": str(DB_PATH), "season": season["code"] if season else None,
            "species": connection.execute("SELECT COUNT(*) FROM pokemon_species").fetchone()[0],
            "forms": connection.execute("SELECT COUNT(*) FROM pokemon_forms").fetchone()[0],
        }
