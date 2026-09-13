import json
import os
import re
import time
import urllib.error
import urllib.request

from db import database, save_snapshot, static_data, translation_map, translation_overrides

BATTLE_DATA_URL = os.environ.get("BATTLE_DATA_URL", "https://championsbattledata.com/api")
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
  pokemon_v2_movename(where: {language_id: {_in: [9, 12]}}) {
    language_id name pokemon_v2_move { name }
  }
  pokemon_v2_itemname(where: {language_id: {_in: [9, 12]}}) {
    language_id name pokemon_v2_item { name }
  }
  pokemon_v2_abilityname(where: {language_id: {_in: [9, 12]}}) {
    language_id name pokemon_v2_ability { name }
  }
}
"""


def _request_json(url, *, payload=None, timeout=45):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "ChampionGrid/0.4", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    last_error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < 2:
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


def refresh():
    raw = _request_json(BATTLE_DATA_URL)
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
