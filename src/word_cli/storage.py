from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator

from .models import ReviewRecord, SessionState, WordEntry, WordState


class WordRepository:
    def __init__(self, csv_path: Path):
        self.csv_path = csv_path
        self._word_map = self._load_words(csv_path)

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
