from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, get_type_hints, ClassVar


def _coerce_value(value: Any, target_type: Any, default: Any) -> Any:
    if target_type is bool:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
        return bool(value)
    if target_type is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if target_type is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


@dataclass
class AppConfig:
    daily_new_limit: int = 50
    daily_review_limit: int = 150
    similarity_candidates: int = 25
    default_words_file: str = "data/words.csv"
    session_storage_path: str = "data/progress.json"
    archive_dir: str = "data"
    extra_batch_ratio: float = 0.1
    settlement_auto_advance_ms: int = 500
    random_new_selection: bool = True

    # Human-readable descriptions for each config field (not a dataclass field)
    DESCRIPTIONS: ClassVar[dict[str, str] | None] = None

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            config = cls()
            config.save(path)
            return config

        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config = cls()
            config.save(path)
            return config

        default_instance = cls()
        defaults = asdict(default_instance)
        field_map = get_type_hints(cls)
        for key, value in content.items():
            if key in defaults:
                target_type = field_map.get(key, type(defaults[key]))
                defaults[key] = _coerce_value(value, target_type, defaults[key])
        config = cls(**defaults)
        config.save(path)
        return config

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    def update_from_dict(self, payload: Dict[str, Any]) -> None:
        field_map = get_type_hints(self.__class__)
        for key, value in payload.items():
            if hasattr(self, key) and value is not None:
                target_type = field_map.get(key, type(getattr(self, key)))
                coerced = _coerce_value(value, target_type, getattr(self, key))
                setattr(self, key, coerced)

    @classmethod
    def descriptions(cls) -> dict[str, str]:
        """Return a mapping of config key -> human-friendly description."""
        if getattr(cls, "DESCRIPTIONS", None) is None:
            cls.DESCRIPTIONS = {
                "daily_new_limit": "每天新学单词数量上限",
                "daily_review_limit": "每天复习单词数量上限",
                "similarity_candidates": "生成干扰项时的候选集大小",
                "default_words_file": "默认词库 CSV 文件路径（用于初始化/创建新存档）",
                "session_storage_path": "当前活跃进度的路径",
                "archive_dir": "存档文件夹，用于保存/读取多个进度存档",
                "extra_batch_ratio": "再学一些时，增加量相对于每日上限的比例",
                "settlement_auto_advance_ms": "结算页自动继续的等待毫秒数；-1表示必须按回车继续",
                "random_new_selection": "是否对新词随机抽样；否则按文件顺序",
            }
        return cls.DESCRIPTIONS or {}
