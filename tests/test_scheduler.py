from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from word_cli.config import AppConfig  # type: ignore[import]
from word_cli.scheduler import prepare_daily_session  # type: ignore[import]
from word_cli.storage import ProgressStore, WordRepository  # type: ignore[import]


def test_prepare_daily_session_creates_new_and_review(tmp_path: Path) -> None:
    base_dir = Path(__file__).resolve().parents[1]
    repo = WordRepository(base_dir / "data" / "words.csv")
    progress_path = tmp_path / "progress.json"
    store = ProgressStore(progress_path)
    config = AppConfig(daily_new_limit=3, daily_review_limit=5)

    session = prepare_daily_session(repo, store, config, today=date(2025, 10, 16))

    assert len(session.new_words) == 3
    assert isinstance(store.load_session(session.date), type(session))
