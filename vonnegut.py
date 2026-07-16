"""vonnegut: split-pane TUI writing agent.

Left pane (67%): editable markdown. Right pane (33%): AI chat.
Agent has read/write file tools scoped to the working directory.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, RunContext
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, TextArea

MODEL = os.environ.get("VONNEGUT_MODEL", "openai:gpt-4o")

SYSTEM = (
    "You are Vonnegut, a writing assistant living beside a markdown editor. "
    "The human drafts in the left pane; you help in the right. "
    "Use read_file to see the current draft and write_file to save changes. "
    "Files are markdown in the working directory. Be concrete and terse."
)


@dataclass
class Deps:
    workdir: Path


agent = Agent(MODEL, deps_type=Deps, instructions=SYSTEM)


def _resolve(workdir: Path, name: str) -> Path:
    # ponytail: confine to workdir so the agent can't wander the filesystem.
    p = (workdir / name).resolve()
    if not str(p).startswith(str(workdir.resolve())):
        raise ValueError(f"path escapes working directory: {name}")
    if p.suffix != ".md":
        p = p.with_suffix(".md")
    return p


@agent.tool
def list_files(ctx: RunContext[Deps]) -> list[str]:
    """List markdown files in the working directory."""
    return sorted(p.name for p in ctx.deps.workdir.glob("*.md"))


@agent.tool
def read_file(ctx: RunContext[Deps], name: str) -> str:
    """Read a markdown file's contents."""
    p = _resolve(ctx.deps.workdir, name)
    return p.read_text() if p.exists() else ""


@agent.tool
def write_file(ctx: RunContext[Deps], name: str, content: str) -> str:
    """Write content to a markdown file, overwriting it."""
    p = _resolve(ctx.deps.workdir, name)
    p.write_text(content)
    return f"wrote {len(content)} chars to {p.name}"


class Vonnegut(App):
    CSS = """
    #editor { width: 67%; }
    #chat { width: 33%; border-left: solid $accent; }
    #log { height: 1fr; }
    Input { dock: bottom; }
    """
    BINDINGS = [("ctrl+s", "save", "Save"), ("ctrl+q", "quit", "Quit")]

    def __init__(self, workdir: Path, filename: str) -> None:
        super().__init__()
        self.workdir = workdir
        self.file = _resolve(workdir, filename)
        self.deps = Deps(workdir=workdir)
        self.history: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            text = self.file.read_text() if self.file.exists() else ""
            yield TextArea.code_editor(text, language="markdown", id="editor")
            with Vertical(id="chat"):
                yield RichLog(id="log", wrap=True, markup=True)
                yield Input(placeholder="Ask Vonnegut...")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "vonnegut"
        self.sub_title = str(self.file.name)
        self.query_one("#log", RichLog).write(
            f"[dim]Editing {self.file.name} in {self.workdir}. Ctrl+S saves.[/dim]"
        )

    def action_save(self) -> None:
        self.file.write_text(self.query_one("#editor", TextArea).text)
        self.query_one("#log", RichLog).write(f"[green]saved {self.file.name}[/green]")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        event.input.value = ""
        self.query_one("#log", RichLog).write(f"[bold cyan]you:[/bold cyan] {prompt}")
        self.ask(prompt)

    @work(exclusive=True)
    async def ask(self, prompt: str) -> None:
        log = self.query_one("#log", RichLog)
        try:
            result = await agent.run(prompt, deps=self.deps, message_history=self.history)
        except Exception as exc:  # surface API/config errors instead of dying silently
            log.write(f"[red]error:[/red] {exc}")
            return
        self.history = result.all_messages()
        log.write(f"[bold magenta]vonnegut:[/bold magenta] {result.output}")
        # Reload editor if the agent rewrote the open file.
        if self.file.exists():
            editor = self.query_one("#editor", TextArea)
            disk = self.file.read_text()
            if disk != editor.text:
                editor.load_text(disk)
                log.write(f"[dim]reloaded {self.file.name}[/dim]")


def main() -> None:
    ap = argparse.ArgumentParser(prog="vonnegut", description="TUI writing agent")
    ap.add_argument("file", nargs="?", default="draft.md", help="markdown file to edit")
    ap.add_argument("-d", "--dir", default=".", help="working directory for markdown files")
    args = ap.parse_args()

    workdir = Path(args.dir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("set OPENAI_API_KEY")
    Vonnegut(workdir, args.file).run()


if __name__ == "__main__":
    main()
