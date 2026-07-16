"""vonnegut: full-screen TUI prose editor with a scope-aware AI agent.

The whole screen is prose. Ctrl+G opens an agent panel at the cursor. Pick a
scope — Document, Selection, or Line — type a request, and the agent's revision
replaces exactly that range in place.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pydantic_ai import Agent
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import (
    Footer,
    Header,
    Input,
    RadioButton,
    RadioSet,
    Tab,
    Tabs,
    TextArea,
)
from textual.widgets.text_area import Selection

MODEL = os.environ.get("VONNEGUT_MODEL", "openai:gpt-4o")
THEMES = ["monokai", "dracula", "vscode_dark", "github_light", "css"]
SCOPES = ["Document", "Selection", "Line"]

EDIT_SYSTEM = (
    "You are Vonnegut, a prose-editing assistant. Each request gives you the full "
    "document for context, a TARGET span, and an instruction. Return ONLY the "
    "revised replacement text for the TARGET — no commentary, no code fences, no "
    "surrounding quotes. Match the surrounding voice and markdown style."
)

agent = Agent(MODEL, instructions=EDIT_SYSTEM)


def _resolve(workdir: Path, name: str) -> Path:
    p = (workdir / name).resolve()
    if p.suffix != ".md":
        p = p.with_suffix(".md")
    return p


def _advance(start, text: str):
    """End Location after inserting `text` at `start`."""
    lines = text.split("\n")
    if len(lines) == 1:
        return (start[0], start[1] + len(lines[0]))
    return (start[0] + len(lines) - 1, len(lines[-1]))


def _scope_range(editor: TextArea, scope: str):
    """Return the (start, end) Locations the scope covers."""
    doc = editor.document
    if scope == "Document":
        last = doc.line_count - 1
        return (0, 0), (last, len(doc.get_line(last)))
    if scope == "Selection" and not editor.selection.is_empty:
        s, e = editor.selection.start, editor.selection.end
        return min(s, e), max(s, e)  # Locations are (row, col) tuples
    # Line — also the fallback when "Selection" is empty.
    row = editor.cursor_location[0]
    return (row, 0), (row, len(doc.get_line(row)))


class Vonnegut(App):
    CSS = """
    Screen { layers: base overlay; }
    #tabs { dock: top; }
    #editor { height: 1fr; }
    #agentpanel {
        layer: overlay;
        display: none;
        width: 62;
        height: auto;
        max-height: 20;
        padding: 0 1;
        background: $panel;
        border: round $accent;
    }
    #agentpanel.-visible { display: block; }
    #scope { height: auto; layout: horizontal; }
    """
    BINDINGS = [
        ("ctrl+g", "ask", "Ask agent"),
        ("escape", "close_panel", "Close panel"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+t", "theme", "Theme"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, workdir: Path, filename: str, theme_name: str) -> None:
        super().__init__()
        self.workdir = workdir
        self.file = _resolve(workdir, filename)
        self.editor_theme = theme_name
        others = [p for p in sorted(workdir.glob("*.md")) if p != self.file]
        self.tabmap = {f"f{i}": p for i, p in enumerate([self.file, *others])}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(*(Tab(p.name, id=tid) for tid, p in self.tabmap.items()), id="tabs")
        text = self.file.read_text() if self.file.exists() else ""
        yield TextArea.code_editor(
            text, language="markdown", theme=self.editor_theme, id="editor"
        )
        with Vertical(id="agentpanel"):
            with RadioSet(id="scope"):
                yield RadioButton("Document")
                yield RadioButton("Selection")
                yield RadioButton("Line", value=True)
            yield Input(placeholder="Instruction…", id="ask")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "vonnegut"
        self.sub_title = self.file.name
        self.query_one("#editor", TextArea).focus()

    # --- files / tabs ---

    def action_save(self) -> None:
        self.file.write_text(self.query_one("#editor", TextArea).text)
        self.notify(f"saved {self.file.name}")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        target = self.tabmap.get(event.tab.id)
        if target is None or target == self.file:
            return
        try:
            editor = self.query_one("#editor", TextArea)
        except Exception:
            return  # fired before the editor mounted
        self.file.write_text(editor.text)  # auto-save on switch
        self.file = target
        editor.load_text(target.read_text() if target.exists() else "")
        self.sub_title = target.name

    def action_theme(self) -> None:
        editor = self.query_one("#editor", TextArea)
        i = THEMES.index(editor.theme) + 1 if editor.theme in THEMES else 0
        editor.theme = THEMES[i % len(THEMES)]
        self.notify(f"theme: {editor.theme}")

    # --- agent panel (follows the cursor, scoped edits) ---

    def action_ask(self) -> None:
        editor = self.query_one("#editor", TextArea)
        panel = self.query_one("#agentpanel")
        off = editor.cursor_screen_offset
        x = min(off.x, max(0, self.size.width - 62))
        y = min(off.y + 1, max(0, self.size.height - 20))
        panel.styles.offset = (x, y)
        panel.add_class("-visible")
        # Default the scope to Selection if there's a highlight, else Line.
        buttons = list(self.query_one("#scope", RadioSet).query(RadioButton))
        buttons[1 if not editor.selection.is_empty else 2].value = True
        self.query_one("#ask", Input).focus()

    def action_close_panel(self) -> None:
        self.query_one("#agentpanel").remove_class("-visible")
        self.query_one("#editor", TextArea).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        idx = self.query_one("#scope", RadioSet).pressed_index
        self.action_close_panel()  # close dialog immediately
        self.edit_scope(prompt, SCOPES[idx if idx >= 0 else 2])

    @work(exclusive=True)
    async def edit_scope(self, prompt: str, scope: str) -> None:
        editor = self.query_one("#editor", TextArea)
        start, end = _scope_range(editor, scope)
        target = editor.get_text_range(start, end)
        context = (
            f"<document>\n{editor.text}\n</document>\n\n"
            f"<target scope={scope.lower()!r}>\n{target}\n</target>\n\n"
            f"Request: {prompt}"
        )
        cur_end = end
        try:
            async with agent.run_stream(context) as result:
                # Cumulative text: rewrite the range in place and highlight it live.
                async for text in result.stream_text(debounce_by=0.1):
                    editor.replace(text, start, cur_end, maintain_selection_offset=False)
                    cur_end = _advance(start, text)
                    last_line = editor.document.get_line(cur_end[0])
                    editor.selection = Selection((start[0], 0), (cur_end[0], len(last_line)))
        except Exception as exc:  # surface API/config errors instead of dying silently
            self.notify(f"agent error: {exc}", severity="error")


def main() -> None:
    ap = argparse.ArgumentParser(prog="vonnegut", description="TUI prose editor with AI agent")
    ap.add_argument("file", nargs="?", default="draft.md", help="markdown file to edit")
    ap.add_argument("-d", "--dir", default=".", help="working directory for markdown files")
    ap.add_argument(
        "-t",
        "--theme",
        default=os.environ.get("VONNEGUT_THEME", "monokai"),
        choices=THEMES,
        help="editor syntax-highlight theme",
    )
    args = ap.parse_args()

    workdir = Path(args.dir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("set OPENAI_API_KEY")
    Vonnegut(workdir, args.file, args.theme).run()


if __name__ == "__main__":
    main()
