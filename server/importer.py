import json
import os
import re
import time
import urllib.error
import urllib.request

from db import database, save_snapshot, static_data, translation_map

BATTLE_DATA_URL = os.environ.get("BATTLE_DATA_URL", "https://championsbattledata.com/api")
POKEAPI_URL = os.environ.get("POKEAPI_URL", "https://beta.pokeapi.co/graphql/v1beta")
ASSET_ORIGIN = "https://championsbattledata.com"


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


def fetch_aliases():
    query = "query ChampionGridNames { pokemon_v2_pokemonspeciesname(where: {language_id: {_eq: 12}}) { name pokemon_v2_pokemonspecy { name } } }"
    payload = _request_json(POKEAPI_URL, payload={"query": query})
    return {
        row["pokemon_v2_pokemonspecy"]["name"]: row["name"]
        for row in payload.get("data", {}).get("pokemon_v2_pokemonspeciesname", [])
        if row.get("pokemon_v2_pokemonspecy") and row.get("name")
    }


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
    return {"position": battle.get("position"), "top": battle.get("top") or {}, "values": battle.get("values") or {}}


def _number(value):
    try:
        return int(value) or 1
    except (TypeError, ValueError):
        return 1


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
                observed_items.update(battle["values"].get("held_item") or [])
        forms = summary.get("forms") or [primary]
        for form in forms:
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
                "types": [static["typeZh"].get(value, value) for value in (form.get("types") or summary.get("types") or [])],
                "stats": {"hp":_number(form.get("hp")),"atk":_number(form.get("attack")),"def":_number(form.get("defense")),"spa":_number(form.get("sp_attack")),"spd":_number(form.get("sp_defense")),"spe":_number(form.get("speed"))},
                "learnableMoves": pokemon.get("learnableMoveNames") or [],
            })
        catalog.append({
            "name": pokemon.get("name"), "displayName": zh_name(pokemon.get("name")),
            "slug": pokemon.get("slug"), "sprite": asset_url(summary.get("sprite") or primary.get("image_path")),
            "types": [static["typeZh"].get(value, value) for value in (summary.get("types") or primary.get("types") or [])],
            "battles": battles,
            "stats": {"hp":_number(primary.get("hp")),"atk":_number(primary.get("attack")),"def":_number(primary.get("defense")),"spa":_number(primary.get("sp_attack")),"spd":_number(primary.get("sp_defense")),"spe":_number(primary.get("speed"))},
        })
    item_names = sorted(set(static["implementedItems"]) | observed_items, key=lambda value: static["itemZh"].get(value, value))
    return {
        "meta": {"generatedAt":raw.get("generatedAt"),"dataVersion":raw.get("dataVersion"),"season":season,"source":"Champions Battle Data"},
        "aliases": aliases, "catalog": catalog,
        "calculator": sorted(calculator, key=lambda value: value["displayName"]), "items": item_names,
    }


def refresh():
    raw = _request_json(BATTLE_DATA_URL)
    with database() as connection:
        static = static_data(connection)
        existing_aliases = translation_map(connection, "pokemon_api")
    try:
        aliases = fetch_aliases()
    except Exception:
        aliases = existing_aliases
    store = normalize(raw, aliases, static)
    if not store["catalog"] or not store["calculator"]:
        raise ValueError("Battle Data 返回的数据为空")
    save_snapshot(store, raw, aliases)
    return store["meta"]
