"""Build offline Chinese reference descriptions and Champions form abilities."""

import json
import re
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "server" / "reference.zh-CN.json"
OVERRIDES = ROOT / "server" / "translation_overrides.zh-CN.json"
GRAPHQL_URL = "https://beta.pokeapi.co/graphql/v1beta"
BATTLE_DATA_URL = "https://championsbattledata.com/api"
QUERY = """
query ChampionGridReferenceZh {
  pokemon_v2_itemname(where: {language_id: {_eq: 9}}) {
    name pokemon_v2_item { name }
  }
  pokemon_v2_abilityname(where: {language_id: {_eq: 9}}) {
    name pokemon_v2_ability { name }
  }
  pokemon_v2_itemflavortext(where: {language_id: {_eq: 12}}) {
    flavor_text version_group_id pokemon_v2_item { name }
  }
  pokemon_v2_abilityflavortext(where: {language_id: {_eq: 12}}) {
    flavor_text version_group_id pokemon_v2_ability { name }
  }
}
"""


def request_json(url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ChampionGrid/0.8)", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def newest_descriptions(rows, relation):
    newest = {}
    for row in rows:
        entity = row.get(relation) or {}
        slug = entity.get("name")
        text = re.sub(r"\s+", " ", row.get("flavor_text") or "").strip()
        if slug and text and row.get("version_group_id", 0) >= newest.get(slug, (0, ""))[0]:
            newest[slug] = (row["version_group_id"], text)
    return {slug: value[1] for slug, value in newest.items()}


def english_names(rows, relation):
    return {
        row[relation]["name"]: row["name"].replace("’", "'")
        for row in rows
        if row.get(relation) and row.get("name")
    }


def main():
    payload = request_json(GRAPHQL_URL, {"query": QUERY})
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    data = payload["data"]
    item_names = english_names(data["pokemon_v2_itemname"], "pokemon_v2_item")
    ability_names = english_names(data["pokemon_v2_abilityname"], "pokemon_v2_ability")
    item_flavors = newest_descriptions(data["pokemon_v2_itemflavortext"], "pokemon_v2_item")
    ability_flavors = newest_descriptions(data["pokemon_v2_abilityflavortext"], "pokemon_v2_ability")
    descriptions = {
        "item": {item_names[slug]: text for slug, text in item_flavors.items() if slug in item_names},
        "ability": {ability_names[slug]: text for slug, text in ability_flavors.items() if slug in ability_names},
    }
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    for item in overrides.get("item", {}):
        if re.search(r"(?:ite|nite)(?: [XYZ])?$", item, re.I):
            descriptions["item"].setdefault(item, "携带后可让对应的宝可梦在对战中进行超级进化。")

    battle_data = request_json(BATTLE_DATA_URL)
    form_abilities = {}
    for pokemon in battle_data.get("pokemon", []):
        summary = pokemon.get("summary") or {}
        for form in summary.get("forms") or [summary.get("primary") or {}]:
            title = form.get("title") or form.get("form_name")
            if not title:
                continue
            slug = form.get("slug") or f"{pokemon.get('slug')}-{title}"
            abilities = [value.strip() for value in re.split(r"[|,]", form.get("abilities") or "") if value.strip()]
            form_abilities[slug] = abilities

    snapshot = {
        "generatedAt": battle_data.get("generatedAt"),
        "descriptions": descriptions,
        "formAbilities": form_abilities,
    }
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_bytes(rendered.replace("\n", "\r\n").encode("utf-8"))
    print(
        f"items={len(descriptions['item'])}, abilities={len(descriptions['ability'])}, "
        f"forms={len(form_abilities)}"
    )


if __name__ == "__main__":
    main()
