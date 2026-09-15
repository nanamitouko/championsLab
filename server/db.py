import json
import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from stat_model import audit_stat_pair, base_from_level_50_neutral

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR.parent / "data" / "champions.sqlite3"))
SCHEMA_PATH = BASE_DIR / "schema.sql"
SEED_PATH = BASE_DIR / "seed.json"
TRANSLATION_SNAPSHOT_PATH = BASE_DIR / "translations.zh-CN.json"
TRANSLATION_OVERRIDES_PATH = BASE_DIR / "translation_overrides.zh-CN.json"
POKECHAM_OVERRIDES_PATH = BASE_DIR / "pokecham_overrides.json"
REFERENCE_DATA_PATH = BASE_DIR / "reference.zh-CN.json"
REFERENCE_OVERRIDES_PATH = BASE_DIR / "reference_overrides.zh-CN.json"


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


def _sprite_cache_key(version, source_url):
    return hashlib.sha256(f"{version}\0{source_url}".encode("utf-8")).hexdigest()[:24]


def _sprite_sources(store):
    return {
        entry.get("sprite")
        for group in (store.get("catalog", []), store.get("calculator", []))
        for entry in group
        if str(entry.get("sprite") or "").startswith("https://championsbattledata.com/")
    }


def _register_sprite_sources(connection, season_id, version, source_urls):
    for source_url in source_urls:
        if not str(source_url or "").startswith("https://championsbattledata.com/"):
            continue
        cache_key = _sprite_cache_key(version, source_url)
        connection.execute(
            """INSERT INTO sprite_assets(cache_key,season_id,source_url,local_path)
               VALUES(?,?,?,?)
               ON CONFLICT(season_id,source_url) DO UPDATE SET
                 cache_key=excluded.cache_key,
                 local_path=excluded.local_path,
                 mime_type=CASE WHEN sprite_assets.cache_key=excluded.cache_key THEN sprite_assets.mime_type END,
                 byte_size=CASE WHEN sprite_assets.cache_key=excluded.cache_key THEN sprite_assets.byte_size END,
                 fetched_at=CASE WHEN sprite_assets.cache_key=excluded.cache_key THEN sprite_assets.fetched_at END""",
            (cache_key, season_id, source_url, cache_key),
        )


def _prune_sprite_sources(connection, season_id, source_urls):
    source_urls = tuple(source_urls)
    if not source_urls:
        connection.execute("DELETE FROM sprite_assets WHERE season_id=?", (season_id,))
        return
    placeholders = ",".join("?" for _ in source_urls)
    connection.execute(
        f"DELETE FROM sprite_assets WHERE season_id=? AND source_url NOT IN ({placeholders})",
        (season_id, *source_urls),
    )


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _entity_slug(name):
    value = str(name or "").lower().replace("’", "'").replace("♀", "-female").replace("♂", "-male")
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", value))


def _ensure_column(connection, table, column, declaration):
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def migrate_schema(connection):
    _ensure_column(connection, "items", "slug", "TEXT")
    _ensure_column(connection, "items", "description_zh", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "pokemon_forms", "abilities_json", "TEXT NOT NULL DEFAULT '[]'")
    for table in ("pokemon_species", "pokemon_forms"):
        _ensure_column(connection, table, "base_stats_json", "TEXT")
        for row in connection.execute(
            f"SELECT id,stats_json FROM {table} WHERE base_stats_json IS NULL OR base_stats_json=''"
        ):
            try:
                base_stats = base_from_level_50_neutral(json.loads(row["stats_json"]))
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                continue
            connection.execute(
                f"UPDATE {table} SET base_stats_json=? WHERE id=?", (_json(base_stats), row["id"])
            )
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_slug_unique ON items(slug)")


def bundled_translation_groups():
    snapshot = _read_json(TRANSLATION_SNAPSHOT_PATH)
    overrides = _read_json(TRANSLATION_OVERRIDES_PATH)
    pokecham_overrides = _read_json(POKECHAM_OVERRIDES_PATH)
    groups = {kind: dict(values) for kind, values in snapshot.items()}
    for source in (overrides, pokecham_overrides):
        for kind, values in source.items():
            groups.setdefault(kind, {}).update(values)
    return groups


def translation_overrides():
    return _read_json(TRANSLATION_OVERRIDES_PATH)


def upsert_translation_groups(connection, groups):
    for kind, entries in groups.items():
        connection.executemany(
            """INSERT INTO translations(kind,source_name,zh_name) VALUES(?,?,?)
               ON CONFLICT(kind,source_name) DO UPDATE SET zh_name=excluded.zh_name""",
            [(kind, source, translated) for source, translated in entries.items() if source and translated],
        )


