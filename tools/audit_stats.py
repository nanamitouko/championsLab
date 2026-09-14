import argparse
import json
import sqlite3
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
sys.path.insert(0, str(SERVER_DIR))

from stat_model import audit_stat_pair  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="审计 SQLite 中的 Champions 六维数据")
    parser.add_argument("database", nargs="?", default="data/champions.sqlite3")
    parser.add_argument("--name", help="只打印名称中包含该关键字的形态")
    args = parser.parse_args()

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    rows = list(connection.execute(
        """SELECT name,display_name,slug,base_stats_json,stats_json
           FROM pokemon_forms
           WHERE season_id=(SELECT id FROM seasons WHERE is_active=1 ORDER BY id DESC LIMIT 1)
           ORDER BY display_name,slug"""
    ))
    invalid = []
    selected = []
    query = (args.name or "").casefold()
    for row in rows:
        try:
            base = json.loads(row["base_stats_json"] or "null")
            level_50 = json.loads(row["stats_json"])
        except json.JSONDecodeError:
            base, level_50 = None, None
        entry = {
            "name": row["display_name"], "slug": row["slug"],
            "baseStats": base, "level50NeutralStats": level_50,
        }
        if not audit_stat_pair(base, level_50):
            invalid.append(entry)
        if query and query in f"{row['name']} {row['display_name']} {row['slug']}".casefold():
            selected.append(entry)

    print(json.dumps({
        "total": len(rows), "valid": len(rows) - len(invalid), "invalid": invalid,
        "selected": selected,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(1 if invalid else 0)


if __name__ == "__main__":
    main()
