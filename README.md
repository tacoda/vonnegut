# vonnegut

Split-pane terminal writing agent. Left (67%): editable markdown. Right (33%): AI chat.
The agent can read/write markdown files in the working directory.

## Run

```sh
export OPENAI_API_KEY=sk-...
uv run vonnegut               # edits ./draft.md
uv run vonnegut notes.md      # edits ./notes.md
uv run vonnegut -d ~/writing chapter1.md
```

- `Ctrl+S` save the editor to disk
- `Ctrl+Q` quit
- Type in the right pane, `Enter` to ask. The agent reads/writes files via tools.
  If it rewrites the open file, the editor reloads.

Model override: `VONNEGUT_MODEL=openai:gpt-4o-mini`.
