from __future__ import annotations

import math
import random
import time
from collections import deque
from datetime import date
from typing import Callable, Deque, Iterable, List, Sequence, Tuple, Union
from dataclasses import dataclass

from prompt_toolkit import prompt  # type: ignore[import]
from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import]
from rich.console import Console  # type: ignore[import]
from rich.markup import MarkupError  # type: ignore[import]
from rich.text import Text  # type: ignore[import]

from .config import AppConfig
from .models import ReviewRecord, SessionItem, SessionState, UndoAction, WordEntry, WordState
from .scheduler import prepare_daily_session
from .similarity import pick_distractors
from .storage import ProgressStore, WordRepository
from .scheduler import _chunked_shuffle


ColumnInput = Union[str, Text, Sequence[Union[str, Text]]]


class SessionAbort(RuntimeError):
    """Raised when the user elects to end the session early."""


class PracticeSession:
    def __init__(self, config: AppConfig, repo: WordRepository, store: ProgressStore, today: date | None = None):
        self.console = Console()
        self.config = config
        self.repo = repo
        self.store = store
        self.session_state = prepare_daily_session(repo, store, config, today)
        self.translations = {entry.headword: entry.translation for entry in repo}
        self.queue: Deque[SessionItem] = self._build_queue()
        self.undo_stack: List[UndoAction] = []
        self.last_log: str | Text | None = None
        # Track words that have already been downgraded once in this session
        self.downgraded_today: set[str] = set()
        self.key_bindings = self._build_key_bindings()

    # Public API ---------------------------------------------------------
    def run(self) -> None:
        try:
            while True:
                while self.queue:
                    item = self.queue.popleft()
                    if item.is_new:
                        self._handle_new_word(item)
                    else:
                        self._handle_review_word(item)
                if not self._handle_post_cycle():
                    break
            self._render_completion()
        except SessionAbort:
            self._set_log("[yellow]已退出，会话进度已保存。[/]", immediate=True)

    # Queue helpers -----------------------------------------------------
    def _build_queue(self) -> Deque[SessionItem]:
        """Build initial queue for the session.

        New model: remaining_successes is deprecated and not used to drive the flow.
        - New words: enqueue once (optionally chunk-shuffled).
        - Review words: enqueue once if not already marked completed today.
        """
        queue: Deque[SessionItem] = deque()
        new_list = list(self.session_state.new_words)
        completed_set = set(self.session_state.completed)
        for headword in new_list:
            if headword in completed_set:
                continue
            queue.append(SessionItem(headword=headword, is_new=True))

        for headword in self.session_state.review_words:
            if headword in completed_set:
                continue
            queue.append(SessionItem(headword=headword, is_new=False))
        return queue

    def _shuffle_queue_in_chunks(self) -> None:
        items = list(self.queue)
        items = _chunked_shuffle(items, chunk_size=50)
        self.queue = deque(items)

    def _render_post_cycle_prompt(self) -> None:
        self._clear_main_screen()
        self._render_columns()

        ps = self._progress_summary()
        summary_line = f"今日任务已完成。已新学 {ps['new_total']} 词，复习 {ps['review_total']} 词。"
        self._render_columns(left=summary_line)
        self._render_columns()

        repo_words = list(self.repo.all_words())
        repo_total = max(len(repo_words), 1)

        level_counts = {level: 0 for level in range(1, 6)}
        for headword in repo_words:
            state = self.store.get_word_state(headword)
            for threshold in range(1, 6):
                if state.level >= threshold:
                    level_counts[threshold] += 1

        color_map = {
            1: "red",
            2: "orange3",
            3: "yellow3",
            4: "green",
            5: "green",
        }

        width = self.console.width or 80
        segments = width - 30

        for level in range(1, 6):
            count = level_counts[level]
            ratio = count / repo_total if repo_total else 0.0
            filled = max(0, min(segments, round(ratio * segments)))

            bar = Text(f"lvl>={level}", style=color_map[level])
            bar.append(" [")
            for idx in range(segments):
                char = "=" if idx < filled else " "
                segment = Text(char)
                if idx < filled:
                    segment.stylize(color_map[level])
                bar.append_text(segment)
            bar.append("]")

            percent = ratio * 100
            stats = Text(f"{count}/{repo_total} ({percent:04.1f}%)")
            self._render_columns(left=bar, right=stats)

        self._render_columns()
        self._render_columns(left="[1]  结束")
        self._render_columns(left="[2]  再背一些")
        self._render_log_section()

    def _handle_post_cycle(self) -> bool:
        self._render_post_cycle_prompt()
        while True:
            choice = self._read_choice(["1", "2"], allow_undo=False)
            if choice != "UNDO":
                break
            self._render_post_cycle_prompt()
        if choice == "2":
            if self._extend_with_extra_batch():
                self._set_log("[green]已添加额外单词。[/]")
                return True
            self._set_log("[yellow]没有更多单词可供学习，结束本次会话。[/]", immediate=True)
        return False

    def _extend_with_extra_batch(self) -> bool:
        ratio = max(self.config.extra_batch_ratio, 0.0)
        if ratio <= 0:
            return False

        extra_new = 0
        extra_review = 0
        if self.config.daily_new_limit > 0:
            extra_new = max(1, math.ceil(self.config.daily_new_limit * ratio))
        if self.config.daily_review_limit > 0:
            extra_review = max(1, math.ceil(self.config.daily_review_limit * ratio))

        all_words = self.repo.all_words()
        completed_words = set(self.session_state.completed)

        new_candidates: List[str] = []
        review_candidates: List[Tuple[str, WordState]] = []

        for headword in all_words:
            state = self.store.get_word_state(headword)
            if state.mastered:
                continue
            if state.level == -2:
                if headword in completed_words:
                    continue
                new_candidates.append(headword)
            elif 1 <= state.level <= 5:
                if headword in completed_words:
                    continue
                review_candidates.append((headword, state))
        additions: List[SessionItem] = []

        if getattr(self.config, "random_new_selection", True):
            random.shuffle(new_candidates)
        new_count = min(extra_new, len(new_candidates))
        for headword in new_candidates[:new_count]:
            if headword not in self.session_state.new_words:
                self.session_state.new_words.append(headword)
            additions.append(SessionItem(headword=headword, is_new=True))

        random.shuffle(review_candidates)
        review_count = min(extra_review, len(review_candidates))
        for headword, _state in review_candidates[:review_count]:
            if headword not in self.session_state.review_words:
                self.session_state.review_words.append(headword)
            additions.append(SessionItem(headword=headword, is_new=False))

        if not additions:
            return False

        # when inserting additions, shuffle each 50-word chunk to avoid long tail
        additions_list = list(additions)
        additions_list = _chunked_shuffle(additions_list, chunk_size=50)
        self.queue.extend(additions_list)
        self._shuffle_queue_in_chunks()
        self.store.upsert_session(self.session_state)
        self.store.save()
        return True

    # Rendering ---------------------------------------------------------
    def _progress_summary(self) -> dict[str, int]:
        new_total = len(self.session_state.new_words)
        review_total = len(self.session_state.review_words)
        unique_total = len(set(self.session_state.new_words) | set(self.session_state.review_words))
        completed_set = set(self.session_state.completed)
        completed_new = len([w for w in self.session_state.new_words if w in completed_set])
        completed_review = len([w for w in self.session_state.review_words if w in completed_set])
        total_completed = len(completed_set)
        return {
            "total_completed": total_completed,
            "total_unique": unique_total,
            "completed_new": completed_new,
            "new_total": new_total,
            "completed_review": completed_review,
            "review_total": review_total,
        }

    def _render_header(self, entry: WordEntry, state: WordState) -> None:
        self._clear_main_screen()
        ps = self._progress_summary()
        percent = (ps["total_completed"] / ps["total_unique"] * 100) if ps["total_unique"] else 0.0
        progress_text = f"{ps['total_completed']}/{ps['total_unique']} ({percent:04.1f}%)"
        self._render_columns(right=progress_text)

        level_text = self._level_visual(state.level)
        headword_text = Text(entry.headword)
        if state.level == -2:
            headword_text.append(" ")
            headword_text.append("[new!]", style="green")
        self._render_columns(left=headword_text, right=level_text)

    def _render_new_card(
        self,
        entry: WordEntry,
        state: WordState,
        highlight: dict[str, str] | None = None,
        settlement: bool = False,
    ) -> None:
        highlight = highlight or {}
        self._render_header(entry, state)
        self._render_columns(left=entry.translation)

        self._render_columns()
        option_labels = ["认识", "不认识", "模糊"]
        for idx, label in enumerate(option_labels, start=1):
            if settlement:
                prefix = f"[{idx}]  "
            else:
                prefix = f"[[blue]{idx}[/blue]]  "
            subsequent = " " * (len(f"[{idx}]  "))
            option_lines = self._prefix_lines(label, prefix, subsequent)
            style = highlight.get(str(idx))
            rendered_lines = self._stylize_lines(option_lines, style)
            self._render_columns(left=rendered_lines)

        self._render_columns()
        # 统一规则：
        # - 非结算态：可选，数字蓝色
        # - 结算态：默认无样式；若 highlight["9"] 存在则按其样式渲染
        if settlement:
            base_mastered = "[9]  已掌握"
            if highlight.get("9"):
                mastered_line = self._stylize_markup(base_mastered, highlight["9"])
            else:
                mastered_line = base_mastered
        else:
            mastered_line = "[[blue]9[/blue]]  已掌握"
        self._render_columns(left=mastered_line)
        if settlement:
            helper_line = "[dim][Backspace] undo · [Enter] next · [Ctrl+C] / [Ctrl+D] exit[/]"
        else:
            helper_line = "[dim][Ctrl+C] / [Ctrl+D] exit[/]"
        self._render_columns(left=helper_line)
        self._render_log_section()

    def _render_review_card(
        self,
        entry: WordEntry,
        state: WordState,
        options: Sequence[str],
        settlement: bool = False,
        option_words: Sequence[str] | None = None,
        highlight: dict[int, str] | None = None,
        highlight_all_red: bool = False,
    ) -> None:
        highlight = highlight or {}
        option_words = option_words or []
        self._render_header(entry, state)
        self._render_columns()
        if not settlement:
            for idx, text in enumerate(options, start=1):
                prefix = f"[[blue]{idx}[/blue]]  "
                subsequent = " " * (len(f"[{idx}]  "))
                option_lines = self._prefix_lines(text, prefix, subsequent)
                self._render_columns(left=option_lines)
            self._render_columns(left="[[blue]5[/blue]]  不认识")
            self._render_columns()
            self._render_columns(left="[[blue]9[/blue]]  已掌握")
            self._render_columns(left="[dim][Ctrl+C] / [Ctrl+D] exit[/]")
            self._render_log_section()
            return

        for idx, text in enumerate(options, start=1):
            word = option_words[idx - 1] if idx - 1 < len(option_words) else ""
            style = "red" if highlight_all_red else highlight.get(idx)
            self._render_settlement_option(idx, text, word, style)

        if 5 in highlight:
            dont_know_text = self._stylize_markup("[5]  不认识", highlight[5])
        else:
            dont_know_text = "[5]  不认识"
        self._render_columns(left=dont_know_text)

        self._render_columns()
        if 9 in highlight:
            mastered_text = self._stylize_markup("[9]  已掌握", highlight[9])
        else:
            mastered_text = "[9]  已掌握"
        self._render_columns(left=mastered_text)
        self._render_columns(left="[dim][Backspace] undo · [Enter] next · [Ctrl+C] / [Ctrl+D] exit[/]")
        self._render_log_section()

    def _render_completion(self) -> None:
        self._clear_main_screen()
        self._render_columns()
        self._render_columns(center="[bold green]完成[/]")
        self._render_columns()
        self._render_columns(center="每日任务已完成，这是给你的奖励！")
        self._render_columns(center="[green]EXP +10[/green]")

    # Interaction -------------------------------------------------------
    def _build_key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        def bind_key(key: str, result: str) -> None:
            try:
                @kb.add(key)
                def _(event, _result=result):  # type: ignore[misc]
                    event.app.exit(result=_result)
            except ValueError:
                pass

        for digit in "123456789":
            bind_key(digit, digit)
            bind_key(f"kp-{digit}", digit)
            bind_key(f"numpad{digit}", digit)

        bind_key("backspace", "UNDO")
        bind_key("c-z", "UNDO")
        bind_key("c-c", "QUIT")
        bind_key("c-d", "QUIT")

        return kb

    def _prompt_key(self) -> str:
        try:
            return prompt("", key_bindings=self.key_bindings, refresh_interval=0.2)
        except (EOFError, KeyboardInterrupt):
            raise SessionAbort

    def _read_choice(self, valid: Iterable[str], allow_undo: bool = True) -> str:
        valid_set = set(valid)
        while True:
            result = self._prompt_key()
            if result == "QUIT":
                raise SessionAbort
            if result == "UNDO":
                if not allow_undo:
                    self._set_log("[yellow]请输入有效的数字选项。[/]", immediate=True)
                    continue
                if not self._undo_last_action():
                    self._set_log("[yellow]没有可撤销的操作。[/]", immediate=True)
                    continue
                self._set_log("[green]已撤销上一操作。[/]")
                return "UNDO"
            if result in valid_set:
                return result
            self._set_log("[yellow]请输入有效的数字选项。[/]", immediate=True)

    def _wait_for_settlement(self, require_enter: bool, refresh: Callable[[], None] | None = None) -> str:
        raw_value = getattr(self.config, "settlement_auto_advance_ms", -1)
        try:
            auto_ms = int(raw_value)
        except (TypeError, ValueError):
            auto_ms = -1
        if require_enter or auto_ms < 0:
            while True:
                result = self._prompt_key()
                if result == "QUIT":
                    raise SessionAbort
                if result == "UNDO":
                    if self._undo_last_action():
                        self._set_log("[green]已撤销上一操作。[/]")
                        return "UNDO"
                    self._set_log("[yellow]没有可撤销的操作。[/]", immediate=True)
                    if refresh:
                        refresh()
                    continue
                if result in ("", "\r", "\n"):
                    return "CONTINUE"
                self._set_log("[yellow]请按 Enter 继续。[/]", immediate=True)
                if refresh:
                    refresh()
        else:
            time.sleep(max(auto_ms, 0) / 1000.0)
            return "CONTINUE"

    # Core handlers -----------------------------------------------------
    def _handle_new_word(self, item: SessionItem) -> None:
        entry = self.repo.get(item.headword)
        state = self.store.get_word_state(item.headword)
        undo_action = UndoAction(
            word=item.headword,
            previous_state=self._clone_word_state(state),
            previous_session_state=self._clone_session_state(),
            item=item,
            queue_snapshot=list(self.queue),
        )

        self._render_new_card(entry, state)
        choice = self._read_choice(["1", "2", "3", "9"], allow_undo=False)

        # New words: -2 initial; choices -> 1: recognize => 0, 2: unknown => -2, 3: fuzzy => -1, 9: mastered => 5
        mapping = {
            "1": (0, "recognize"),
            "2": (-2, "unknown"),
            "3": (-1, "fuzzy"),
            "9": (5, "mastered"),
        }
        new_level, outcome = mapping[choice]
        previous_level = state.level
        state.level = new_level
        state.last_seen = self.session_state.date
        state.mastered = state.level > 4

        record = ReviewRecord(
            word=item.headword,
            date=self.session_state.date,
            outcome=outcome,
            level_before=previous_level,
            level_after=state.level,
        )
        state.history.append(record)
        undo_action.last_action = record

        self.store.set_word_state(item.headword, state)
        self.store.append_history(record)
        # Update queue according to new-level rules
        if state.level > 4:
            # mastered directly, mark completed, do not requeue
            if item.headword not in self.session_state.completed:
                self.session_state.completed.append(item.headword)
        else:
            # For 0/-1/-2: always add to queue (new: level -2; others considered review)
            is_new_flag = state.level == -2
            self.queue.append(SessionItem(headword=item.headword, is_new=is_new_flag))
            self._shuffle_queue_in_chunks()
        self.store.upsert_session(self.session_state)
        self.store.save()

        self.undo_stack.append(undo_action)

        highlight: dict[str, str] = {}
        if choice == '1':
            highlight[choice] = "green"
        elif choice == '2':
            highlight[choice] = "red"
        elif choice == '3':
            highlight[choice] = "yellow"
        elif choice == "9":
            highlight["9"] = "bold"
        settlement_highlight = dict(highlight)
        self._render_new_card(entry, state, highlight=settlement_highlight, settlement=True)
        result = self._wait_for_settlement(
            require_enter=self.config.settlement_auto_advance_ms < 0,
            refresh=lambda: self._render_new_card(entry, state, highlight=settlement_highlight, settlement=True),
        )
        if result == "UNDO":
            return

    def _handle_review_word(self, item: SessionItem) -> None:
        entry = self.repo.get(item.headword)
        state = self.store.get_word_state(item.headword)
        undo_action = UndoAction(
            word=item.headword,
            previous_state=self._clone_word_state(state),
            previous_session_state=self._clone_session_state(),
            item=item,
            queue_snapshot=list(self.queue),
        )

        options, option_words, correct_idx = self._build_review_options(item.headword)

        self._render_review_card(entry, state, options)
        choice = self._read_choice(["1", "2", "3", "4", "5", "9"], allow_undo=False)

        previous_level = state.level
        outcome = ""
    # In new model, remaining_successes is deprecated
        highlight: dict[int, str] = {}
        highlight_all_red = False
        require_enter = False

        if choice == "9":
            state.level = 5
            state.mastered = True
            outcome = "mastered"
            highlight[9] = "bold"
            require_enter = self.config.settlement_auto_advance_ms < 0
        elif choice == "5":
            # Incorrect: apply at most one downgrade per word per session
            if item.headword not in self.downgraded_today:
                state.level = max(state.level - 1, -2)
                self.downgraded_today.add(item.headword)
            state.mastered = state.level > 4
            outcome = "incorrect"
            highlight_all_red = True
            highlight[5] = "red"
            require_enter = True
        else:
            selected_idx = int(choice) - 1
            if selected_idx == correct_idx:
                state.level = min(state.level + 1, 5)
                state.mastered = state.level > 4
                outcome = "correct"
                highlight[selected_idx + 1] = "green"
                require_enter = self.config.settlement_auto_advance_ms < 0
            else:
                # Incorrect: apply at most one downgrade per word per session
                if item.headword not in self.downgraded_today:
                    state.level = max(state.level - 1, -2)
                    self.downgraded_today.add(item.headword)
                # keep level if already downgraded earlier this session
                state.mastered = state.level > 4
                outcome = "incorrect"
                highlight[selected_idx + 1] = "red"
                highlight[correct_idx + 1] = "green"
                require_enter = True

        state.last_seen = self.session_state.date
        record = ReviewRecord(
            word=item.headword,
            date=self.session_state.date,
            outcome=outcome,
            level_before=previous_level,
            level_after=state.level,
        )
        state.history.append(record)
        undo_action.last_action = record

        self.store.set_word_state(item.headword, state)
        self.store.append_history(record)

        # Queue updates per new rules
        if state.level >= 1:
            # Upgraded to >=1 or remained >=1: consider done for today, do not requeue
            if item.headword not in self.session_state.completed:
                self.session_state.completed.append(item.headword)
        else:
            # level < 1: requeue (treat -1,0 as review; -2 as new)
            is_new_flag = state.level == -2
            self.queue.append(SessionItem(headword=item.headword, is_new=is_new_flag))
            self._shuffle_queue_in_chunks()

        self.store.upsert_session(self.session_state)
        self.store.save()

        self.undo_stack.append(undo_action)

        settlement_highlight = dict(highlight)
        self._render_review_card(
            entry,
            state,
            options,
            settlement=True,
            option_words=option_words,
            highlight=settlement_highlight,
            highlight_all_red=highlight_all_red,
        )
        result = self._wait_for_settlement(
            require_enter=require_enter,
            refresh=lambda: self._render_review_card(
                entry,
                state,
                options,
                settlement=True,
                option_words=option_words,
                highlight=settlement_highlight,
                highlight_all_red=highlight_all_red,
            ),
        )
        if result == "UNDO":
            return

    # Undo --------------------------------------------------------------
    def _undo_last_action(self) -> bool:
        if not self.undo_stack:
            return False
        action = self.undo_stack.pop()
        self.store.set_word_state(action.word, action.previous_state)
        self.session_state = action.previous_session_state
        if action.last_action:
            self.store.pop_history()
        restored_queue = deque(action.queue_snapshot or [])
        if action.item:
            restored_queue.appendleft(action.item)
        self.queue = restored_queue
        # Allow potential downgrade again after undo for the word
        if action.word in self.downgraded_today:
            self.downgraded_today.discard(action.word)
        self.store.upsert_session(self.session_state)
        self.store.save()
        return True

    # Helpers -----------------------------------------------------------
    def _prefix_lines(
        self,
        text: str | Text,
        first_prefix: str | Text,
        subsequent_prefix: str | Text,
    ) -> List[Text]:
        base = self._to_text(text)
        segments = base.split("\n")
        if not segments:
            segments = [Text("")]
        first = self._to_text(first_prefix)
        subsequent = self._to_text(subsequent_prefix)
        prefixed: List[Text] = []
        for idx, segment in enumerate(segments):
            prefix = first if idx == 0 else subsequent
            line = prefix.copy()
            line.append_text(segment)
            prefixed.append(line)
        return prefixed

    def _stylize_lines(self, lines: Sequence[Text], style: str | None) -> List[Text]:
        if not lines:
            return []
        if not style:
            return [line.copy() for line in lines]
        styled: List[Text] = []
        for line in lines:
            new_line = line.copy()
            new_line.stylize(style)
            styled.append(new_line)
        return styled

    def _render_columns(
        self,
        left: ColumnInput | None = None,
        center: ColumnInput | None = None,
        right: ColumnInput | None = None,
    ) -> None:
        for line in self._compose_column_rows(left, center, right):
            self.console.print(line)

    def _compose_column_rows(
        self,
        left: ColumnInput | None,
        center: ColumnInput | None,
        right: ColumnInput | None,
    ) -> List[Text]:
        width = self.console.width or 80
        left_lines = self._normalize_column_input(left)
        center_lines = self._normalize_column_input(center)
        right_lines = self._normalize_column_input(right)
        if not any((left_lines, center_lines, right_lines)):
            return [Text("")]

        total_lines = max(len(left_lines), len(center_lines), len(right_lines))
        result: List[Text] = []
        for idx in range(total_lines):
            left_text = left_lines[idx] if idx < len(left_lines) else Text("")
            center_text = center_lines[idx] if idx < len(center_lines) else Text("")
            right_text = right_lines[idx] if idx < len(right_lines) else Text("")
            result.append(self._compose_column_row(left_text, center_text, right_text, width))
        return result

    def _normalize_column_input(self, value: ColumnInput | None) -> List[Text]:
        if value is None:
            return []
        if isinstance(value, Text):
            return [value.copy()]
        if isinstance(value, Sequence) and not isinstance(value, (str, Text)):
            collected: List[Text] = []
            for item in value:
                collected.extend(self._normalize_column_input(item))
            return collected
        text_obj = self._to_text(value)
        segments = text_obj.split("\n")
        if not segments:
            segments = [Text("")]
        return [segment.copy() for segment in segments]

    def _to_text(self, value: str | Text) -> Text:
        if isinstance(value, Text):
            return value.copy()
        try:
            return Text.from_markup(str(value), emoji=False)
        except MarkupError:
            return Text(str(value))

    def _compose_column_row(self, left: Text, center: Text, right: Text, width: int) -> Text:
        line = Text()
        width = max(width, 1)

        left_text = left.copy()
        center_text = center.copy()
        right_text = right.copy()

        left_len = left_text.cell_len
        center_len = center_text.cell_len
        right_len = right_text.cell_len

        min_total = left_len + center_len + right_len
        if left_len and center_len:
            min_total += 1
        if center_len and right_len:
            min_total += 1
        if left_len and not center_len and right_len:
            min_total += 1
        width = max(width, min_total)

        cursor = 0

        def append_spaces(target: int) -> None:
            nonlocal cursor
            spaces = max(0, target - cursor)
            if spaces:
                line.append(" " * spaces)
                cursor += spaces

        def append_text(segment: Text) -> None:
            nonlocal cursor
            seg_len = segment.cell_len
            if seg_len:
                line.append_text(segment)
                cursor += seg_len

        if left_len:
            append_text(left_text)

        if center_len:
            min_pos = cursor + (1 if cursor else 0)
            desired = (width - center_len) // 2
            desired = max(desired, min_pos)
            desired = min(desired, width - center_len)
            desired = max(desired, min_pos)
            append_spaces(desired)
            append_text(center_text)

        if right_len:
            min_pos = cursor + (1 if cursor else 0)
            desired = max(width - right_len, min_pos)
            append_spaces(desired)
            append_text(right_text)

        if line.cell_len == 0:
            return Text("")
        return line

    def _render_settlement_option(
        self,
        idx: int,
        text: str,
        word: str,
        style: str | None,
    ) -> None:
        prefix = f"[{idx}]  "
        lines = text.splitlines() or [text]
        word_text = self._stylize_markup(word, style) if word else ""

        first_line = self._stylize_markup(f"{prefix}{lines[0]}", style)
        self._render_columns(left=first_line, right=word_text or None)

        indent = " " * len(prefix)
        for line in lines[1:]:
            continuation = self._stylize_markup(f"{indent}{line}", style)
            self._render_columns(left=continuation)

    def _clear_main_screen(self) -> None:
        self.console.clear()
        self._render_columns()
        self._render_columns()

    def _set_log(self, message: str | Text | None, *, immediate: bool = False) -> None:
        if message is None:
            self.last_log = None
        else:
            self.last_log = message
        if immediate and message:
            self._print_log_line(message)

    def _print_log_line(self, message: str | Text) -> None:
        self._render_columns()
        prefix = Text("[log]", style="dim")
        body = self._to_text(message)
        if not body.spans:
            body.stylize("dim")
        combined = Text()
        combined.append_text(prefix)
        combined.append(" ")
        combined.append_text(body)
        self._render_columns(left=combined)
        self.last_log = None

    def _render_log_section(self) -> None:
        if not self.last_log:
            return
        self._print_log_line(self.last_log)

    def _stylize_markup(self, text: str, style: str | None) -> str:
        if not style:
            return text
        return f"[{style}]{text}[/]"


    def _find_headword_for_translation(self, translation: str, exclude: set[str]) -> str:
        for headword, text in self.translations.items():
            if headword in exclude:
                continue
            if text == translation:
                return headword
        return ""

    def _clone_word_state(self, state: WordState) -> WordState:
        return WordState.from_dict(state.to_dict())

    def _clone_session_state(self) -> SessionState:
        return SessionState.from_dict(self.session_state.to_dict())

    def _level_visual(self, level: int) -> Text:
        width = 5
        if level < 0:
            sym = "-" * min(abs(level), width)
            color = "red"
        elif level > 0:
            sym = "+" * min(level, width)
            color = "green"
        else:
            sym = ""
            color = None

        pad = " " * (width - len(sym))
        out = Text("[")
        if pad:
            out.append(pad)
        if sym:
            out.append(sym, style=color)
        out.append("]")
        return out

    def _build_review_options(self, headword: str) -> tuple[List[str], List[str], int]:
        entry = self.repo.get(headword)
        distractors = pick_distractors(
            headword,
            self.translations.keys(),
            self.translations,
            desired=3,
            pool_size=self.config.similarity_candidates,
        )
        translation_set = set(distractors)
        if len(distractors) < 3:
            candidates = [v for k, v in self.translations.items() if k != headword and v not in translation_set]
            random.shuffle(candidates)
            for cand in candidates:
                distractors.append(cand)
                translation_set.add(cand)
                if len(distractors) == 3:
                    break
        distractors = distractors[:3]
        used_words = {headword}
        option_pairs: List[Tuple[str, str]] = []
        for translation in distractors:
            alt_word = self._find_headword_for_translation(translation, used_words)
            if alt_word:
                used_words.add(alt_word)
            option_pairs.append((translation, alt_word))
        option_pairs.append((entry.translation, headword))
        random.shuffle(option_pairs)
        options = [text for text, _ in option_pairs]
        option_words = [word for _, word in option_pairs]
        correct_index = option_words.index(headword)
        return options, option_words, correct_index
