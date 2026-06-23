from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HistoryConfig:
    repo_root: Path
    compiler_path: Path
    codex_home: Path
    omp_agent_dir: Path
    t3code_home: Path
    reasonix_home: Path
    cache_dir: Path
    t3code_db_override: str | None

    @classmethod
    def from_env(cls) -> "HistoryConfig":
        repo_root = _repo_root()
        compiler_path = _compiler_path(repo_root)
        return cls(
            repo_root=repo_root,
            compiler_path=compiler_path,
            codex_home=Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser(),
            omp_agent_dir=Path(os.environ.get("PI_CODING_AGENT_DIR", "~/.omp/agent")).expanduser(),
            t3code_home=Path(os.environ.get("T3CODE_HOME", "~/.t3")).expanduser(),
            reasonix_home=Path(os.environ.get("REASONIX_HOME", "~/.reasonix")).expanduser(),
            cache_dir=Path(os.environ.get("VCC_CACHE_DIR", "~/.cache/vcc-history")).expanduser(),
            t3code_db_override=os.environ.get("VCC_T3CODE_DB"),
        )


def _repo_root() -> Path:
    override = os.environ.get("VCC_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def _compiler_path(repo_root: Path) -> Path:
    override = os.environ.get("VCC_COMPILER_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root / "skills" / "conversation-compiler" / "scripts" / "VCC.py"
