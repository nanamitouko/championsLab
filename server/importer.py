import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from html import unescape

from db import database, load_refresh_base, save_snapshot, static_data, translation_map, translation_overrides

BATTLE_DATA_URL = os.environ.get("BATTLE_DATA_URL", "https://championsbattledata.com/api")
POKECHAM_BASE_URL = os.environ.get("POKECHAM_BASE_URL", "https://pokechamdb.com").rstrip("/")
POKEAPI_URL = os.environ.get("POKEAPI_URL", "https://beta.pokeapi.co/graphql/v1beta")
ASSET_ORIGIN = "https://championsbattledata.com"
MOVE_DATA_URL = os.environ.get(
    "MOVE_DATA_URL",
    "https://raw.githubusercontent.com/otterlyclueless/pokemon-champions-data/main/moves/moves.json",
)
TRANSLATION_QUERY = """
query ChampionGridZhNames {
  pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 12}}) {
    name pokemon_v2_pokemonspecy { name }
  }
  pokemon_v2_movename(where: {language_id: {_in: [1, 9, 12]}}) {
    language_id name pokemon_v2_move { name }
  }
  pokemon_v2_itemname(where: {language_id: {_in: [1, 9, 12]}}) {
    language_id name pokemon_v2_item { name }
  }
  pokemon_v2_abilityname(where: {language_id: {_in: [1, 9, 12]}}) {
    language_id name pokemon_v2_ability { name }
  }
  pokemon_v2_naturename(where: {language_id: {_in: [1, 9]}}) {
    language_id name pokemon_v2_nature { name }
  }
}
"""


def _request_json(url, *, payload=None, timeout=45, attempts=3):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChampionGrid/0.6)", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(attempt + 1)
    raise last_error


