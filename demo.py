"""Deterministic demo of vonnegut — no API key, canned agent output.

Used to record the README screencast. Stubs the agent with a TestModel so the
streamed edit is fast and reproducible.
"""

import pathlib
import sys

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import vonnegut

DEMO_OUTPUT = (
    "He tumbles loose through his own life — powerless, blinking, a tourist "
    "among his own days."
)
vonnegut.agent = Agent(
    TestModel(custom_output_text=DEMO_OUTPUT), instructions=vonnegut.EDIT_SYSTEM
)

DRAFT = """# Slaughterhouse

Billy Pilgrim has come unstuck in time. He wanders.

So it goes.
"""

if __name__ == "__main__":
    d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    (d / "draft.md").write_text(DRAFT)
    vonnegut.Vonnegut(d, "draft.md", "dracula").run()
