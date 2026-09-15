PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS types (
  name_zh TEXT PRIMARY KEY,
  name_en TEXT,
  color TEXT NOT NULL,
  sort_order INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS type_effectiveness (
  attack_type TEXT NOT NULL REFERENCES types(name_zh),
  defense_type TEXT NOT NULL REFERENCES types(name_zh),
  multiplier REAL NOT NULL,
  PRIMARY KEY (attack_type, defense_type)
);

CREATE TABLE IF NOT EXISTS translations (
  kind TEXT NOT NULL,
  source_name TEXT NOT NULL,
  zh_name TEXT NOT NULL,
  PRIMARY KEY (kind, source_name)
);

CREATE TABLE IF NOT EXISTS moves (
  name TEXT PRIMARY KEY,
  name_zh TEXT NOT NULL,
  type_zh TEXT NOT NULL REFERENCES types(name_zh),
  category TEXT NOT NULL,
  power INTEGER NOT NULL CHECK(power > 0)
);

CREATE TABLE IF NOT EXISTS items (
  name TEXT PRIMARY KEY,
  slug TEXT UNIQUE,
  name_zh TEXT NOT NULL,
  description_zh TEXT NOT NULL DEFAULT '',
  implemented INTEGER NOT NULL DEFAULT 1 CHECK(implemented IN (0, 1))
);

CREATE TABLE IF NOT EXISTS abilities (
  name TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name_zh TEXT NOT NULL,
  description_zh TEXT NOT NULL DEFAULT '',
  implemented INTEGER NOT NULL DEFAULT 0 CHECK(implemented IN (0, 1))
);

CREATE TABLE IF NOT EXISTS natures (
  name TEXT PRIMARY KEY,
  name_zh TEXT NOT NULL,
  boost_stat TEXT,
  drop_stat TEXT
);

CREATE TABLE IF NOT EXISTS stat_defaults (
  side TEXT NOT NULL CHECK(side IN ('attacker', 'defender')),
  stat_key TEXT NOT NULL,
  label TEXT NOT NULL,
  points INTEGER NOT NULL CHECK(points BETWEEN 0 AND 32),
  sort_order INTEGER NOT NULL,
  PRIMARY KEY (side, stat_key)
);

CREATE TABLE IF NOT EXISTS seasons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  generated_at TEXT,
  data_version TEXT,
  source TEXT NOT NULL,
  refreshed_at TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS pokemon_species (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  sprite TEXT,
  types_json TEXT NOT NULL,
  base_stats_json TEXT NOT NULL,
  stats_json TEXT NOT NULL,
  battles_json TEXT NOT NULL,
  UNIQUE(season_id, slug)
);

CREATE TABLE IF NOT EXISTS pokemon_forms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  base_name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  base_display_name TEXT NOT NULL,
  search_text TEXT NOT NULL,
  sprite TEXT,
  types_json TEXT NOT NULL,
  base_stats_json TEXT NOT NULL,
  stats_json TEXT NOT NULL,
  abilities_json TEXT NOT NULL DEFAULT '[]',
  learnable_moves_json TEXT NOT NULL,
  UNIQUE(season_id, slug)
);

CREATE TABLE IF NOT EXISTS season_items (
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  item_name TEXT NOT NULL,
  PRIMARY KEY (season_id, item_name)
);

CREATE TABLE IF NOT EXISTS season_moves (
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  type_zh TEXT NOT NULL REFERENCES types(name_zh),
  category TEXT NOT NULL,
  power INTEGER,
  PRIMARY KEY (season_id, name)
);

CREATE TABLE IF NOT EXISTS source_snapshots (
  season_id INTEGER PRIMARY KEY REFERENCES seasons(id) ON DELETE CASCADE,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sprite_assets (
  cache_key TEXT PRIMARY KEY,
  season_id INTEGER NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  local_path TEXT NOT NULL,
  mime_type TEXT,
  byte_size INTEGER,
  fetched_at TEXT,
  UNIQUE(season_id, source_url)
);

CREATE INDEX IF NOT EXISTS idx_species_season ON pokemon_species(season_id);
CREATE INDEX IF NOT EXISTS idx_forms_season ON pokemon_forms(season_id);
CREATE INDEX IF NOT EXISTS idx_moves_season ON season_moves(season_id);
CREATE INDEX IF NOT EXISTS idx_sprite_assets_season ON sprite_assets(season_id);
CREATE INDEX IF NOT EXISTS idx_abilities_slug ON abilities(slug);
PRAGMA user_version = 6;