def _request_text(url, *, timeout=20, attempts=2):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ChampionGrid/0.6)", "Accept": "text/html,*/*"},
    )
    last_error = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeError) as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(attempt + 1)
    raise last_error


def _paired_names(rows, relation):
    grouped = {}
    for row in rows:
        entity = row.get(relation) or {}
        if entity.get("name"):
            grouped.setdefault(entity["name"], {})[row["language_id"]] = row["name"]
    return {
        names[9]: names[12]
        for names in grouped.values()
        if names.get(9) and names.get(12)
    }


def _source_names(rows, relation, source_language, target_language):
    grouped = {}
    for row in rows:
        entity = row.get(relation) or {}
        if entity.get("name"):
            grouped.setdefault(entity["name"], {})[row["language_id"]] = row["name"]
    return {
        names[source_language]: names[target_language]
        for names in grouped.values()
        if names.get(source_language) and names.get(target_language)
    }


def fetch_translation_groups():
    data = _request_json(POKEAPI_URL, payload={"query": TRANSLATION_QUERY}).get("data", {})
    return {
        "pokemon_api": {
            row["pokemon_v2_pokemonspecy"]["name"]: row["name"]
            for row in data.get("pokemon_v2_pokemonspeciesname", [])
            if row.get("pokemon_v2_pokemonspecy") and row.get("name")
        },
        "move": _paired_names(data.get("pokemon_v2_movename", []), "pokemon_v2_move"),
        "item": _paired_names(data.get("pokemon_v2_itemname", []), "pokemon_v2_item"),
        "ability": _paired_names(data.get("pokemon_v2_abilityname", []), "pokemon_v2_ability"),
        "move_ja": _source_names(data.get("pokemon_v2_movename", []), "pokemon_v2_move", 1, 9),
        "item_ja": _source_names(data.get("pokemon_v2_itemname", []), "pokemon_v2_item", 1, 9),
        "ability_ja": _source_names(data.get("pokemon_v2_abilityname", []), "pokemon_v2_ability", 1, 9),
        "nature_ja": _source_names(data.get("pokemon_v2_naturename", []), "pokemon_v2_nature", 1, 9),
    }


def fetch_move_metadata():
    rows = _request_json(MOVE_DATA_URL)
    if not isinstance(rows, list) or not rows:
        raise ValueError("Champions 招式元数据返回的数据为空")
    return rows


def _move_key(name):
    return str(name or "").replace("’", "'").strip().casefold()


def build_move_catalog(calculator, rows, static):
    needed = sorted({
        name
        for pokemon in calculator
        for name in pokemon.get("learnableMoves", [])
        if name
    })
    metadata = {_move_key(row.get("name")): row for row in rows if row.get("name")}
    category_names = {"Physical": "物理", "Special": "特殊", "Status": "变化"}
    catalog, missing = [], []
    for name in needed:
        row = metadata.get(_move_key(name))
        if not row:
            missing.append(name)
            continue
        category = category_names.get(row.get("category"))
        type_zh = static["typeZh"].get(row.get("type"))
        if not category or not type_zh:
            missing.append(name)
            continue
        try:
            power = int(row["power"]) if row.get("power") is not None else None
        except (TypeError, ValueError):
            power = None
        if power is not None and power <= 0:
            power = None
        catalog.append({"name": name, "type": type_zh, "category": category, "power": power})
    if missing:
        sample = "、".join(missing[:8])
        suffix = "……" if len(missing) > 8 else ""
        raise ValueError(f"Champions 招式元数据缺少 {len(missing)} 项：{sample}{suffix}")
    return catalog


def species_key(name):
    value = str(name or "").lower()
    value = re.sub(r"^mega\s+", "", value)
    value = re.sub(r"^(alolan|hisuian|galarian|paldean)\s+", "", value)
    value = re.sub(r"\s*\[.*$", "", value)
    value = re.sub(r"\s+(male|female)$", "", value)
    value = re.sub(r"\s+z$", "", value)
    value = re.sub(r"[.'’]", "", value)
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", value))


def asset_url(path):
    if not path:
        return ""
    return path if str(path).startswith(("http://", "https://")) else f"{ASSET_ORIGIN}/{str(path).lstrip('/')}"


def compact_battle(battle):
    if not battle:
        return None
    top = dict(battle.get("top") or {})
    values = dict(battle.get("values") or {})
    values["held_item"] = [name for name in (values.get("held_item") or []) if valid_item_name(name)]
    if not valid_item_name((top.get("held_item") or {}).get("name")):
        top["held_item"] = None
    return {"position": battle.get("position"), "top": top, "values": values}


def _number(value):
    try:
        return int(value) or 1
    except (TypeError, ValueError):
        return 1


def valid_item_name(value):
    return bool(value) and not re.fullmatch(r"Unknown Item \d+", str(value), re.I)


def normalize(raw, aliases, static):
    local_names = static["nameZh"]

    def zh_name(name):
        return local_names.get(name) or aliases.get(species_key(name)) or name

    def form_name(title, base_name):
        if title in local_names:
            return local_names[title]
        base_zh = zh_name(base_name)
        if title == base_name:
            return base_zh
        if title.startswith("Mega "):
            clean = title[5:]
            suffix = clean[len(base_name):].strip() if clean.lower().startswith(base_name.lower()) else ""
            return f"超级{base_zh}{suffix}"
        match = re.search(r"\[(Hisuian|Alolan|Galarian|Paldean)[^]]*]", title, re.I)
        if match:
            labels = {"hisuian":"洗翠", "alolan":"阿罗拉", "galarian":"伽勒尔", "paldean":"帕底亚"}
            return f"{base_zh}（{labels[match.group(1).lower()]}形态）"
        return f"{base_zh} · {title}" if base_zh != base_name else title

    folder = (raw.get("dailyDataFolders") or raw.get("battleDataFolders") or [raw.get("defaultSeason") or "未知"])[0]
    season = str(folder).split("/")[0]
    catalog, calculator, observed_items, seen_forms = [], [], set(), set()
    for pokemon in raw.get("pokemon", []):
        summary = pokemon.get("summary") or {}
        primary = summary.get("primary") or {}
        current = (summary.get("battleSummary") or {}).get("Current") or {}
        battles = {"Doubles": compact_battle(current.get("Doubles")), "Singles": compact_battle(current.get("Singles"))}
        for battle in battles.values():
            if battle:
                observed_items.update(name for name in (battle["values"].get("held_item") or []) if valid_item_name(name))
        forms = summary.get("forms") or [primary]
        added_form = False
        for form in forms:
            form_types = form.get("types") or summary.get("types") or []
            if not form or not (form.get("title") or form.get("form_name")) or not form_types:
                continue
            key = form.get("slug") or f"{pokemon.get('slug')}-{form.get('title')}"
            if key in seen_forms:
                continue
            seen_forms.add(key)
            name = form.get("title") or form.get("form_name") or pokemon.get("name")
            display_name = form_name(name, pokemon.get("name"))
            base_display_name = zh_name(pokemon.get("name"))
            search_text = " ".join(str(x) for x in (
                name, pokemon.get("name"), pokemon.get("slug"), pokemon.get("showdownName"),
                pokemon.get("showdownId"), display_name, base_display_name,
                form.get("form_name"), form.get("saved_name")
            ) if x).lower()
            calculator.append({
                "name": name, "baseName": pokemon.get("name"), "displayName": display_name,
                "baseDisplayName": base_display_name, "searchText": search_text, "slug": key,
                "sprite": asset_url(form.get("image_path") or summary.get("sprite")),
                "types": [static["typeZh"].get(value, value) for value in form_types],
                "stats": {"hp":_number(form.get("hp")),"atk":_number(form.get("attack")),"def":_number(form.get("defense")),"spa":_number(form.get("sp_attack")),"spd":_number(form.get("sp_defense")),"spe":_number(form.get("speed"))},
                "learnableMoves": pokemon.get("learnableMoveNames") or [],
            })
            added_form = True
        if not added_form and not any(battles.values()):
            continue
        catalog.append({
            "name": pokemon.get("name"), "displayName": zh_name(pokemon.get("name")),
            "slug": pokemon.get("slug"), "sprite": asset_url(summary.get("sprite") or primary.get("image_path")),
            "types": [static["typeZh"].get(value, value) for value in (summary.get("types") or primary.get("types") or [])],
            "battles": battles,
            "stats": {"hp":_number(primary.get("hp")),"atk":_number(primary.get("attack")),"def":_number(primary.get("defense")),"spa":_number(primary.get("sp_attack")),"spd":_number(primary.get("sp_defense")),"spe":_number(primary.get("speed"))},
        })
    item_names = sorted({name for name in set(static["implementedItems"]) | observed_items if valid_item_name(name)}, key=lambda value: static["itemZh"].get(value, value))
    return {
        "meta": {"generatedAt":raw.get("generatedAt"),"dataVersion":raw.get("dataVersion"),"season":season,"source":"Champions Battle Data"},
        "aliases": aliases, "catalog": catalog,
        "calculator": sorted(calculator, key=lambda value: value["displayName"]), "items": item_names,
    }


POKECHAM_SECTIONS = {
    "moves": "MOVES", "items": "ITEMS", "abilities": "ABILITY",
    "natures": "NATURE", "partners": "PARTNER",
}

POKECHAM_SLUG_ALIASES = {
    "aegislash": "aegislash-shield-forme",
    "basculegion": "basculegion-male",
    "floette-eternal": "floette-form-5",
    "indeedee": "indeedee-male",
    "lycanroc": "lycanroc-midday",
    "meowstic": "meowstic-male",
    "squawkabilly": "squawkabilly-green-plumage",
    "tauros-paldea-combat": "paldean-tauros",
    "toxtricity": "toxtricity-amped-form",
}


def _pokecham_season(home_html):
    matches = re.findall(
        r'latestSeasonByFormat\\?"\s*:\s*\{\\?"(?:single|double)\\?"\s*:\s*\\?"(M-\d+)\\?"',
        home_html,
    )
    if not matches:
        raise ValueError("PokéChamp DB 页面中未找到当前赛季")
    return matches[0]


def _pokecham_section_names(page_html, section):
    label = POKECHAM_SECTIONS[section]
    start = re.search(rf">{re.escape(label)}</span>", page_html)
    if not start:
        return []
    following_headers = [
        match.start() for next_label in POKECHAM_SECTIONS.values()
        for match in [re.search(rf">{re.escape(next_label)}</span>", page_html[start.end():])]
        if match
    ]
    end = start.end() + min(following_headers) if following_headers else len(page_html)
    segment = page_html[start.end():end]
    names = []
    for item in re.findall(r"<li\b[^>]*>(.*?)</li>", segment, re.I | re.S):
        match = re.search(
            r'<span class="min-w-0 flex-1 truncate[^"]*">(.*?)</span>', item, re.I | re.S
        )
        if match:
            name = unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
            if name:
                names.append(name)
    return names


def _percentage(value):
    try:
        number = float(value)
        return f"{number:g}%"
    except (TypeError, ValueError):
        return ""


def _pokecham_name_maps(payload, page_html, season):
    variants = payload.get("variants") or {}
    reference = variants.get(f"{season}:single")
    if not reference:
        reference = next((value for key, value in variants.items() if key.startswith(f"{season}:")), None)
    if not reference:
        raise ValueError(f"PokéChamp DB 缺少 {payload.get('slug')} 的 {season} 配置")
    maps = {}
    for key in POKECHAM_SECTIONS:
        rows = reference.get(key) or []
        names = _pokecham_section_names(page_html, key)
        if rows and len(names) < len(rows):
            raise ValueError(
                f"PokéChamp DB 的 {payload.get('slug')} {key} 本地化列表不完整（{len(names)}/{len(rows)}）"
            )
        maps[key] = {
            row.get("name"): names[index]
            for index, row in enumerate(rows)
            if row.get("name") and index < len(names)
        }
    return maps


def _pokecham_named_rows(snapshot, name_maps, key):
    rows = snapshot.get(key) or []
    localized = name_maps.get(key) or {}
    missing = [row.get("name") for row in rows if not localized.get(row.get("name"))]
    if missing:
        raise ValueError(
            f"PokéChamp DB 的 {snapshot.get('pokemonSlug')} 有 {len(missing)} 个 {key} 名称无法转换"
        )
    return [
        {**row, "sourceName": localized[row.get("name")]}
        for row in rows
    ]


def _pokecham_top_entry(row):
    if not row:
        return None
    result = {"name": row.get("sourceName"), "percentage": _percentage(row.get("percentage"))}
    if row.get("percentage") is not None:
        result["percentage_value"] = row["percentage"]
    return result


def _pokecham_battle(snapshot, name_maps):
    moves = _pokecham_named_rows(snapshot, name_maps, "moves")
    items = _pokecham_named_rows(snapshot, name_maps, "items")
    abilities = _pokecham_named_rows(snapshot, name_maps, "abilities")
    natures = _pokecham_named_rows(snapshot, name_maps, "natures")
    evs = snapshot.get("evs") or []
    top_spread = None
    if evs:
        row = evs[0]
        top_spread = {
            "percentage": _percentage(row.get("percentage")),
            "percentage_value": row.get("percentage"),
            "hp_points": row.get("hp", 0), "attack_points": row.get("atk", 0),
            "defense_points": row.get("def", 0), "sp_atk_points": row.get("spAtk", 0),
            "sp_def_points": row.get("spDef", 0), "speed_points": row.get("speed", 0),
        }
    return {
        "position": snapshot.get("rank"),
        "top": {
            "move": _pokecham_top_entry(moves[0] if moves else None),
            "held_item": _pokecham_top_entry(items[0] if items else None),
            "ability": _pokecham_top_entry(abilities[0] if abilities else None),
            "stat_alignment": _pokecham_top_entry(natures[0] if natures else None),
            "stat_points": top_spread,
        },
        "values": {
            "move": [row["sourceName"] for row in moves],
            "held_item": [row["sourceName"] for row in items if valid_item_name(row["sourceName"])],
            "ability": [row["sourceName"] for row in abilities],
        },
    }


def _pokecham_catalog_slug(source_slug, available):
    candidates = [source_slug, POKECHAM_SLUG_ALIASES.get(source_slug)]
    regional = re.fullmatch(r"(.+)-(alola|hisui|galar)", source_slug)
    if regional:
        adjective = {"alola": "alolan", "hisui": "hisuian", "galar": "galarian"}[regional.group(2)]
        candidates.append(f"{adjective}-{regional.group(1)}")
    for candidate in candidates:
        if candidate and candidate in available:
            return candidate
    return None


def _fetch_pokecham_pokemon(source_slug, season, formats):
    quoted_slug = urllib.parse.quote(source_slug, safe="-")
    payload = _request_json(
        f"{POKECHAM_BASE_URL}/snapshots/pokemon/{quoted_slug}.json", timeout=20, attempts=2
    )
    for format_name in formats:
        source_format = format_name.lower().removesuffix("s")
        snapshot = (payload.get("variants") or {}).get(f"{season}:{source_format}")
        if not snapshot:
            raise ValueError(f"PokéChamp DB 缺少 {source_slug} 的 {season} {source_format} 配置")
    return source_slug, payload


def refresh_from_pokecham():
    base_store = load_refresh_base()
    if not base_store:
        raise RuntimeError("备用镜像需要本地已有一次完整 Battle Data 快照作为图鉴基础")
    home_html = _request_text(f"{POKECHAM_BASE_URL}/en")
    season = _pokecham_season(home_html)
    rankings = {}
    wanted_formats = {}
    generated_times = []
    for source_format, format_name in (("double", "Doubles"), ("single", "Singles")):
        ranking = _request_json(
            f"{POKECHAM_BASE_URL}/snapshots/rankings/{season}/{source_format}.json",
            timeout=20, attempts=2,
        )
        entries = (ranking.get("entries") or [])[:50]
        if len(entries) < 50:
            raise ValueError(f"PokéChamp DB 的 {source_format} 排名不足 50 条")
        rankings[format_name] = ranking
        if ranking.get("updatedAt"):
            generated_times.append(ranking["updatedAt"])
        for entry in entries:
            wanted_formats.setdefault(entry["pokemonSlug"], set()).add(format_name)

    snapshots, source_payloads = {}, {}
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="pokecham") as executor:
        futures = [
            executor.submit(_fetch_pokecham_pokemon, slug, season, formats)
            for slug, formats in wanted_formats.items()
        ]
        for future in as_completed(futures):
            slug, payload = future.result()
            snapshots[slug] = payload
            source_payloads[slug] = payload

    with database() as connection:
        combined_name_maps = {
            "moves": translation_map(connection, "move_ja"),
            "items": translation_map(connection, "item_ja"),
            "abilities": translation_map(connection, "ability_ja"),
            "natures": translation_map(connection, "nature_ja"),
            "partners": {},
        }

    store = deepcopy(base_store)
    by_slug = {entry["slug"]: entry for entry in store["catalog"]}
    for entry in store["catalog"]:
        entry["battles"] = {"Doubles": None, "Singles": None}
    missing = []
    observed_items = set(store["items"])
    for format_name, ranking in rankings.items():
        for source_entry in ranking["entries"][:50]:
            source_slug = source_entry["pokemonSlug"]
            target_slug = _pokecham_catalog_slug(source_slug, by_slug)
            if not target_slug:
                missing.append(source_slug)
                continue
            source_format = format_name.lower().removesuffix("s")
            snapshot = (snapshots[source_slug].get("variants") or {}).get(f"{season}:{source_format}")
            battle = _pokecham_battle(snapshot, combined_name_maps)
            battle["position"] = source_entry["rank"]
            by_slug[target_slug]["battles"][format_name] = battle
            observed_items.update(battle["values"]["held_item"])
    if missing:
        sample = "、".join(sorted(set(missing))[:8])
        raise ValueError(f"备用镜像有 {len(set(missing))} 只宝可梦无法映射到本地图鉴：{sample}")

    generated_at = max(generated_times) if generated_times else None
    version_time = re.sub(r"[^0-9]", "", generated_at or "")
    store["meta"] = {
        "season": season, "generatedAt": generated_at,
        "dataVersion": f"pokecham-{season}-{version_time or int(time.time())}",
        "source": "PokéChamp DB（备用镜像）",
    }
    store["items"] = sorted(name for name in observed_items if valid_item_name(name))
    save_snapshot(
        store,
        {"provider": "pokechamdb.com", "rankings": rankings, "pokemon": source_payloads},
    )
    return store["meta"]


def refresh_from_battle_data():
    raw = _request_json(BATTLE_DATA_URL, timeout=12, attempts=2)
    move_rows = fetch_move_metadata()
    overrides = translation_overrides()
    with database() as connection:
        static = static_data(connection)
        cached = {
            kind: translation_map(connection, kind)
            for kind in ("pokemon", "pokemon_api", "move", "item", "ability")
        }
    try:
        remote = fetch_translation_groups()
    except Exception:
        remote = {}
    merged = {}
    for kind in cached.keys() | remote.keys() | overrides.keys():
        merged[kind] = {**cached.get(kind, {}), **remote.get(kind, {}), **overrides.get(kind, {})}
    aliases = merged.get("pokemon_api", {})
    static["nameZh"] = merged.get("pokemon", static["nameZh"])
    static["moveZh"] = merged.get("move", static["moveZh"])
    static["itemZh"] = merged.get("item", static["itemZh"])
    static["abilityZh"] = merged.get("ability", static["abilityZh"])
    store = normalize(raw, aliases, static)
    if not store["catalog"] or not store["calculator"]:
        raise ValueError("Battle Data 返回的数据为空")
    store["moves"] = build_move_catalog(store["calculator"], move_rows, static)
    save_snapshot(store, raw, aliases, merged)
    return store["meta"]


def refresh():
    try:
        return refresh_from_battle_data()
    except Exception as primary_error:
        try:
            return refresh_from_pokecham()
        except Exception as fallback_error:
            raise RuntimeError(
                f"主数据源更新失败：{primary_error}；备用镜像更新失败：{fallback_error}"
            ) from fallback_error
