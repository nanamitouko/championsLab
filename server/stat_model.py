STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")
SOURCE_STAT_KEYS = {
    "hp": "hp",
    "atk": "attack",
    "def": "defense",
    "spa": "sp_attack",
    "spd": "sp_defense",
    "spe": "speed",
}


def _integer_stats(stats, label):
    values = {}
    for key in STAT_KEYS:
        value = stats.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError(f"{label}的 {key} 数值无效：{value!r}")
        values[key] = int(value)
    return values


def level_50_neutral_from_base(base_stats):
    """Champions: level 50, perfect IVs, zero Stat Points, neutral nature."""
    base = _integer_stats(base_stats, "种族值")
    if any(value < 1 or value > 255 for value in base.values()):
        raise ValueError(f"种族值超出 1–255：{base}")
    return {
        "hp": 1 if base["hp"] == 1 else base["hp"] + 75,
        **{key: base[key] + 20 for key in STAT_KEYS if key != "hp"},
    }


def base_from_level_50_neutral(level_50_stats):
    """Reverse Battle Data's level-50 neutral values into canonical base stats."""
    stats = _integer_stats(level_50_stats, "50级基准值")
    base = {
        "hp": 1 if stats["hp"] == 1 else stats["hp"] - 75,
        **{key: stats[key] - 20 for key in STAT_KEYS if key != "hp"},
    }
    # Recompute as a round-trip guard so malformed or differently-scaled source
    # data cannot silently enter the calculator.
    if level_50_neutral_from_base(base) != stats:
        raise ValueError(f"50级基准值无法还原为合法种族值：{stats}")
    return base


def source_level_50_stats(form):
    values = {}
    for key, source_key in SOURCE_STAT_KEYS.items():
        raw = form.get(source_key)
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Battle Data 缺少合法的 {source_key}：{raw!r}") from None
        if isinstance(raw, bool) or not numeric.is_integer():
            raise ValueError(f"Battle Data 的 {source_key} 不是整数：{raw!r}")
        values[key] = int(numeric)
    return values


def audit_stat_pair(base_stats, level_50_stats):
    try:
        expected = level_50_neutral_from_base(base_stats)
        actual = _integer_stats(level_50_stats, "50级基准值")
    except (AttributeError, ValueError):
        return False
    return expected == actual
