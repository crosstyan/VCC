from __future__ import annotations

from typing import cast

from . import vcc


def compile_pass(
    path: str,
    *,
    output_dir: str | None = None,
    grep_pattern: object | None = None,
    quiet: bool = False,
) -> list[tuple[str, list[dict[str, object]]]]:
    result = vcc.compile_pass(
        path,
        output_dir=output_dir,
        grep_pattern=grep_pattern,
        quiet=quiet,
    )
    return cast(list[tuple[str, list[dict[str, object]]]], result)
