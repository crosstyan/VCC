from __future__ import annotations

from typing import cast

from . import vcc


def compile_pass(
    path: str,
    *,
    output_dir: str | None = None,
    quiet: bool = False,
    harness: str | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    result = vcc.compile_pass(
        path,
        output_dir=output_dir,
        quiet=quiet,
        harness=harness,
    )
    return cast(list[tuple[str, list[dict[str, object]]]], result)
