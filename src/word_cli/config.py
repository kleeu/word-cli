from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, get_type_hints


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
    words_file: str = "data/words.csv"
    session_storage_path: str = "data/progress.json"
    export_path: str = "data/export.csv"
    extra_batch_ratio: float = 0.1
    settlement_auto_advance_ms: int = -1
    random_new_selection: bool = True

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
