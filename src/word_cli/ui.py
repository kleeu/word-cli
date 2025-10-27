from __future__ import annotations

from typing import Sequence, Union, List
from rich.console import Console  # type: ignore[import]
from rich.markup import MarkupError  # type: ignore[import]
from rich.text import Text  # type: ignore[import]

ColumnInput = Union[str, Text, Sequence[Union[str, Text]]]


class ConsoleUI:
	"""Reusable console UI helpers for column layout and common widgets.

	Methods mirror the helpers previously implemented in PracticeSession.
	"""

	def __init__(self, console: Console | None = None) -> None:
		self.console = console or Console()
		# last_log buffer for deferred log printing
		self.last_log: Text | None = None

	# Basic rendering primitives -------------------------------------------------
	def render_columns(
		self,
		left: ColumnInput | None = None,
		center: ColumnInput | None = None,
		right: ColumnInput | None = None,
	) -> None:
		for line in self.compose_column_rows(left, center, right):
			self.console.print(line)

	def clear_main_screen(self) -> None:
		self.console.clear()
		self.render_columns()
		self.render_columns()

	# Text utilities -------------------------------------------------------------
	def to_text(self, value: str | Text) -> Text:
		if isinstance(value, Text):
			return value.copy()
		try:
			return Text.from_markup(str(value), emoji=False)
		except MarkupError:
			return Text(str(value))

	def stylize_markup(self, text: str, style: str | None) -> str:
		if not style:
			return text
		return f"[{style}]{text}[/]"

	def normalize_column_input(self, value: ColumnInput | None) -> List[Text]:
		if value is None:
			return []
		if isinstance(value, Text):
			return [value.copy()]
		if isinstance(value, Sequence) and not isinstance(value, (str, Text)):
			collected: List[Text] = []
			for item in value:
				collected.extend(self.normalize_column_input(item))
			return collected
		text_obj = self.to_text(value)
		segments = text_obj.split("\n")
		if not segments:
			segments = [Text("")]
		return [segment.copy() for segment in segments]

	def compose_column_row(self, left: Text, center: Text, right: Text, width: int) -> Text:
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

	def compose_column_rows(
		self,
		left: ColumnInput | None,
		center: ColumnInput | None,
		right: ColumnInput | None,
	) -> List[Text]:
		width = self.console.width or 80
		left_lines = self.normalize_column_input(left)
		center_lines = self.normalize_column_input(center)
		right_lines = self.normalize_column_input(right)
		if not any((left_lines, center_lines, right_lines)):
			return [Text("")]

		total_lines = max(len(left_lines), len(center_lines), len(right_lines))
		result: List[Text] = []
		for idx in range(total_lines):
			left_text = left_lines[idx] if idx < len(left_lines) else Text("")
			center_text = center_lines[idx] if idx < len(center_lines) else Text("")
			right_text = right_lines[idx] if idx < len(right_lines) else Text("")
			result.append(self.compose_column_row(left_text, center_text, right_text, width))
		return result

	# Higher-level widgets -------------------------------------------------------
	def prefix_lines(
		self,
		text: str | Text,
		first_prefix: str | Text,
		subsequent_prefix: str | Text,
	) -> List[Text]:
		base = self.to_text(text)
		segments = base.split("\n")
		if not segments:
			segments = [Text("")]
		first = self.to_text(first_prefix)
		subsequent = self.to_text(subsequent_prefix)
		prefixed: List[Text] = []
		for idx, segment in enumerate(segments):
			prefix = first if idx == 0 else subsequent
			line = prefix.copy()
			line.append_text(segment)
			prefixed.append(line)
		return prefixed

	def print_log_line(self, message: str | Text) -> None:
		self.render_columns()
		prefix = Text("[log]", style="dim")
		body = self.to_text(message)
		if not body.spans:
			body.stylize("dim")
		combined = Text()
		combined.append_text(prefix)
		combined.append(" ")
		combined.append_text(body)
		self.render_columns(left=combined)

	# Logging helpers -----------------------------------------------------------
	def set_log(self, message: str | Text | None, *, immediate: bool = False) -> None:
		if message is None:
			self.last_log = None
		else:
			self.last_log = self.to_text(message)
		if immediate and message:
			self.print_log_line(self.last_log or "")
			self.last_log = None

	def render_log_section(self) -> None:
		if not self.last_log:
			return
		self.print_log_line(self.last_log)
		self.last_log = None

	def build_ratio_bar(self, label: str, count: int, total: int, width: int, color: str) -> Text:
		total = max(total, 1)
		segments = max(0, width)
		ratio = count / total if total else 0.0
		filled = max(0, min(segments, round(ratio * segments)))

		bar = Text(label, style=color)
		bar.append(" [")
		for idx in range(segments):
			char = "=" if idx < filled else " "
			segment = Text(char)
			if idx < filled:
				segment.stylize(color)
			bar.append_text(segment)
		bar.append("]")
		return bar

	def render_ratio_bar_and_stat(
		self,
		*,
		label: str,
		count: int,
		total: int,
		level: int | None = None,
		color: str | None = None,
		width: int | None = None,
	) -> None:
		"""Build and print a ratio bar with right-aligned stats in one call.

		- color: optional explicit color; otherwise picked from level mapping if provided.
		- width: optional terminal width; defaults to console width.
		- segments: automatically computed as (width - 30), clamped to >= 0.
		"""
		# Default color mapping for levels 1..5
		default_colors = {1: "red", 2: "orange3", 3: "yellow3", 4: "green", 5: "green"}
		use_color = color or (default_colors.get(level, "green") if level is not None else "green")

		line = self.build_ratio_bar_and_stat(
			label=label, count=count, total=total, level=level, color=use_color, width=width
		)
		self.render_columns(left=line)

	def build_ratio_bar_and_stat(
		self,
		*,
		label: str,
		count: int,
		total: int,
		level: int | None = None,
		color: str | None = None,
		width: int | None = None,
	) -> Text:
		"""Build a single-line Text containing the ratio bar on the left and stats on the right.

		Returns a Text object so callers can embed it (e.g., inside a Panel) without immediate printing.
		"""
		default_colors = {1: "red", 2: "orange3", 3: "yellow3", 4: "green", 5: "green"}
		use_color = color or (default_colors.get(level, "green") if level is not None else "green")
		term_width = width if width is not None else (self.console.width or 80)
		segments = max(0, term_width - 30)

		bar = self.build_ratio_bar(label=label, count=count, total=total, width=segments, color=use_color)
		percent = (count / max(total, 1) * 100.0) if total else 0.0
		if percent == 100.0:
			stats = Text(f"{count}/{total if total else 1} ( ALL )")
		else:
			stats = Text(f"{count}/{total if total else 1} ({percent:04.1f}%)")
		# Compose a single row with left bar and right stats
		row = self.compose_column_row(bar, Text(""), stats, term_width)
		return row

