"""Build the bundled Simplified Chinese translation snapshot from PokeAPI."""

import json
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "server" / "translations.zh-CN.json"
GRAPHQL_URL = "https://beta.pokeapi.co/graphql/v1beta"
QUERY = """
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


def request_payload():
    body = json.dumps({"query": QUERY}).encode()
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "ChampionGrid/0.7"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    return payload["data"]


def paired_names(rows, relation):
    grouped = {}
    for row in rows:
        entity = row.get(relation) or {}
        slug = entity.get("name")
        if slug:
            grouped.setdefault(slug, {})[row["language_id"]] = row["name"]
    return {
        names[9]: names[12]
        for names in grouped.values()
        if names.get(9) and names.get(12)
    }


def source_names(rows, relation, source_language, target_language):
    grouped = {}
    for row in rows:
        entity = row.get(relation) or {}
        slug = entity.get("name")
        if slug:
            grouped.setdefault(slug, {})[row["language_id"]] = row["name"]
    return {
        names[source_language]: names[target_language]
        for names in grouped.values()
        if names.get(source_language) and names.get(target_language)
    }


def main():
    data = request_payload()
    snapshot = {
        "pokemon_api": {
            row["pokemon_v2_pokemonspecy"]["name"]: row["name"]
            for row in data["pokemon_v2_pokemonspeciesname"]
            if row.get("pokemon_v2_pokemonspecy") and row.get("name")
        },
        "move": paired_names(data["pokemon_v2_movename"], "pokemon_v2_move"),
        "item": paired_names(data["pokemon_v2_itemname"], "pokemon_v2_item"),
        "ability": paired_names(data["pokemon_v2_abilityname"], "pokemon_v2_ability"),
        "move_ja": source_names(data["pokemon_v2_movename"], "pokemon_v2_move", 1, 9),
        "item_ja": source_names(data["pokemon_v2_itemname"], "pokemon_v2_item", 1, 9),
        "ability_ja": source_names(data["pokemon_v2_abilityname"], "pokemon_v2_ability", 1, 9),
        "nature_ja": source_names(data["pokemon_v2_naturename"], "pokemon_v2_nature", 1, 9),
    }
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_bytes(rendered.replace("\n", "\r\n").encode("utf-8"))
    print(", ".join(f"{kind}={len(values)}" for kind, values in snapshot.items()))


if __name__ == "__main__":
    main()
