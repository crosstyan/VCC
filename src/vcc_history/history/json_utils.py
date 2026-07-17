from __future__ import annotations

import json
from pathlib import Path

from .errors import warn


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def json_or_none(value: object) -> object | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def read_first_json(path: str) -> dict[str, object] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except (ValueError, TypeError) as error:
                    warn(f"{path}:{line_number}", f"malformed JSON record: {error}")
                    return None
                if not isinstance(value, dict):
                    warn(f"{path}:{line_number}", "expected a JSON object")
                    return None
                return value
    except OSError as error:
        warn(path, error)
    return None


def read_json_file(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as error:
        warn(path, f"could not read JSON: {error}")
        return {}
    if not isinstance(value, dict):
        warn(path, "expected a JSON object")
        return {}
    return value


def split_model_ref(model: str | None) -> tuple[str | None, str | None]:
    if not model:
        return None, None
    if "/" in model:
        provider, model_id = model.split("/", 1)
        return provider, model_id
    return None, model


def nested_get(value: object, keys: tuple[str, ...]) -> object | None:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