def seed_static(connection):
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    english_by_zh = {value: key for key, value in seed["TYPE_ZH"].items()}
    for order, name_zh in enumerate(seed["TYPES"]):
        connection.execute(
            """INSERT INTO types(name_zh,name_en,color,sort_order) VALUES(?,?,?,?)
               ON CONFLICT(name_zh) DO UPDATE SET name_en=excluded.name_en,
                 color=excluded.color,sort_order=excluded.sort_order""",
            (name_zh, english_by_zh.get(name_zh), seed["TYPE_COLORS"][name_zh], order),
        )
    for attack in seed["TYPES"]:
        for defense, multiplier in seed["TYPE_CHART"].get(attack, {}).items():
            connection.execute(
                """INSERT INTO type_effectiveness(attack_type,defense_type,multiplier) VALUES(?,?,?)
                   ON CONFLICT(attack_type,defense_type) DO UPDATE SET multiplier=excluded.multiplier""",
                (attack, defense, multiplier),
            )
    translation_groups = {
        "pokemon": seed["NAME_ZH"], "item": seed["ITEM_ZH"],
        "move": seed["MOVE_ZH"], "ability": seed["ABILITY_ZH"],
        "nature": seed["NATURE_ZH"],
    }
    upsert_translation_groups(connection, translation_groups)
    bundled = bundled_translation_groups()
    upsert_translation_groups(connection, bundled)
    effective = {kind: {**translation_groups.get(kind, {}), **bundled.get(kind, {})} for kind in translation_groups}
    for move in seed["MOVES"]:
        connection.execute(
            """INSERT INTO moves(name,name_zh,type_zh,category,power) VALUES(?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET name_zh=excluded.name_zh,
                 type_zh=excluded.type_zh,category=excluded.category,power=excluded.power""",
            (move["name"], effective["move"].get(move["name"], move["name"]), move["type"], move["category"], move["power"]),
        )
    reference = _read_json(REFERENCE_DATA_PATH)
    descriptions = reference.get("descriptions") or {}
    reference_overrides = _read_json(REFERENCE_OVERRIDES_PATH)
    descriptions = {
        kind: {**(descriptions.get(kind) or {}), **(reference_overrides.get(kind) or {})}
        for kind in ("item", "ability")
    }
    canonical_items = {_entity_slug(name): name for name in seed["IMPLEMENTED_ITEMS"]}
    for item, description in (descriptions.get("item") or {}).items():
        source_item = item
        item = canonical_items.get(_entity_slug(item), item)
        if item != source_item or connection.execute(
            "SELECT 1 FROM items WHERE slug=? AND name<>?", (_entity_slug(item), item)
        ).fetchone():
            connection.execute("DELETE FROM items WHERE slug=? AND name<>?", (_entity_slug(item), item))
        connection.execute(
            """INSERT INTO items(name,slug,name_zh,description_zh,implemented) VALUES(?,?,?,?,0)
               ON CONFLICT(name) DO UPDATE SET slug=excluded.slug,
                 name_zh=excluded.name_zh,description_zh=excluded.description_zh""",
            (item, _entity_slug(item), effective["item"].get(item, item), description),
        )
    for item in seed["IMPLEMENTED_ITEMS"]:
        connection.execute(
            """INSERT INTO items(name,slug,name_zh,description_zh,implemented) VALUES(?,?,?,?,1)
               ON CONFLICT(name) DO UPDATE SET slug=excluded.slug,name_zh=excluded.name_zh,
                 description_zh=CASE WHEN items.description_zh='' THEN excluded.description_zh ELSE items.description_zh END,
                 implemented=1""",
            (item, _entity_slug(item), effective["item"].get(item, item),
             (descriptions.get("item") or {}).get(item, "")),
        )
    for ability, description in (descriptions.get("ability") or {}).items():
        connection.execute("DELETE FROM abilities WHERE slug=? AND name<>?", (_entity_slug(ability), ability))
        connection.execute(
            """INSERT INTO abilities(name,slug,name_zh,description_zh,implemented) VALUES(?,?,?,?,0)
               ON CONFLICT(name) DO UPDATE SET slug=excluded.slug,name_zh=excluded.name_zh,
                 description_zh=excluded.description_zh""",
            (ability, _entity_slug(ability), effective["ability"].get(ability, ability), description),
        )
    for nature in seed["NATURES"]:
        connection.execute(
            """INSERT INTO natures(name,name_zh) VALUES(?,?)
               ON CONFLICT(name) DO UPDATE SET name_zh=excluded.name_zh""",
            (nature, seed["NATURE_ZH"].get(nature, nature)),
        )
    for side, stats in seed["DEFAULT_POINTS"].items():
        for sort_order, stat_key in enumerate(seed["STAT_KEYS"]):
            connection.execute(
                """INSERT INTO stat_defaults(side,stat_key,label,points,sort_order) VALUES(?,?,?,?,?)
                   ON CONFLICT(side,stat_key) DO UPDATE SET label=excluded.label,
                     points=excluded.points,sort_order=excluded.sort_order""",
                (side, stat_key, seed["STAT_LABELS"][stat_key], stats[stat_key], sort_order),
            )
    connection.execute(
        """INSERT INTO app_meta(key,value) VALUES('static_seed_version','5')
           ON CONFLICT(key) DO UPDATE SET value=excluded.value"""
    )

    form_abilities = reference.get("formAbilities") or {}
    for slug, abilities in form_abilities.items():
        connection.execute(
            """UPDATE pokemon_forms SET abilities_json=?
               WHERE slug=? AND (abilities_json IS NULL OR abilities_json='[]')""",
            (_json(abilities), slug),
        )
    active = connection.execute("SELECT id FROM seasons WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if active:
        season_id = active["id"]
        connection.execute("UPDATE items SET implemented=0")
        connection.execute(
            "UPDATE items SET implemented=1 WHERE name IN (SELECT item_name FROM season_items WHERE season_id=?)",
            (season_id,),
        )
        connection.execute("UPDATE abilities SET implemented=0")
        active_abilities = {
            name
            for row in connection.execute(
                "SELECT abilities_json FROM pokemon_forms WHERE season_id=?", (season_id,)
            )
            for name in json.loads(row["abilities_json"] or "[]")
            if name
        }
        for name in active_abilities:
            connection.execute("UPDATE abilities SET implemented=1 WHERE name=?", (name,))


def init_database():
    with database() as connection:
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        migrate_schema(connection)
        seed_static(connection)
        for season in connection.execute("SELECT id,code,data_version FROM seasons"):
            urls = {
                row["sprite"]
                for table in ("pokemon_species", "pokemon_forms")
                for row in connection.execute(f"SELECT sprite FROM {table} WHERE season_id=?", (season["id"],))
                if row["sprite"]
            }
            _register_sprite_sources(connection, season["id"], season["data_version"] or season["code"], urls)
        connection.commit()


def translation_map(connection, kind):
    return {row["source_name"]: row["zh_name"] for row in connection.execute(
        "SELECT source_name,zh_name FROM translations WHERE kind=?", (kind,)
    )}


def _species_key(name):
    value = str(name or "").lower()
    value = re.sub(r"^mega\s+", "", value)
    value = re.sub(r"^(alolan|hisuian|galarian|paldean)\s+", "", value)
    value = re.sub(r"\s*\[.*$", "", value)
    value = re.sub(r"\s+(male|female)$", "", value)
    value = re.sub(r"\s+z$", "", value)
    value = re.sub(r"[.'’]", "", value)
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", value))


def _pokemon_localizer(connection):
    exact = translation_map(connection, "pokemon")
    aliases = translation_map(connection, "pokemon_api")
    return lambda source, stored: exact.get(source) or aliases.get(_species_key(source)) or stored


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


def _store_base_stats(pokemon):
    if "baseStats" in pokemon:
        return pokemon["baseStats"]
    return base_from_level_50_neutral(pokemon["stats"])


def save_snapshot(store, raw_payload, aliases=None, translation_groups=None):
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
        for table in ("pokemon_species", "pokemon_forms", "season_items", "season_moves", "source_snapshots"):
            connection.execute(f"DELETE FROM {table} WHERE season_id=?", (season_id,))
        connection.executemany(
            """INSERT INTO pokemon_species(season_id,slug,name,display_name,sprite,types_json,base_stats_json,stats_json,battles_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            [(season_id, p["slug"], p["name"], p["displayName"], p.get("sprite"), _json(p["types"]), _json(_store_base_stats(p)), _json(p["stats"]), _json(p["battles"])) for p in store["catalog"]],
        )
        connection.executemany(
            """INSERT INTO pokemon_forms(season_id,slug,name,base_name,display_name,base_display_name,search_text,sprite,types_json,base_stats_json,stats_json,abilities_json,learnable_moves_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(season_id, p["slug"], p["name"], p["baseName"], p["displayName"], p["baseDisplayName"], p["searchText"], p.get("sprite"), _json(p["types"]), _json(_store_base_stats(p)), _json(p["stats"]), _json(p.get("abilities", [])), _json(p["learnableMoves"])) for p in store["calculator"]],
        )
        item_zh = translation_map(connection, "item")
        ability_zh = translation_map(connection, "ability")
        connection.execute("UPDATE items SET implemented=0")
        connection.execute("UPDATE abilities SET implemented=0")
        for name in store["items"]:
            connection.execute(
                """INSERT INTO items(name,slug,name_zh,description_zh,implemented) VALUES(?,?,?,'',1)
                   ON CONFLICT(name) DO UPDATE SET slug=excluded.slug,name_zh=excluded.name_zh,implemented=1""",
                (name, _entity_slug(name), item_zh.get(name, name)),
            )
        implemented_abilities = {
            name for pokemon in store["calculator"] for name in pokemon.get("abilities", []) if name
        }
        for name in implemented_abilities:
            connection.execute(
                """INSERT INTO abilities(name,slug,name_zh,description_zh,implemented) VALUES(?,?,?,'',1)
                   ON CONFLICT(name) DO UPDATE SET slug=excluded.slug,name_zh=excluded.name_zh,implemented=1""",
                (name, _entity_slug(name), ability_zh.get(name, name)),
            )
        connection.executemany("INSERT INTO season_items(season_id,item_name) VALUES(?,?)", [(season_id, name) for name in store["items"]])
        connection.executemany(
            "INSERT INTO season_moves(season_id,name,type_zh,category,power) VALUES(?,?,?,?,?)",
            [(season_id, move["name"], move["type"], move["category"], move.get("power")) for move in store.get("moves", [])],
        )
        connection.execute("INSERT INTO source_snapshots(season_id,payload_json) VALUES(?,?)", (season_id, _json(raw_payload)))
        sprite_version = meta.get("dataVersion") or meta["season"]
        sprite_sources = _sprite_sources(store)
        _register_sprite_sources(connection, season_id, sprite_version, sprite_sources)
        _prune_sprite_sources(connection, season_id, sprite_sources)
        if translation_groups:
            upsert_translation_groups(connection, translation_groups)
        if aliases:
            upsert_translation_groups(connection, {"pokemon_api": aliases})
        connection.commit()


def _season_translation_names(connection, season_id):
    names = {"move": set(), "item": set(), "ability": set()}
    for row in connection.execute("SELECT battles_json FROM pokemon_species WHERE season_id=?", (season_id,)):
        for battle in (json.loads(row["battles_json"]) or {}).values():
            if not battle:
                continue
            top = battle.get("top") or {}
            values = battle.get("values") or {}
            for source_key, kind in (("move", "move"), ("held_item", "item"), ("ability", "ability")):
                top_name = (top.get(source_key) or {}).get("name")
                if top_name:
                    names[kind].add(top_name)
                names[kind].update(value for value in values.get(source_key, []) if value)
    for row in connection.execute("SELECT learnable_moves_json FROM pokemon_forms WHERE season_id=?", (season_id,)):
        names["move"].update(value for value in json.loads(row["learnable_moves_json"]) if value)
    for row in connection.execute("SELECT abilities_json FROM pokemon_forms WHERE season_id=?", (season_id,)):
        names["ability"].update(value for value in json.loads(row["abilities_json"] or "[]") if value)
    names["item"].update(
        row["item_name"]
        for row in connection.execute("SELECT item_name FROM season_items WHERE season_id=?", (season_id,))
    )
    return names


def _translation_coverage(connection, season):
    if not season:
        return {"allTranslated": True, "missingTotal": 0, "pokemon": {"total": 0, "missing": 0}, "move": {"total": 0, "missing": 0}, "item": {"total": 0, "missing": 0}, "ability": {"total": 0, "missing": 0}}
    names = _season_translation_names(connection, season["id"])
    coverage = {}
    for kind in ("move", "item", "ability"):
        translated = translation_map(connection, kind)
        missing = [name for name in names[kind] if not translated.get(name)]
        coverage[kind] = {"total": len(names[kind]), "missing": len(missing)}
    localize = _pokemon_localizer(connection)
    display_names = {
        localize(row["name"], row["display_name"])
        for row in connection.execute(
            "SELECT name,display_name FROM pokemon_forms WHERE season_id=?", (season["id"],)
        )
    }
    for row in connection.execute(
        "SELECT name,display_name,battles_json FROM pokemon_species WHERE season_id=?", (season["id"],)
    ):
        battles = (json.loads(row["battles_json"]) or {}).values()
        if any(isinstance((battle or {}).get("position"), int) and 1 <= battle["position"] <= 50 for battle in battles):
            display_names.add(localize(row["name"], row["display_name"]))
    pokemon_missing = [name for name in display_names if re.search(r"[A-Za-z]{2,}", name)]
    coverage["pokemon"] = {"total": len(display_names), "missing": len(pokemon_missing)}
    missing_total = sum(group["missing"] for group in coverage.values())
    return {"allTranslated": missing_total == 0, "missingTotal": missing_total, **coverage}


def _move_pool_coverage(connection, season):
    if not season:
        return {"complete": True, "learnable": 0, "metadata": 0, "missing": 0, "attacking": 0, "formulaReady": 0, "conditional": 0}
    needed = {
        name
        for row in connection.execute("SELECT learnable_moves_json FROM pokemon_forms WHERE season_id=?", (season["id"],))
        for name in json.loads(row["learnable_moves_json"])
        if name
    }
    rows = list(connection.execute("SELECT name,category,power FROM season_moves WHERE season_id=?", (season["id"],)))
    available = {row["name"] for row in rows}
    attacking = [row for row in rows if row["category"] in ("物理", "特殊")]
    ready = [row for row in attacking if row["power"] is not None and row["power"] > 0]
    missing = needed - available
    return {
        "complete": not missing,
        "learnable": len(needed),
        "metadata": len(available),
        "missing": len(missing),
        "attacking": len(attacking),
        "formulaReady": len(ready),
        "conditional": len(attacking) - len(ready),
    }


def _season_moves(connection, season_id):
    return [
        {"name": row["name"], "type": row["type_zh"], "category": row["category"], "power": row["power"]}
        for row in connection.execute(
            "SELECT name,type_zh,category,power FROM season_moves WHERE season_id=? AND category IN ('物理','特殊') ORDER BY name",
            (season_id,),
        )
    ]


def _season_meta(connection, season):
    if not season:
        return None
    return {
        "season": season["code"],
        "generatedAt": season["generated_at"],
        "dataVersion": season["data_version"],
        "source": season["source"],
        "rosterCount": connection.execute(
            "SELECT COUNT(*) FROM pokemon_forms WHERE season_id=?", (season["id"],)
        ).fetchone()[0],
        "statCoverage": _stat_coverage(connection, season),
        "translationCoverage": _translation_coverage(connection, season),
        "movePoolCoverage": _move_pool_coverage(connection, season),
    }


def _active_season(connection):
    return connection.execute(
        "SELECT * FROM seasons WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _row_stats(row):
    level_50 = json.loads(row["stats_json"])
    raw_base = row["base_stats_json"] if "base_stats_json" in row.keys() else None
    try:
        base = json.loads(raw_base) if raw_base else base_from_level_50_neutral(level_50)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        base = None
    return base, level_50


def _stat_coverage(connection, season):
    if not season:
        return {"complete": True, "total": 0, "valid": 0, "invalid": 0, "rankingRowsWithoutMetadata": 0}
    total = valid = 0
    for row in connection.execute(
        "SELECT base_stats_json,stats_json FROM pokemon_forms WHERE season_id=?", (season["id"],)
    ):
        total += 1
        base, level_50 = _row_stats(row)
        if base is not None and audit_stat_pair(base, level_50):
            valid += 1
    ranking_rows_without_metadata = 0
    for row in connection.execute(
        "SELECT base_stats_json,stats_json FROM pokemon_species WHERE season_id=?", (season["id"],)
    ):
        base, level_50 = _row_stats(row)
        if base is None or not audit_stat_pair(base, level_50):
            ranking_rows_without_metadata += 1
    return {
        "complete": valid == total, "total": total, "valid": valid,
        "invalid": total - valid,
        "rankingRowsWithoutMetadata": ranking_rows_without_metadata,
    }


def _sprite_map(connection, season_id):
    return {
        row["source_url"]: f"/api/sprites/{row['cache_key']}"
        for row in connection.execute(
            "SELECT cache_key,source_url FROM sprite_assets WHERE season_id=?", (season_id,)
        )
    }


def _slim_top_entry(entry):
    if not entry:
        return None
    fields = (
        "name", "percentage", "hp_points", "attack_points", "defense_points",
        "sp_atk_points", "sp_def_points", "speed_points",
    )
    return {key: entry.get(key) for key in fields if entry.get(key) not in (None, "")}


def load_bootstrap():
    with database() as connection:
        static = static_data(connection)
        season = _active_season(connection)
        if season:
            needed = _season_translation_names(connection, season["id"])
            static["moveZh"] = {name: static["moveZh"][name] for name in needed["move"] if name in static["moveZh"]}
            static["itemZh"] = {name: static["itemZh"][name] for name in needed["item"] if name in static["itemZh"]}
            static["abilityZh"] = {name: static["abilityZh"][name] for name in needed["ability"] if name in static["abilityZh"]}
            season_moves = _season_moves(connection, season["id"])
            if season_moves:
                static["moves"] = season_moves
        return {"static": static, "meta": _season_meta(connection, season)}


def load_usage(format_name):
    if format_name not in ("Doubles", "Singles"):
        raise ValueError("对战形式必须是 Doubles 或 Singles")
    with database() as connection:
        season = _active_season(connection)
        if not season:
            return {"meta": None, "format": format_name, "catalog": []}
        sid = season["id"]
        sprites = _sprite_map(connection, sid)
        localize = _pokemon_localizer(connection)
        catalog = []
        for row in connection.execute("SELECT * FROM pokemon_species WHERE season_id=? ORDER BY id", (sid,)):
            battle = (json.loads(row["battles_json"]) or {}).get(format_name) or {}
            position = battle.get("position")
            if not isinstance(position, int) or not 1 <= position <= 50:
                continue
            top = battle.get("top") or {}
            values = battle.get("values") or {}
            slim_battle = {
                "position": position,
                "top": {key: _slim_top_entry(top.get(key)) for key in (
                    "move", "held_item", "stat_alignment", "stat_points", "ability"
                )},
                "values": {
                    "move": (values.get("move") or [])[:4],
                    "held_item": (values.get("held_item") or [])[:3],
                    "ability": (values.get("ability") or [])[:2],
                },
            }
            catalog.append({
                "name": row["name"], "displayName": localize(row["name"], row["display_name"]),
                "slug": row["slug"], "sprite": sprites.get(row["sprite"], row["sprite"]),
                "types": json.loads(row["types_json"]), "battles": {format_name: slim_battle},
            })
        catalog.sort(key=lambda entry: entry["battles"][format_name]["position"])
        return {"meta": _season_meta(connection, season), "format": format_name, "catalog": catalog}


def load_calculator():
    with database() as connection:
        static = static_data(connection)
        season = _active_season(connection)
        if not season:
            return {"meta": None, "calculator": [], "items": static["implementedItems"]}
        sid = season["id"]
        sprites = _sprite_map(connection, sid)
        localize = _pokemon_localizer(connection)
        calculator = []
        for row in connection.execute("SELECT * FROM pokemon_forms WHERE season_id=? ORDER BY display_name", (sid,)):
            display_name = localize(row["name"], row["display_name"])
            base_display_name = localize(row["base_name"], row["base_display_name"])
            base_stats, level_50_stats = _row_stats(row)
            calculator.append({"name":row["name"],"baseName":row["base_name"],"displayName":display_name,"baseDisplayName":base_display_name,"searchText":" ".join((row["search_text"],display_name,base_display_name)).lower(),"slug":row["slug"],"sprite":sprites.get(row["sprite"], row["sprite"]),"types":json.loads(row["types_json"]),"baseStats":base_stats,"stats":level_50_stats,"abilities":json.loads(row["abilities_json"] or "[]"),"learnableMoves":json.loads(row["learnable_moves_json"])})
        calculator.sort(key=lambda entry: entry["displayName"])
        season_items = [row["item_name"] for row in connection.execute("SELECT item_name FROM season_items WHERE season_id=?", (sid,))]
        season_items.sort(key=lambda name: static["itemZh"].get(name, name))
        return {
            "meta": _season_meta(connection, season), "calculator": calculator,
            "items": season_items,
        }


REFERENCE_KINDS = {"pokemon", "item", "ability"}


def _visible_zh(source_name, translated_name):
    """Never expose an untranslated internal Battle Data name in reference UI."""
    translated_name = str(translated_name or "").strip()
    return translated_name if translated_name and translated_name != source_name else "中文名待补充"


def _reference_form_summary(row, sprites, localize):
    return {
        "slug": row["slug"],
        "name": _visible_zh(row["name"], localize(row["name"], row["display_name"])),
        "sprite": sprites.get(row["sprite"], row["sprite"]),
        "types": json.loads(row["types_json"]),
    }


def _reference_search_match(query, *names):
    if not query:
        return True
    haystack = " ".join(str(name or "") for name in names).casefold()
    return query.casefold() in haystack


def load_reference(kind, query="", limit=60, offset=0):
    if kind not in REFERENCE_KINDS:
        raise ValueError("资料分类必须是 pokemon、item 或 ability")
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    query = str(query or "").strip()[:80]
    with database() as connection:
        season = _active_season(connection)
        if not season:
            return {"meta": None, "kind": kind, "total": 0, "items": []}
        sid = season["id"]
        results = []
        if kind == "pokemon":
            sprites = _sprite_map(connection, sid)
            localize = _pokemon_localizer(connection)
            rows = connection.execute(
                "SELECT * FROM pokemon_forms WHERE season_id=? ORDER BY display_name,slug", (sid,)
            )
            for row in rows:
                display_name = localize(row["name"], row["display_name"])
                base_display_name = localize(row["base_name"], row["base_display_name"])
                if _reference_search_match(
                    query, row["name"], row["base_name"], row["slug"], row["search_text"],
                    display_name, base_display_name,
                ):
                    results.append(_reference_form_summary(row, sprites, localize))
        elif kind == "item":
            rows = connection.execute(
                """SELECT i.name,i.slug,i.name_zh FROM season_items si
                   JOIN items i ON i.name=si.item_name
                   WHERE si.season_id=? ORDER BY i.name_zh,i.name""", (sid,)
            )
            for row in rows:
                if _reference_search_match(query, row["name"], row["name_zh"], row["slug"]):
                    results.append({"slug": row["slug"], "name": _visible_zh(row["name"], row["name_zh"])})
        else:
            rows = connection.execute(
                "SELECT name,slug,name_zh FROM abilities WHERE implemented=1 ORDER BY name_zh,name"
            )
            for row in rows:
                if _reference_search_match(query, row["name"], row["name_zh"], row["slug"]):
                    results.append({"slug": row["slug"], "name": _visible_zh(row["name"], row["name_zh"])})
        total = len(results)
        return {
            "meta": _season_meta(connection, season), "kind": kind, "total": total,
            "items": results[offset:offset + limit],
        }


def _related_species(connection, season_id, source_kind, source_name, sprites, localize):
    related = []
    forms = list(connection.execute("SELECT * FROM pokemon_forms WHERE season_id=?", (season_id,)))
    by_slug = {row["slug"]: row for row in forms}
    by_name = {}
    for row in forms:
        by_name.setdefault(row["name"], row)
        by_name.setdefault(row["base_name"], row)
    if source_kind == "ability":
        for row in forms:
            if source_name in json.loads(row["abilities_json"] or "[]"):
                related.append({**_reference_form_summary(row, sprites, localize), "rank": None})
    else:
        for species in connection.execute("SELECT * FROM pokemon_species WHERE season_id=?", (season_id,)):
            best_rank = None
            used = False
            for battle in (json.loads(species["battles_json"]) or {}).values():
                if not battle:
                    continue
                held_items = list(((battle.get("values") or {}).get("held_item") or []))
                top_item = (((battle.get("top") or {}).get("held_item") or {}).get("name"))
                if top_item:
                    held_items.append(top_item)
                if source_name in held_items:
                    used = True
                    position = battle.get("position")
                    if isinstance(position, int):
                        best_rank = position if best_rank is None else min(best_rank, position)
            if not used:
                continue
            form = by_slug.get(species["slug"]) or by_name.get(species["name"])
            if form:
                related.append({**_reference_form_summary(form, sprites, localize), "rank": best_rank})
    unique = {}
    for entry in related:
        unique.setdefault(entry["slug"], entry)
    return sorted(unique.values(), key=lambda entry: (entry["rank"] is None, entry["rank"] or 9999, entry["name"]))


def load_reference_detail(kind, slug):
    if kind not in REFERENCE_KINDS:
        raise ValueError("资料分类必须是 pokemon、item 或 ability")
    slug = str(slug or "").strip()
    with database() as connection:
        season = _active_season(connection)
        if not season:
            return None
        sid = season["id"]
        sprites = _sprite_map(connection, sid)
        localize = _pokemon_localizer(connection)
        if kind == "pokemon":
            row = connection.execute(
                "SELECT * FROM pokemon_forms WHERE season_id=? AND slug=?", (sid, slug)
            ).fetchone()
            if not row:
                return None
            move_zh = translation_map(connection, "move")
            move_names = json.loads(row["learnable_moves_json"] or "[]")
            move_rows = {
                move["name"]: move for move in connection.execute(
                    "SELECT name,type_zh,category,power FROM season_moves WHERE season_id=?", (sid,)
                )
            }
            moves = []
            for name in move_names:
                move = move_rows.get(name)
                if not move:
                    continue
                moves.append({
                    "name": _visible_zh(name, move_zh.get(name)), "type": move["type_zh"],
                    "category": move["category"], "power": move["power"],
                })
            moves.sort(key=lambda move: (move["type"], move["name"]))
            ability_zh = translation_map(connection, "ability")
            abilities = [{
                "slug": _entity_slug(name), "name": _visible_zh(name, ability_zh.get(name)),
            } for name in json.loads(row["abilities_json"] or "[]")]
            forms = []
            for form in connection.execute(
                """SELECT * FROM pokemon_forms WHERE season_id=? AND base_name=?
                   ORDER BY display_name,slug""", (sid, row["base_name"])
            ):
                forms.append(_reference_form_summary(form, sprites, localize))
            base_stats, level_50_stats = _row_stats(row)
            detail = {
                **_reference_form_summary(row, sprites, localize),
                "baseName": _visible_zh(row["base_name"], localize(row["base_name"], row["base_display_name"])),
                "baseStats": base_stats, "stats": level_50_stats, "abilities": abilities,
                "moves": moves, "forms": forms,
            }
        elif kind == "item":
            row = connection.execute(
                """SELECT i.* FROM items i JOIN season_items si ON si.item_name=i.name
                   WHERE si.season_id=? AND i.slug=?""", (sid, slug)
            ).fetchone()
            if not row:
                return None
            detail = {
                "slug": row["slug"], "name": _visible_zh(row["name"], row["name_zh"]),
                "descriptionZh": row["description_zh"],
                "pokemon": _related_species(connection, sid, "item", row["name"], sprites, localize),
            }
        else:
            row = connection.execute(
                "SELECT * FROM abilities WHERE implemented=1 AND slug=?", (slug,)
            ).fetchone()
            if not row:
                return None
            detail = {
                "slug": row["slug"], "name": _visible_zh(row["name"], row["name_zh"]),
                "descriptionZh": row["description_zh"],
                "pokemon": _related_species(connection, sid, "ability", row["name"], sprites, localize),
            }
        return {"meta": _season_meta(connection, season), "kind": kind, "detail": detail}


def load_refresh_base():
    """Load the complete active snapshot for a source adapter to update transactionally."""
    with database() as connection:
        season = _active_season(connection)
        if not season:
            return None
        sid = season["id"]
        catalog = []
        for row in connection.execute("SELECT * FROM pokemon_species WHERE season_id=? ORDER BY id", (sid,)):
            base_stats, level_50_stats = _row_stats(row)
            catalog.append({
                "slug": row["slug"], "name": row["name"], "displayName": row["display_name"],
                "sprite": row["sprite"], "types": json.loads(row["types_json"]),
                "baseStats": base_stats, "stats": level_50_stats,
                "battles": json.loads(row["battles_json"]),
            })
        calculator = []
        for row in connection.execute("SELECT * FROM pokemon_forms WHERE season_id=? ORDER BY id", (sid,)):
            base_stats, level_50_stats = _row_stats(row)
            calculator.append({
                "slug": row["slug"], "name": row["name"], "baseName": row["base_name"],
                "displayName": row["display_name"], "baseDisplayName": row["base_display_name"],
                "searchText": row["search_text"], "sprite": row["sprite"],
                "types": json.loads(row["types_json"]), "baseStats": base_stats,
                "stats": level_50_stats,
                "abilities": json.loads(row["abilities_json"] or "[]"),
                "learnableMoves": json.loads(row["learnable_moves_json"]),
            })
        return {
            "meta": {
                "season": season["code"], "generatedAt": season["generated_at"],
                "dataVersion": season["data_version"], "source": season["source"],
            },
            "catalog": catalog,
            "calculator": calculator,
            "items": [row["item_name"] for row in connection.execute(
                "SELECT item_name FROM season_items WHERE season_id=? ORDER BY item_name", (sid,)
            )],
            "moves": _season_moves(connection, sid),
        }


def replace_api_aliases(aliases):
    with database() as connection:
        connection.execute("DELETE FROM translations WHERE kind='pokemon_api'")
        connection.executemany("INSERT INTO translations(kind,source_name,zh_name) VALUES('pokemon_api',?,?)", aliases.items())
        connection.commit()


def health_info():
    with database() as connection:
        season = _active_season(connection)
        sprite_counts = connection.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END) AS cached
               FROM sprite_assets WHERE season_id=?""",
            (season["id"],),
        ).fetchone() if season else {"total": 0, "cached": 0}
        return {
            "database": str(DB_PATH), "season": season["code"] if season else None,
            "dataVersion": season["data_version"] if season else None,
            "source": season["source"] if season else None,
            "species": connection.execute("SELECT COUNT(*) FROM pokemon_species").fetchone()[0],
            "forms": connection.execute("SELECT COUNT(*) FROM pokemon_forms").fetchone()[0],
            "sprites": {"cached": sprite_counts["cached"] or 0, "total": sprite_counts["total"]},
            "translationCoverage": _translation_coverage(connection, season),
            "movePoolCoverage": _move_pool_coverage(connection, season),
            "statCoverage": _stat_coverage(connection, season),
            "referenceCoverage": {
                "items": {
                    "total": connection.execute("SELECT COUNT(*) FROM items WHERE implemented=1").fetchone()[0],
                    "missingDescription": connection.execute(
                        "SELECT COUNT(*) FROM items WHERE implemented=1 AND TRIM(description_zh)=''"
                    ).fetchone()[0],
                },
                "abilities": {
                    "total": connection.execute("SELECT COUNT(*) FROM abilities WHERE implemented=1").fetchone()[0],
                    "missingDescription": connection.execute(
                        "SELECT COUNT(*) FROM abilities WHERE implemented=1 AND TRIM(description_zh)=''"
                    ).fetchone()[0],
                },
            },
        }


def get_sprite_asset(cache_key):
    with database() as connection:
        row = connection.execute(
            "SELECT * FROM sprite_assets WHERE cache_key=?", (cache_key,)
        ).fetchone()
        return dict(row) if row else None


def mark_sprite_cached(cache_key, mime_type, byte_size):
    with database() as connection:
        connection.execute(
            "UPDATE sprite_assets SET mime_type=?,byte_size=?,fetched_at=? WHERE cache_key=?",
            (mime_type, byte_size, datetime.now(timezone.utc).isoformat(), cache_key),
        )
        connection.commit()


def pending_sprite_assets():
    with database() as connection:
        season = _active_season(connection)
        if not season:
            return []
        priority_urls = set()
        for row in connection.execute(
            "SELECT sprite,battles_json FROM pokemon_species WHERE season_id=?", (season["id"],)
        ):
            battles = json.loads(row["battles_json"])
            if any(isinstance((battle or {}).get("position"), int) and (battle or {}).get("position") <= 50 for battle in battles.values()):
                priority_urls.add(row["sprite"])
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM sprite_assets WHERE season_id=? ORDER BY fetched_at IS NOT NULL,cache_key",
            (season["id"],),
        )]
        rows.sort(key=lambda row: (row["source_url"] not in priority_urls, row["fetched_at"] is not None))
        return rows
