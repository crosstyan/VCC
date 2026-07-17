from __future__ import annotations

import json
from typing import Mapping


def _emit_mapping(data: Mapping[str, object], indent: int = 0) -> str:
    parts = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, Mapping):
            parts.append(prefix + str(key) + ":")
            parts.append(_emit_mapping(value, indent + 2))
        elif isinstance(value, (list, tuple)):
            parts.append(prefix + str(key) + ":")
            for item in value:
                if isinstance(item, Mapping):
                    parts.append(prefix + "  -")
                    parts.append(_emit_mapping(item, indent + 4))
                else:
                    parts.append(prefix + "  - " + str(item))
        else:
            text = str(value)
            if "\n" in text:
                parts.append(prefix + str(key) + ": |")
                child_prefix = " " * (indent + 2)
                for line in text.split("\n"):
                    parts.append((child_prefix + line) if line else "")
            else:
                parts.append(prefix + str(key) + ": " + text)
    return "\n".join(parts)


def structured_text(value: object) -> str:
    if value in (None, "", {}, []):
        return ""
    if isinstance(value, Mapping):
        return _emit_mapping(value)
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                return value
            if isinstance(parsed, Mapping):
                return _emit_mapping(parsed)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False, indent=2)
        return value
    return str(value)
