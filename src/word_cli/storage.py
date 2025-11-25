from __future__ import annotations

import csv
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator

from .models import ReviewRecord, SessionState, WordEntry, WordState


class WordRepository:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self._word_map = self._load_words(csv_path)
        self._checksum: str | None = None

    @staticmethod
    def _load_words(csv_path: Path) -> Dict[str, WordEntry]:
        word_map: Dict[str, WordEntry] = {}
        if not csv_path.exists():
            raise FileNotFoundError(f"Word list not found: {csv_path}")

        with csv_path.open("r", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if not row:
                    continue
                headword = row[0].strip()
                if not headword:
                    continue
                translation = "".join(row[1:]).strip() if len(row) > 1 else ""
                word_map[headword] = WordEntry(headword=headword, translation=translation)
        return word_map

    def all_words(self) -> Dict[str, WordEntry]:
        return self._word_map

    def get(self, headword: str) -> WordEntry:
        return self._word_map[headword]

    def get_checksum(self) -> str:
        """Calculate a stable checksum of the loaded dictionary content."""
        if self._checksum is not None:
            return self._checksum

        # Sort by headword to ensure stability
        sorted_items = sorted(self._word_map.items())
        hasher = hashlib.sha256()
        for headword, entry in sorted_items:
            # Feed headword and translation
            hasher.update(headword.encode("utf-8"))
            hasher.update(entry.translation.encode("utf-8"))

        full_hash = hasher.hexdigest()
        self._checksum = full_hash
        return self._checksum

    def __iter__(self) -> Iterator[WordEntry]:
        return iter(self._word_map.values())


class ProgressStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()
        # ensure required keys exist
        self.data.setdefault("words", {})
        self.data.setdefault("history", [])
        self.data.setdefault("sessions", {})

    def _load(self) -> Dict[str, Any]:
        default_payload = {
            "words": {},
            "history": [],
            "sessions": {},
        }
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(default_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return default_payload

        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            self.path.write_text(json.dumps(default_payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return default_payload

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def ensure_all_words_initialized(self, repo: WordRepository, words_file: Path | None = None) -> None:
        """Ensure every word from repo exists in store with default level -2.

        Also write metadata for words_file to support future updates.
        """
        words = self.data.setdefault("words", {})
        for headword in repo.all_words().keys():
            if headword not in words:
                words[headword] = WordState(level=-2).to_dict()
        meta = self.data.setdefault("meta", {})
        if words_file is not None:
            meta["words_file"] = str(words_file)
        self.save()

    # Word state helpers -------------------------------------------------
    def get_word_state(self, headword: str) -> WordState:
        word_payload = self.data.get("words", {}).get(headword)
        if word_payload is None:
            return WordState()
        return WordState.from_dict(word_payload)

    def set_word_state(self, headword: str, state: WordState) -> None:
        self.data.setdefault("words", {})[headword] = state.to_dict()

    def append_history(self, record: ReviewRecord) -> None:
        self.data.setdefault("history", []).append(record.__dict__)

    def pop_history(self) -> None:
        history = self.data.get("history")
        if isinstance(history, list) and history:
            history.pop()

    # Session helpers ----------------------------------------------------
    def load_session(self, date: str) -> SessionState | None:
        sessions = self.data.get("sessions", {})
        payload = sessions.get(date)
        if not payload:
            return None
        return SessionState.from_dict(payload)

    def upsert_session(self, session: SessionState) -> None:
        self.data.setdefault("sessions", {})[session.date] = session.to_dict()

    # Export -------------------------------------------------------------
    def export_progress(self, export_path: Path) -> None:
        words = self.data.get("words", {})
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["word", "level", "mastered", "last_seen", "history_count"])
            for headword, meta in sorted(words.items()):
                writer.writerow(
                    [
                        headword,
                        meta.get("level", -1),
                        meta.get("mastered", False),
                        meta.get("last_seen", ""),
                        len(meta.get("history", [])),
                    ]
                )

    # Utility ------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return deepcopy(self.data)


# Archive helpers ---------------------------------------------------------------
def is_valid_archive_payload(data: Any) -> bool:
    """Basic check to decide whether a JSON payload can be treated as an archive.

    Rules:
    - Prefer meta.app_id/schema_version markers
    - Otherwise accept dicts containing at least 'words' and 'history'
    """
    if not isinstance(data, dict):
        return False
    meta = data.get("meta", {})
    if isinstance(meta, dict) and meta.get("app_id") in {"word-cli", "word-cli-demo"}:
        return True
    return ("words" in data and isinstance(data.get("words"), dict) and "history" in data)


def distribution_1to5_percent(words: Dict[str, Any]) -> str:
    """Return a compact percent string for levels 1..5, ignoring 'new' bucket.

    Percent denominator is total words count to keep scale comparable across archives.
    Format: 'p1|p2|p3|p4|p5' where each is like '12.3%'.
    """
    total = max(len(words or {}), 1)
    buckets = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for _w, meta in (words or {}).items():
        try:
            lvl = meta.get("level", -2)
        except AttributeError:
            lvl = -2
        if lvl in buckets:
            buckets[lvl] += 1

    def pct(n: int) -> str:
        return f"{(n/total*100):.1f}%"

    return "|".join([pct(buckets[i]) for i in range(1, 6)])


def count_words_in_payload(words: Dict[str, Any]) -> int:
    try:
        return len(words or {})
    except Exception:
        return 0


def validate_archive_name(name: str, forbidden_file_names: set[str] | None = None) -> tuple[bool, str | None]:
    """Validate an archive name. Allows A-Za-z0-9_- only.

    Optionally forbid specific target file names (e.g., session_storage_path basename).
    Returns (ok, error_message)."""
    import re

    if not name:
        return False, "名称为空"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return False, "仅允许字母、数字、下划线和短横线"
    if forbidden_file_names:
        target = f"{name}.json"
        if target in forbidden_file_names:
            return False, f"禁止使用 {target} 作为存档名"
    return True, None
