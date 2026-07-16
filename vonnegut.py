"""vonnegut: full-screen TUI prose editor with a scope-laddering AI agent.

The whole screen is prose.
  Ctrl+D          grow the edit scope: cursor → word → sentence → paragraph
                  → section → document (clamps at document). The scope is
                  highlighted and the cursor locks while a scope is active.
  Ctrl+E          shrink the scope one rung (back to cursor unlocks).
  Ctrl+G          open the instruction box for the current scope (or the whole
                  document if none). Type + Enter → the agent's revision streams
                  in place over that range. ↑/↓ recall past instructions.
                  When it lands the edit is PENDING: Enter accepts, Esc reverts.
  Ctrl+R          retry: rerun the last instruction on the same original span.
  Ctrl+Z          undo the last accepted agent edit in one step.
  Esc             cancel: reject a pending edit / close the box / collapse.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pydantic_ai import Agent
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Static, Tab, Tabs, TextArea
from textual.widgets.text_area import Selection

MODEL = os.environ.get("VONNEGUT_MODEL", "openai:gpt-4o")
THEMES = ["monokai", "dracula", "vscode_dark", "github_light", "css"]
LEVELS = ["Word", "Sentence", "Paragraph", "Section", "Document"]

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


def _loc_to_offset(text: str, loc) -> int:
    row, col = loc
    lines = text.split("\n")
    return sum(len(l) + 1 for l in lines[:row]) + col


def _offset_to_loc(text: str, off: int):
    off = max(0, min(off, len(text)))
    lines = text.split("\n")
    row = 0
    for line in lines:
        if off <= len(line):
            return (row, off)
        off -= len(line) + 1
        row += 1
    return (len(lines) - 1, len(lines[-1]))


def _advance(start, text: str):
    """End Location after inserting `text` at `start`."""
    lines = text.split("\n")
    if len(lines) == 1:
        return (start[0], start[1] + len(lines[0]))
    return (start[0] + len(lines) - 1, len(lines[-1]))


# --- scope ladder ------------------------------------------------------------

def _is_word(c: str) -> bool:
    return c.isalnum() or c in "'-_"


def _word_range(text: str, off: int):
    n = len(text)
    if n == 0:
        return 0, 0
    p = min(off, n - 1)
    if not _is_word(text[p]):  # snap onto the nearest word char
        if off > 0 and _is_word(text[off - 1]):
            p = off - 1
        else:
            q = off
            while q < n and not _is_word(text[q]):
                q += 1
            p = q if q < n else max(0, off - 1)
    s = e = min(p, n)
    while s > 0 and _is_word(text[s - 1]):
        s -= 1
    while e < n and _is_word(text[e]):
        e += 1
    return s, e


def _paragraph_range(text: str, off: int):
    n = len(text)
    s = text.rfind("\n\n", 0, off)
    s = 0 if s < 0 else s + 2
    e = text.find("\n\n", off)
    return s, (n if e < 0 else e)


def _sentence_range(text: str, off: int):
    ps, pe = _paragraph_range(text, off)  # sentences never cross paragraphs
    s = ps
    for i in range(min(off, pe) - 1, ps - 1, -1):
        if text[i] in ".!?":
            s = i + 1
            break
    while s < pe and text[s] in " \t\n":
        s += 1
    e = pe
    for i in range(max(off, s), pe):
        if text[i] in ".!?":
            e = i + 1
            break
    return s, max(s, e)


def _heading_level(line: str) -> int:
    st = line.lstrip()
    return len(st) - len(st.lstrip("#")) if st.startswith("#") else 0


def _section_range(text: str, off: int):
    lines = text.split("\n")
    starts, acc = [], 0
    for l in lines:
        starts.append(acc)
        acc += len(l) + 1
    row = _offset_to_loc(text, off)[0]
    h, hl = None, 0
    for i in range(row, -1, -1):
        lv = _heading_level(lines[i])
        if lv:
            h, hl = i, lv
            break
    start = starts[h] if h is not None else 0
    start_row = h if h is not None else 0
    end = len(text)
    for i in range(start_row + 1, len(lines)):
        lv = _heading_level(lines[i])
        if lv and hl and lv <= hl:
            end = starts[i] - 1  # stop before the newline preceding the next heading
            break
    return start, max(start, end)


def _level_range(text: str, off: int, level: str):
    if level == "Document":
        return 0, len(text)
    return {
        "Word": _word_range,
        "Sentence": _sentence_range,
        "Paragraph": _paragraph_range,
        "Section": _section_range,
    }[level](text, off)


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
        max-height: 8;
        padding: 0 1;
        background: $panel;
        border: round $accent;
    }
    #agentpanel.-visible { display: block; }
    #target { height: 1; color: $text-muted; }
    """
    BINDINGS = [
        Binding("ctrl+g", "ask", "Ask agent", priority=True),
        Binding("ctrl+d", "grow", "Grow scope", priority=True),
        Binding("ctrl+e", "shrink", "Shrink scope", priority=True),
        Binding("ctrl+r", "retry", "Retry edit", priority=True),
        Binding("ctrl+z", "undo_edit", "Undo edit", priority=True),
        Binding("enter", "accept_edit", "Accept", show=False),
        ("escape", "cancel", "Cancel"),
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
        self.level = -1  # -1 = no scope (cursor); else index into LEVELS
        self.anchor = 0  # char offset the ladder expands around
        self.sel: tuple[int, int] | None = None  # active scope range (offsets)
        self.edit_range: tuple[int, int] = (0, 0)
        self.pending = False  # a streamed edit is awaiting accept/reject
        self.snapshot: str | None = None  # full doc before the last agent edit
        self.snap_cursor = 0  # cursor offset to restore alongside snapshot
        self.last_edit: tuple[str, int, int] | None = None  # (prompt, s, e) for retry
        self.history: list[str] = []  # past instructions, oldest first
        self.hist_idx = 0  # cursor into history (== len == "new/blank")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(*(Tab(p.name, id=tid) for tid, p in self.tabmap.items()), id="tabs")
        text = self.file.read_text() if self.file.exists() else ""
        yield TextArea.code_editor(
            text, language="markdown", theme=self.editor_theme, id="editor"
        )
        with Vertical(id="agentpanel"):
            yield Static(id="target")
            yield Input(placeholder="Instruction…", id="ask")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "vonnegut"
        self.sub_title = self.file.name
        self.query_one("#editor", TextArea).focus()

    @property
    def editor(self) -> TextArea:
        return self.query_one("#editor", TextArea)

    # --- files / tabs ---

    def action_save(self) -> None:
        self.file.write_text(self.editor.text)
        self.notify(f"saved {self.file.name}")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tab is None:
            return
        target = self.tabmap.get(event.tab.id)
        if target is None or target == self.file:
            return
        try:
            editor = self.editor
        except Exception:
            return  # fired before the editor mounted
        self.file.write_text(editor.text)  # auto-save on switch
        self.file = target
        editor.load_text(target.read_text() if target.exists() else "")
        self._collapse()
        self.sub_title = target.name

    def action_theme(self) -> None:
        editor = self.editor
        i = THEMES.index(editor.theme) + 1 if editor.theme in THEMES else 0
        editor.theme = THEMES[i % len(THEMES)]
        self.notify(f"theme: {editor.theme}")

    # --- scope ladder ---

    def action_grow(self) -> None:
        if self.level < 0:  # engage: anchor at the current cursor
            self.anchor = _loc_to_offset(self.editor.text, self.editor.cursor_location)
            self.level = 0
        else:
            self.level = min(self.level + 1, len(LEVELS) - 1)
        self._apply_scope()

    def action_shrink(self) -> None:
        if self.level <= 0:
            self._collapse()
        else:
            self.level -= 1
            self._apply_scope()

    def _apply_scope(self) -> None:
        editor = self.editor
        s, e = _level_range(editor.text, self.anchor, LEVELS[self.level])
        self.sel = (s, e)
        editor.selection = Selection(
            _offset_to_loc(editor.text, s), _offset_to_loc(editor.text, e)
        )
        self.set_focus(None)  # lock the cursor while a scope is active
        self.sub_title = f"{self.file.name} — {LEVELS[self.level]} ({e - s})"

    def _collapse(self) -> None:
        editor = self.editor
        self.level, self.sel = -1, None
        cur = _offset_to_loc(editor.text, self.anchor)
        editor.selection = Selection(cur, cur)
        editor.focus()  # unlock
        self.sub_title = self.file.name

    # --- agent panel ---

    def action_ask(self) -> None:
        editor = self.editor
        if self.sel is not None:
            s, e = self.sel
            label = f"{LEVELS[self.level]} · {e - s} chars"
        else:
            s, e = 0, len(editor.text)
            label = f"whole document · {e} chars"
        self.edit_range = (s, e)
        panel = self.query_one("#agentpanel")
        off = editor.cursor_screen_offset
        panel.styles.offset = (
            min(off.x, max(0, self.size.width - 62)),
            min(off.y + 1, max(0, self.size.height - 8)),
        )
        self.query_one("#target", Static).update(f"editing: {label}")
        self.hist_idx = len(self.history)  # ↑ starts from the newest instruction
        panel.add_class("-visible")
        self.query_one("#ask", Input).focus()

    def action_cancel(self) -> None:
        if self.pending:  # reject the streamed edit, restore the original text
            self._restore_snapshot()
            self.notify("reverted")
            return
        self.query_one("#agentpanel").remove_class("-visible")
        if self.level >= 0:
            self._collapse()
        else:
            self.editor.focus()

    def action_accept_edit(self) -> None:
        if not self.pending:
            return
        self.pending = False  # keep the text; snapshot stays for a later Ctrl+Z
        self._collapse()
        self.notify("accepted · Ctrl+Z to undo")

    def action_undo_edit(self) -> None:
        if self.snapshot is not None:  # one-step undo of the last agent edit
            self._restore_snapshot()
            self.notify("edit undone")
        else:  # no agent edit outstanding: fall back to the editor's own undo
            self.editor.undo()

    def action_retry(self) -> None:
        if self.last_edit is None:
            self.notify("nothing to retry")
            return
        if self.snapshot is not None:  # rerun on the original span, not the revision
            self.editor.load_text(self.snapshot)
        prompt, s, e = self.last_edit
        self.run_edit(prompt, s, e)

    def _restore_snapshot(self) -> None:
        editor = self.editor
        if self.snapshot is not None:
            editor.load_text(self.snapshot)
        self.snapshot = None
        self.pending = False
        self.anchor = self.snap_cursor
        self._collapse()

    def _stales_snapshot(self, event) -> bool:
        # Manual typing after an accepted edit stales the snapshot.
        if self.pending or self.snapshot is None:
            return False
        if self.focused is not self.editor:
            return False
        return event.character is not None or event.key in ("backspace", "delete")

    def _navigate_history(self, event) -> None:
        if event.key == "up" and self.hist_idx > 0:
            self.hist_idx -= 1
        elif event.key == "down" and self.hist_idx < len(self.history):
            self.hist_idx += 1
        else:
            return
        val = self.history[self.hist_idx] if self.hist_idx < len(self.history) else ""
        self.query_one("#ask", Input).value = val
        event.stop()

    def on_key(self, event) -> None:
        # Drop a stale snapshot so Ctrl+Z reverts to the editor's own history,
        # not the pre-agent state.
        if self._stales_snapshot(event):
            self.snapshot = None
        if self.focused is self.query_one("#ask", Input):
            self._navigate_history(event)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        if not self.history or self.history[-1] != prompt:
            self.history.append(prompt)
        self.hist_idx = len(self.history)
        self.query_one("#agentpanel").remove_class("-visible")
        self.run_edit(prompt, *self.edit_range)

    @work(exclusive=True)
    async def run_edit(self, prompt: str, s_off: int, e_off: int) -> None:
        editor = self.editor
        text = editor.text
        self.snapshot = text  # full doc before the edit → accept/reject/undo pivot
        self.snap_cursor = s_off
        self.last_edit = (prompt, s_off, e_off)
        start = _offset_to_loc(text, s_off)
        end = _offset_to_loc(text, e_off)
        target = editor.get_text_range(start, end)
        context = (
            f"<document>\n{text}\n</document>\n\n"
            f"<target>\n{target}\n</target>\n\nRequest: {prompt}"
        )
        cur_end = end
        try:
            async with agent.run_stream(context) as result:
                # Cumulative text: rewrite the range in place and highlight it live.
                async for chunk in result.stream_text(debounce_by=0.1):
                    editor.replace(chunk, start, cur_end, maintain_selection_offset=False)
                    cur_end = _advance(start, chunk)
                    last_line = editor.document.get_line(cur_end[0])
                    editor.selection = Selection((start[0], 0), (cur_end[0], len(last_line)))
        except Exception as exc:  # surface API/config errors instead of dying silently
            self.notify(f"agent error: {exc}", severity="error")
            self._restore_snapshot()
            return
        # The revision is in place but PENDING: Enter accepts, Esc/Ctrl+Z reverts.
        self.pending = True
        self.anchor = _loc_to_offset(editor.text, cur_end)
        self.set_focus(None)  # keep the cursor locked so accept/reject keys reach us
        self.sub_title = f"{self.file.name} — pending · Enter accept · Esc revert"


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
