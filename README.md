# vonnegut

Full-screen terminal prose editor with a cursor-following AI agent. The whole
screen is your markdown. Hit a shortcut and an agent panel opens at the cursor;
its edits land in the focused tab automatically.

## Run

```sh
export OPENAI_API_KEY=sk-...
uv run vonnegut               # edits ./draft.md
uv run vonnegut notes.md      # edits ./notes.md
uv run vonnegut -d ~/writing chapter1.md
uv run vonnegut -t dracula draft.md
```

## Keys

- `Ctrl+G` — open the agent panel at the cursor. Pick a scope (Document /
  Selection / Line), type a request, `Enter`. The dialog closes and the agent's
  revision streams directly into that range, the changed lines highlighted as
  text arrives. Scope defaults to Selection when text is highlighted, else Line.
- `Esc` — close the panel
- `Ctrl+S` — save the open file
- `Ctrl+T` — cycle syntax-highlight theme
- `Ctrl+Q` — quit
- Tabs (top) — one per `.md` file in the working directory; click to switch.
  Switching auto-saves. New files the agent creates appear as tabs.

## Agent

Tools: `list_files`, `read_file`, `write_file`, `apply_to_current`. Prose edits
go through `apply_to_current`, which overwrites the focused tab; the editor
reloads to show them. Model override: `VONNEGUT_MODEL=openai:gpt-4o-mini`.
Theme override: `VONNEGUT_THEME=dracula`.
