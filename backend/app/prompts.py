from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[2] / "prompts"


def read_prompt(name: str) -> str:
    return (DIRECTORY / name).read_text(encoding="utf-8")
