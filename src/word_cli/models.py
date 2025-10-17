from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class WordEntry:
    headword: str
    translation: str


@dataclass
class ReviewRecord:
    word: str
    date: str
    outcome: str
    level_before: int
    level_after: int


@dataclass
class WordState:
    level: int = -2
    history: List[ReviewRecord] = field(default_factory=list)
    last_seen: Optional[str] = None
    mastered: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "history": [record.__dict__ for record in self.history],
            "last_seen": self.last_seen,
            "mastered": self.mastered,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WordState":
        history = [ReviewRecord(**record) for record in data.get("history", [])]
        return cls(
            level=data.get("level", -2),
            history=history,
            last_seen=data.get("last_seen"),
            mastered=data.get("mastered", False),
        )


@dataclass
class SessionItem:
    headword: str
    is_new: bool
    remaining_successes: int = 1


@dataclass
class SessionState:
    date: str
    new_words: List[str] = field(default_factory=list)
    review_words: List[str] = field(default_factory=list)
    completed: List[str] = field(default_factory=list)
    remaining_successes: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "new_words": self.new_words,
            "review_words": self.review_words,
            "completed": self.completed,
            "remaining_successes": self.remaining_successes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        return cls(
            date=data.get("date") or "",
            new_words=list(data.get("new_words", [])),
            review_words=list(data.get("review_words", [])),
            completed=list(data.get("completed", [])),
            remaining_successes=dict(data.get("remaining_successes", {})),
        )


@dataclass
class UndoAction:
    word: str
    previous_state: WordState
    previous_session_state: SessionState
    last_action: Optional[ReviewRecord] = None
    item: SessionItem | None = None
    queue_snapshot: list[SessionItem] | None = None
