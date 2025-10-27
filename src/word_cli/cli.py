from __future__ import annotations

import sys
from dataclasses import fields
import textwrap
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, get_type_hints

import json
from prompt_toolkit import prompt  # type: ignore[import]
from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import]
from rich.console import Console  # type: ignore[import]
from rich.panel import Panel  # type: ignore[import]
from rich.table import Table  # type: ignore[import]
from rich.markdown import Markdown  # type: ignore[import]

from .config import AppConfig
from .practice import PracticeSession
from .storage import (
    ProgressStore,
    WordRepository,
    is_valid_archive_payload,
    distribution_1to5_percent,
    count_words_in_payload,
    validate_archive_name,
)
from .ui import ConsoleUI


class CliApp:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(__file__).resolve().parents[2]
        self.console = Console()
        self.config_path = self.base_dir / "data" / "config.json"
        self.config = AppConfig.load(self.config_path)
        self._config_field_types = get_type_hints(AppConfig)
        words_path = self._resolve_path(self.config.default_words_file)
        self.repo = WordRepository(words_path)
        self.store = ProgressStore(self._resolve_path(self.config.session_storage_path))
        self.menu_bindings = self._build_menu_bindings()
        self.ui = ConsoleUI(self.console)

    def run(self) -> None:
        while True:
            self.console.clear()
            header = self._build_menu_header()
            self.console.print(Panel(header, title="Word CLI", border_style="cyan"))
            table = Table(show_header=False, box=None, expand=False)
            table.add_row("[1]", "开始")
            table.add_row("[2]", "修改配置")
            table.add_row("[3]", "存档读档")
            table.add_row("[4]", "帮助")
            table.add_row("[5]", "退出")
            self.console.print(table)

            choice = self._read_menu_choice(5)

            if choice == "1":
                self._start_session()
            elif choice == "2":
                self._edit_config()
            elif choice == "3":
                self._archive_menu()
            elif choice == "4":
                self._show_help()
            elif choice == "5":
                self.console.print("再见！")
                break

    # Menu actions ------------------------------------------------------
    def _start_session(self) -> None:
        session = PracticeSession(self.config, self.repo, self.store, today=date.today())
        session.run()

    def _edit_config(self) -> None:
        self.console.clear()
        self.console.print(Panel("修改配置 (按回车保持原值)", title="配置", border_style="blue"))
        updates: Dict[str, Any] = {}
        descriptions = AppConfig.descriptions()
        for field in fields(AppConfig):
            current = getattr(self.config, field.name)
            target_type = self._config_field_types.get(field.name, type(current))
            desc = descriptions.get(field.name, "")
            if desc:
                self.console.print(f"[dim]{field.name}：{desc}[/dim]")
            prompt_text = f"{field.name} (当前 {current}): "
            user_input = input(prompt_text).strip()
            if not user_input:
                continue
            try:
                updates[field.name] = self._parse_config_input(user_input, target_type)
            except ValueError:
                self.console.print(f"[red]无法解析 {field.name}，已跳过。[/red]")
        if updates:
            self.config.update_from_dict(updates)
            self.config.save(self.config_path)
            self.store = ProgressStore(self._resolve_path(self.config.session_storage_path))
            self.console.print("[green]配置已更新。[/green]")
        else:
            self.console.print("[yellow]未作任何更改。[/yellow]")
        self.console.print("[dim]按回车返回菜单…[/dim]")
        input()

    def _archive_menu(self) -> None:
        def shorten_right(s: str, max_len: int = 40) -> str:
            s = str(s)
            return s if len(s) <= max_len else ("…" + s[-(max_len - 1):])

        self.console.clear()
        archive_dir = self._resolve_path(self.config.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        entries: list[tuple[str, dict]] = []
        for p in sorted(archive_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if is_valid_archive_payload(data):
                    entries.append((p.name, data))
            except Exception:
                continue

        # option 1: active progress archive
        active_path = self._resolve_path(self.config.session_storage_path)
        rows: list[dict[str, Any]] = []
        rows.append({
            "cells": ["<另存为存档>", "-", "-", "-", "-"],
            "dim": False,
            "action": ("save_new", None),
        })
        # option 2: create a new archive (blank) with chosen CSV
        rows.append({
            "cells": ["<创建新存档>", "-", "-", "-", "-"],
            "dim": False,
            "action": ("create_new", None),
        })
        active_abs = active_path.resolve()
        for fname, data in entries:
            meta = data.get("meta", {}) if isinstance(data, dict) else {}
            words = data.get("words", {}) if isinstance(data, dict) else {}
            words_file = shorten_right(meta.get("words_file", "?"))
            total_words = count_words_in_payload(words)
            dist = distribution_1to5_percent(words)
            ts = meta.get("last_saved") or meta.get("last_opened") or ""
            if not ts:
                try:
                    p = archive_dir / fname
                    ts = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                except Exception:
                    ts = "?"
            entry_path = (archive_dir / fname).resolve()
            is_active = (entry_path == active_abs)
            rows.append({
                "cells": [fname, words_file, str(total_words), dist, ts],
                "dim": is_active,
                "action": ("active_notice", entry_path) if is_active else ("load_file", (archive_dir / fname, data)),
            })

        # Active progress row will be dimmed inline in the list if it resides in archive_dir;
        # if not in archive_dir, it won't appear here per unified listing.

        kb = KeyBindings()

        @kb.add("up")
        def _(event) -> None:  # type: ignore[misc]
            event.app.exit(result="up")

        @kb.add("down")
        def _(event) -> None:  # type: ignore[misc]
            event.app.exit(result="down")

        @kb.add("enter")
        def _(event) -> None:  # type: ignore[misc]
            event.app.exit(result="enter")

        @kb.add("escape")
        @kb.add("backspace")
        def _(event) -> None:  # type: ignore[misc]
            event.app.exit(result="back")

        cur = 0
        while True:
            self.console.clear()
            table = Table(show_header=True, header_style="bold", box=None)
            table.add_column("存档名")
            table.add_column("词库")
            table.add_column("词数")
            table.add_column("进度 1|2|3|4|5")
            table.add_column("上次保存/修改")
            for i, row in enumerate(rows):
                style = "reverse" if i == cur else ("dim" if row.get("dim") else None)
                table.add_row(*row["cells"], style=style)
            self.console.print(Panel(table, border_style="blue", title="存档读档"))
            self.console.print("[dim]↑↓ 选择, Enter 确定, Backspace/Esc 返回[/dim]")
            try:
                key = prompt("", key_bindings=kb)
            except (EOFError, KeyboardInterrupt):
                return
            if key == "up":
                cur = (cur - 1) % len(rows)
                continue
            if key == "down":
                cur = (cur + 1) % len(rows)
                continue
            if key == "back":
                return
            if key == "enter":
                action, payload = rows[cur]["action"]
                if action == "save_new":
                    self._archive_save_flow(entries)
                    return
                if action == "create_new":
                    self._archive_create_new_flow()
                    return
                if action == "active_notice":
                    self._archive_load_active_notice(active_path)
                    return
                if action == "load_file":
                    path, data = payload
                    if isinstance(path, Path):
                        self._archive_load_flow(path, data)
                        return

    def _archive_save_flow(self, entries: list[tuple[str, dict]]) -> None:
        archive_dir = self._resolve_path(self.config.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)

        quick_name = (self.store.data.get("meta", {}) or {}).get("current_archive_name")
        if quick_name:
            self.console.print(f"[dim]快捷：输入 ! 覆盖存档 {quick_name}.json[/dim]")

        name = input("输入存档名，按回车取消：").strip()
        if not name:
            return
        if name == "!" and quick_name:
            name = quick_name
        ok, err = validate_archive_name(name, forbidden_file_names={Path(self.config.session_storage_path).name})
        if not ok:
            self.console.print(f"[red]{err}[/red]")
            return

        target = archive_dir / f"{name}.json"
        # Compare current progress words_file with the to-be-saved archive's words_file (same as current)
        current_wf = (self.store.data.get("meta", {}) or {}).get("words_file")
        wf_abs = str(self._resolve_path(str(current_wf))) if current_wf else None
        new_abs = wf_abs  # saving snapshot keeps same words_file
        if wf_abs and new_abs and wf_abs != new_abs:
            self.console.print("[yellow]注意：当前词库与存档词库不一致，但仍将保存快照。[/yellow]")

        if target.exists():
            ans = input(f"{target.name} 已存在，确认覆盖？(y/N): ").strip().lower()
            if ans not in {"y", "yes"}:
                return

        data = self.store.snapshot()
        meta = data.setdefault("meta", {})
        meta.setdefault("app_id", "word-cli-demo")
        meta.setdefault("schema_version", 1)
        meta["last_saved"] = datetime.now().isoformat(timespec="seconds")
        meta["current_archive_name"] = name
        # Persist words_file from current progress (relative path if present), fallback to default
        meta["words_file"] = current_wf or self.config.default_words_file

        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        self.console.print(f"[green]已保存到 {target}[/green]")
        input("按回车返回…")

    def _archive_load_flow(self, path: Path, data: dict) -> None:
        active = self._resolve_path(self.config.session_storage_path)
        backup = active.with_suffix(active.suffix + ".bak.json")
        ans = input(f"将读取存档 {path.name} 覆盖当前进度，确认？(y/N): ").strip().lower()
        if ans not in {"y", "yes"}:
            return
        try:
            tmp_bak = backup.with_suffix(backup.suffix + ".tmp")
            bak_data = self.store.snapshot()
            bak_meta = bak_data.setdefault("meta", {})
            bak_meta["words_file"] = (self.store.data.get("meta", {}) or {}).get("words_file") or self.config.default_words_file
            tmp_bak.write_text(json.dumps(bak_data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp_bak.replace(backup)
        except Exception:
            pass

        tmp = active.with_suffix(active.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(active)

        self.store = ProgressStore(active)
        meta = self.store.data.setdefault("meta", {})
        meta["last_opened"] = datetime.now().isoformat(timespec="seconds")
        self.store.save()

        # Use relative words_file in meta for the active progress (default path for initialization fallback)
        self.store.ensure_all_words_initialized(self.repo, words_file=Path(self.config.default_words_file))
        self.console.print("[green]读档成功。[/green]")
        input("按回车返回…")

    def _archive_create_new_flow(self) -> None:
        """Create a brand new archive with a chosen CSV words file.

        Steps:
        1) Ask CSV path with default from config.default_words_file
        2) Confirm creation
        3) Ask archive name and save a blank-initialized archive (all words at level -2)
        """
        # Step 1: CSV path input with default
        default_csv = self.config.default_words_file
        prompt_text = f"新存档词库 CSV 文件路径 (当前 {default_csv}，按回车保持原值)："
        user_csv = input(prompt_text).strip()
        if not user_csv:
            user_csv = default_csv

        # Normalize to relative to project root if possible for metadata
        csv_path_abs = self._resolve_path(user_csv)
        try:
            rel_path = str(csv_path_abs.relative_to(self.base_dir))
        except Exception:
            rel_path = user_csv  # keep as provided (likely already relative)

        # Step 2: Confirm
        ans = input(f"将使用词库 {rel_path} 创建新存档，确认？(y/N): ").strip().lower()
        if ans not in {"y", "yes"}:
            return

        # Step 3: Archive name
        name = input("输入新存档名称：").strip()
        ok, err = validate_archive_name(name, forbidden_file_names={Path(self.config.session_storage_path).name})
        if not ok:
            self.console.print(f"[red]{err or '名称不合法'}[/red]")
            return

        archive_dir = self._resolve_path(self.config.archive_dir)
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / f"{name}.json"
        if target.exists():
            ans2 = input(f"{target.name} 已存在，确认覆盖？(y/N): ").strip().lower()
            if ans2 not in {"y", "yes"}:
                return

        # Build initial archive data based on CSV
        try:
            repo = WordRepository(csv_path_abs)
        except FileNotFoundError as e:
            self.console.print(f"[red]{e}[/red]")
            return

        words_dict: Dict[str, Any] = {h: {"level": -2} for h in repo.all_words().keys()}
        data = {
            "words": words_dict,
            "history": [],
            "sessions": {},
            "meta": {
                "app_id": "word-cli-demo",
                "schema_version": 1,
                "last_saved": datetime.now().isoformat(timespec="seconds"),
                "current_archive_name": name,
                "words_file": rel_path,
            },
        }

        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        self.console.print(f"[green]新存档已创建：{target}[/green]")
        input("按回车返回…")

    def _archive_load_active_notice(self, active_path: Path) -> None:
        name = active_path.name
        self.console.print(f"[yellow]{name} 是当前活跃进度文件，用于存放你当前打开的存档。[/yellow]")
        self.console.print("[yellow]如果你想切换进度，请选择其他的某个存档进行读档。[/yellow]")
        input("按回车返回…")

    def _resolve_path(self, configured_path: str) -> Path:
        path = Path(configured_path)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def _build_menu_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        def bind_key(key: str, result: str) -> None:
            try:
                @kb.add(key)
                def _(event, _result=result):  # type: ignore[misc]
                    event.app.exit(result=_result)
            except ValueError:
                pass

        for digit in "12345":
            bind_key(digit, digit)
            bind_key(f"kp-{digit}", digit)
            bind_key(f"numpad{digit}", digit)
        bind_key("c-c", "5")
        bind_key("escape", "5")
        return kb

    def _read_menu_choice(self, max_choice: int) -> str:
        try:
            result = prompt("请选择: ", key_bindings=self.menu_bindings)
        except (EOFError, KeyboardInterrupt):
            result = "5"    # 5 = exit
        if result and result in {str(i) for i in range(1, max_choice + 1)}:
            return result
        return "5" # 5 = exit


    def _show_help(self) -> None:
        self.console.clear()
        help_text = textwrap.dedent("""
        ## 操作篇
        1. 菜单页面中，按 `[]` 内的数字键选择对应选项。选择 `[1] 开始` 进入背单词页面。
        2. 背单词页面中，按 `[]` 内的数字键选择对应选项，按 `[9]` 选择标记单词为已掌握并永久跳过。
        3. 背单词页面中，选择选项后如果需要确认，按 `[Enter]` 确认，此时也可以按 `[Backspace]` 撤销上一次的选择。

        ## 功能篇
        1. 根据导入的 CSV 单词表，程序会自动安排每日的新词和复习任务。
        2. CSV 单词表应包含至少两列：单词和释义。支持 UTF-8 编码。
        3. 背单词被划分为新词和复习两种类型。新词是指尚未学习过的单词，复习是指已经学习过需要定期复习的单词。
        4. 复习单词时提供的混淆项根据相似度算法选择，并排除形态学变换后的同源词。
        5. 可以通过修改配置调整每日新词和复习单词的数量、相似度候选项数量等参数。
        6. 进度会在程序退出时自动保存，下次启动时会继续之前的进度。

        ## 概念篇
        1. 每个单词被标记为 -2 至 5 共 8 个等级。
        2. 等级 -2 至 0 作为内部等级，用于标识新词的不同状态，不会被呈现给用户。
        3. 等级 1 至 5 标识已学习单词的掌握程度，在背单词页面的右上角显示。
        4. 每次复习时，如果选择正确会提升等级，选择错误会降低等级。降低等级每日最多一次。
        5. 等级 5 表示单词已完全掌握，程序不会再安排复习该单词。
        6. 等级会影响单词出现在复习列表中的概率权重。
        """).strip()
        self.console.print(Panel(Markdown(help_text), title="帮助", border_style="blue"))
        self.console.print("[dim]按回车返回菜单…[/dim]")
        input()

    def _build_menu_header(self) -> "ConsoleRenderable":  # type: ignore[name-defined]
        from rich.text import Text
        from rich.console import Group

        items: list[Text] = []
        words_file = str(self._resolve_path(self.config.default_words_file))
        items.append(Text(f"Curr Dict: {words_file}"))

        history = self.store.data.get("history", [])
        recent = history[-1]["date"] if history else "null"
        items.append(Text(f"Last Seen: {recent}"))

        repo_words = list(self.repo.all_words().keys())
        repo_total = max(len(repo_words), 1)
        level_counts = {level: 0 for level in range(1, 6)}
        for headword in repo_words:
            state = self.store.get_word_state(headword)
            for threshold in range(1, 6):
                if state.level >= threshold:
                    level_counts[threshold] += 1

        width = self.console.width or 80
        width -= 4
        for level in range(1, 6):
            count = level_counts[level]
            line = self.ui.build_ratio_bar_and_stat(
                label=f"lvl>={level}", count=count, total=repo_total, level=level, width=width
            )
            items.append(line)

        return Group(*items)

    def _parse_config_input(self, raw: str, target_type: type[Any]) -> Any:
        if target_type is bool:
            lowered = raw.strip().lower()
            if lowered in {"true", "1", "yes", "y", "on"}:
                return True
            if lowered in {"false", "0", "no", "n", "off"}:
                return False
            raise ValueError()
        if target_type is int:
            return int(raw)
        if target_type is float:
            return float(raw)
        return raw


def main() -> None:
    app = CliApp()
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - top-level guard
        Console().print(f"[red]发生错误: {exc}[/red]")
        sys.exit(1)
