from __future__ import annotations

import os
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List

from config import SETTINGS

def _is_probably_binary(b: bytes) -> bool:
    return b"\x00" in b[:2048]

def _safe_read_text(p: Path) -> Optional[str]:
    try:
        b = p.read_bytes()
        if _is_probably_binary(b):
            return None
        return b.decode("utf-8", errors="replace")
    except Exception:
        return None

def _sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8", errors="replace")).hexdigest()

def scan_repo(repo_root: Path) -> Dict[str, Any]:
    root = repo_root.resolve()
    files: List[Dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SETTINGS.IGNORE_DIRS]

        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            ext = p.suffix.lower()

            if ext not in SETTINGS.TEXT_EXT:
                continue

            try:
                st = p.stat()
                if st.st_size > SETTINGS.MAX_FILE_MB * 1024 * 1024:
                    continue

                text = _safe_read_text(p)
                if text is None:
                    continue

                files.append({
                    "path": rel,
                    "ext": ext,
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                    "lines": int(text.count("\n") + 1),
                    "sha256": _sha256_text(text),
                })
            except Exception:
                continue

    files.sort(key=lambda x: x["path"])
    return {
        "repo_root": str(root),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "files": files,
    }
