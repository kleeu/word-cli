from __future__ import annotations

import sys
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Any, Dict, get_type_hints

from prompt_toolkit import prompt  # type: ignore[import]
from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import]
from rich.console import Console  # type: ignore[import]
from rich.panel import Panel  # type: ignore[import]
from rich.table import Table  # type: ignore[import]

from .config import AppConfig
from .practice import PracticeSession
from .storage import ProgressStore, WordRepository


class CliApp:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or Path(__file__).resolve().parents[2]
        self.console = Console()
        self.config_path = self.base_dir / "data" / "config.json"
        self.config = AppConfig.load(self.config_path)
        self._config_field_types = get_type_hints(AppConfig)
        words_path = self._resolve_path(self.config.words_file)
        self.repo = WordRepository(words_path)
        self.store = ProgressStore(self._resolve_path(self.config.session_storage_path))
        self.menu_bindings = self._build_menu_bindings()

    def run(self) -> None:
        while True:
            self.console.clear()
            self.console.print(Panel("Word CLI", title="主菜单", border_style="cyan"))
            table = Table(show_header=False, box=None, expand=False)
            table.add_row("[1]", "开始")
            table.add_row("[2]", "修改配置")
            table.add_row("[3]", "导出进度")
            table.add_row("[4]", "退出")
            self.console.print(table)

            choice = self._read_menu_choice(4)

            if choice == "1":
                self._start_session()
            elif choice == "2":
                self._edit_config()
            elif choice == "3":
                self._export_progress()
            elif choice == "4":
                self.console.print("再见！")
                break

    # Menu actions ------------------------------------------------------
    def _start_session(self) -> None:
        session = PracticeSession(self.config, self.repo, self.store, today=date.today())
        session.run()

    def _edit_config(self) -> None:
        self.console.clear()
        self.console.print(Panel("修改配置 (直接回车保持原值)", title="配置", border_style="magenta"))
        updates: Dict[str, Any] = {}
        for field in fields(AppConfig):
            current = getattr(self.config, field.name)
            target_type = self._config_field_types.get(field.name, type(current))
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

    def _export_progress(self) -> None:
        export_path = self._resolve_path(self.config.export_path)
        self.store.export_progress(export_path)
        self.console.print(f"[green]已导出到 {export_path}。[/green]")
        self.console.print("[dim]按回车返回菜单…[/dim]")
        input()

    # Utilities ---------------------------------------------------------
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

        for digit in "1234":
            bind_key(digit, digit)
            bind_key(f"kp-{digit}", digit)
            bind_key(f"numpad{digit}", digit)
        bind_key("c-c", "4")
        bind_key("escape", "4")
        return kb

    def _read_menu_choice(self, max_choice: int) -> str:
        try:
            result = prompt("请选择: ", key_bindings=self.menu_bindings)
        except (EOFError, KeyboardInterrupt):
            result = "4"
        if result and result in {str(i) for i in range(1, max_choice + 1)}:
            return result
        return "4"

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
