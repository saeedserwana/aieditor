from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set

@dataclass
class Settings:
    # ===== THE REPO YOU WANT TO EDIT (your project) =====
    # Example: r"C:\Users\Sa'eed\Desktop\my_project"
    REPO_ROOT: str = r"C:\Users\Sa'eed\Desktop\acttech"

    # ===== OPENAI =====
    # Put your key here OR leave empty and set environment variable OPENAI_API_KEY
    OPENAI_API_KEY: str = ""
    MODEL: str = "gpt-5"

    # ===== WEB UI =====
    WEB_HOST: str = "127.0.0.1"
    WEB_PORT: int = 8787
    # Optional: if non-empty, UI requires token (paste in UI box)
    ADMIN_TOKEN: str = ""

    # ===== SAFETY / LIMITS =====
    REQUIRE_CLEAN_GIT: bool = False
    MAX_FILES_TO_SHOW: int = 10
    MAX_CHARS_PER_FILE: int = 6000
    MAX_TOTAL_CONTEXT_CHARS: int = 26000

    # ===== SCAN RULES =====
    IGNORE_DIRS: Set[str] = field(default_factory=lambda: {
        ".git", "node_modules", ".next", "dist", "build",
        "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
        ".autoupdater_state", ".autoupdater_backups"
    })

    TEXT_EXT: Set[str] = field(default_factory=lambda: {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yml", ".yaml",
        ".md", ".txt", ".html", ".css", ".toml", ".ini"
    })

    MAX_FILE_MB: int = 2

    # ===== OUTPUT DIRS (created inside REPO_ROOT) =====
    STATE_DIR: str = ".autoupdater_state"
    BACKUP_DIR: str = ".autoupdater_backups"

SETTINGS = Settings()
