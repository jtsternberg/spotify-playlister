from __future__ import annotations

import os
from pathlib import Path


def load_env(path: Path | None = None) -> None:
    if path is None:
        # Prefer a .env in the current directory, then fall back to the one
        # alongside the project so the CLI works from any working directory.
        cwd_env = Path(".env")
        repo_env = Path(__file__).resolve().parent.parent / ".env"
        path = cwd_env if cwd_env.exists() else repo_env

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue

        key = key.strip()
        if not key or key in os.environ:
            continue

        os.environ[key] = _clean_value(value.strip())


def _clean_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
