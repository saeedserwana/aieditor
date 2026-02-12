from __future__ import annotations

import os
import re
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List

from config import SETTINGS


# ----------------------------
# Detect / read helpers
# ----------------------------

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

def _lang_from_ext(ext: str) -> str:
    ext = (ext or "").lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".json": "json",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".md": "markdown",
        ".html": "html",
        ".css": "css",
        ".txt": "text",
        ".toml": "toml",
    }.get(ext, "text")

def _is_entrypoint(rel: str) -> bool:
    low = rel.lower().replace("\\", "/")
    # Classic entrypoints
    names = ("main.py", "app.py", "server.py", "web_app.py", "api.py", "index.js", "index.ts")
    if any(low.endswith(n) for n in names):
        return True
    # Typical “run” configs
    if any(x in low for x in ("requirements.txt", "pyproject.toml", "package.json", "dockerfile", "docker-compose")):
        return True
    return False

def _peek(text: str, head_chars: int = 2200, tail_chars: int = 1400) -> Dict[str, str]:
    if not text:
        return {"peek_head": "", "peek_tail": ""}
    if len(text) <= head_chars + tail_chars:
        return {"peek_head": text, "peek_tail": ""}
    return {
        "peek_head": text[:head_chars],
        "peek_tail": text[-tail_chars:]
    }


# ----------------------------
# Lightweight symbol extraction
# ----------------------------

_RE_PY_DEF = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_RE_PY_CLASS = re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\(|:)", re.MULTILINE)
_RE_FASTAPI_ROUTE = re.compile(r"^\s*@app\.(get|post|put|delete|patch|websocket)\(\s*['\"]([^'\"]+)['\"]", re.MULTILINE)

_RE_JS_EXPORT_FN = re.compile(r"export\s+(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_RE_JS_CLASS = re.compile(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\s*(\{|extends)", re.MULTILINE)

def _extract_symbols(text: str, lang: str, max_items: int = 40) -> List[Dict[str, str]]:
    """
    Minimal, fast symbol hints for IDE:
    - python: defs/classes + FastAPI routes
    - js/ts: exported functions + classes
    """
    if not text:
        return []
    out: List[Dict[str, str]] = []

    if lang == "python":
        for m in _RE_FASTAPI_ROUTE.finditer(text):
            out.append({"kind": "route", "name": f"{m.group(1).upper()} {m.group(2)}"})
            if len(out) >= max_items:
                return out
        for m in _RE_PY_CLASS.finditer(text):
            out.append({"kind": "class", "name": m.group(1)})
            if len(out) >= max_items:
                return out
        for m in _RE_PY_DEF.finditer(text):
            out.append({"kind": "def", "name": m.group(1)})
            if len(out) >= max_items:
                return out

    elif lang in ("javascript", "typescript"):
        for m in _RE_JS_EXPORT_FN.finditer(text):
            out.append({"kind": "export_fn", "name": m.group(1)})
            if len(out) >= max_items:
                return out
        for m in _RE_JS_CLASS.finditer(text):
            out.append({"kind": "class", "name": m.group(1)})
            if len(out) >= max_items:
                return out

    return out


# ----------------------------
# Main scan
# ----------------------------

def scan_repo(repo_root: Path) -> Dict[str, Any]:
    root = repo_root.resolve()
    files: List[Dict[str, Any]] = []

    # New optional tuning knobs (fallback defaults if not in config)
    max_file_mb = float(getattr(SETTINGS, "MAX_FILE_MB", 1.0))
    peek_head_chars = int(getattr(SETTINGS, "PEEK_HEAD_CHARS", 2200))
    peek_tail_chars = int(getattr(SETTINGS, "PEEK_TAIL_CHARS", 1400))
    enable_symbols = bool(getattr(SETTINGS, "ENABLE_SYMBOLS", True))

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
                if st.st_size > max_file_mb * 1024 * 1024:
                    continue

                text = _safe_read_text(p)
                if text is None:
                    continue

                lang = _lang_from_ext(ext)
                peek = _peek(text, head_chars=peek_head_chars, tail_chars=peek_tail_chars)

                file_obj: Dict[str, Any] = {
                    # original fields (backwards compatible)
                    "path": rel,
                    "ext": ext,
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                    "lines": int(text.count("\n") + 1),
                    "sha256": _sha256_text(text),

                    # new "Replit-like IDE intelligence"
                    "lang": lang,
                    "is_entrypoint": _is_entrypoint(rel),
                    "peek_head": peek["peek_head"],
                    "peek_tail": peek["peek_tail"],
                }

                if enable_symbols:
                    file_obj["symbols"] = _extract_symbols(text, lang)

                files.append(file_obj)

            except Exception:
                continue

    files.sort(key=lambda x: x["path"])

    # Summary metrics (nice for UI)
    entrypoints = [f["path"] for f in files if f.get("is_entrypoint")]
    langs = {}
    for f in files:
        langs[f.get("lang", "text")] = langs.get(f.get("lang", "text"), 0) + 1

    return {
        "repo_root": str(root),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "files": files,
        "summary": {
            "entrypoints": entrypoints[:20],
            "languages": langs,
        }
    }
