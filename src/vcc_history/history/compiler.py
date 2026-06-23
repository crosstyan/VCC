from __future__ import annotations

import importlib.util
from types import ModuleType
from typing import cast

from .config import HistoryConfig
from .errors import HistoryError

_VCC_MODULE: ModuleType | None = None


def reset_compiler_cache() -> None:
    global _VCC_MODULE
    _VCC_MODULE = None


def load_compiler(config: HistoryConfig | None = None) -> ModuleType:
    global _VCC_MODULE
    if _VCC_MODULE is not None:
        return _VCC_MODULE
    resolved = config or HistoryConfig.from_env()
    path = resolved.compiler_path
    spec = importlib.util.spec_from_file_location("vcc_legacy_compiler", path)
    if spec is None or spec.loader is None:
        raise HistoryError(f"Could not load VCC compiler from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _VCC_MODULE = module
    return module


def compile_pass(
    path: str,
    *,
    output_dir: str | None = None,
    grep_pattern: object | None = None,
    quiet: bool = False,
    config: HistoryConfig | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    result = load_compiler(config).compile_pass(
        path,
        output_dir=output_dir,
        grep_pattern=grep_pattern,
        quiet=quiet,
    )
    return cast(list[tuple[str, list[dict[str, object]]]], result)
