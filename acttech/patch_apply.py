from __future__ import annotations

import difflib
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from config import SETTINGS


# ----------------------------
# Small utils
# ----------------------------

def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def _is_safe_rel_path(rel: str) -> bool:
    p = Path(rel)
    return rel and (not p.is_absolute()) and (".." not in p.parts)

def _git_is_clean(repo_root: Path) -> bool:
    try:
        p = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True
        )
        if p.returncode != 0:
            # If git isn't available or repo isn't a git repo, don't block by default
            return True
        return p.stdout.strip() == ""
    except Exception:
        return True

def _backup_file(repo_root: Path, rel: str, backup_dir: Path) -> None:
    src = repo_root / rel
    dst = backup_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

def _unified_diff(rel: str, before: str, after: str, context_lines: int = 3) -> str:
    a = before.splitlines(True)
    b = after.splitlines(True)
    diff = difflib.unified_diff(
        a, b,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        n=context_lines
    )
    return "".join(diff)

def _changed_line_count(before: str, after: str) -> int:
    return sum(1 for _ in difflib.ndiff(before.splitlines(), after.splitlines())
               if _.startswith("+ ") or _.startswith("- "))

def _ensure_text_allowed(path: Path) -> bool:
    return path.suffix.lower() in SETTINGS.TEXT_EXT


# ----------------------------
# Patch ops (more complete)
# ----------------------------

def _replace_range(text: str, start_line: int, end_line: int, new_text: str) -> str:
    lines = text.splitlines(True)
    s = max(1, start_line) - 1
    e = min(len(lines), max(1, end_line))
    return "".join(lines[:s]) + new_text + "".join(lines[e:])

def _delete_range(text: str, start_line: int, end_line: int) -> str:
    return _replace_range(text, start_line, end_line, "")

def _replace_text(text: str, find: str, replace: str, count: Optional[int]) -> str:
    return text.replace(find, replace) if count is None else text.replace(find, replace, int(count))

def _insert_after(text: str, match: str, insert_text: str, once: bool = True) -> str:
    lines = text.splitlines(True)
    out: List[str] = []
    inserted = False
    for ln in lines:
        out.append(ln)
        if match in ln and (not inserted or not once):
            out.append(insert_text)
            inserted = True
    return "".join(out)

def _insert_before(text: str, match: str, insert_text: str, once: bool = True) -> str:
    lines = text.splitlines(True)
    out: List[str] = []
    inserted = False
    for ln in lines:
        if match in ln and (not inserted or not once):
            out.append(insert_text)
            inserted = True
        out.append(ln)
    return "".join(out)

def _append_text(text: str, append_text: str) -> str:
    if not text.endswith("\n") and append_text and not append_text.startswith("\n"):
        return text + "\n" + append_text
    return text + append_text


# ----------------------------
# Main apply
# ----------------------------

def apply_patch_plan(repo_root: Path, plan: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    """
    Replit-like behavior:
    - validates paths
    - shows preview diff (unified)
    - supports extra ops (insert_before, append, delete_range)
    - writes backups on real apply
    - returns rich log for UI/terminal
    """
    if SETTINGS.REQUIRE_CLEAN_GIT and not _git_is_clean(repo_root):
        raise RuntimeError("Repo has uncommitted changes. Commit/stash or set REQUIRE_CLEAN_GIT=False.")

    state_dir = repo_root / SETTINGS.STATE_DIR
    backup_root = repo_root / SETTINGS.BACKUP_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)

    run_id = _ts()
    backup_dir = backup_root / run_id
    backup_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    files = plan.get("files", [])
    if not isinstance(files, list):
        files = []

    for f in files:
        rel = (f.get("path") or "").replace("\\", "/")
        ops = f.get("ops", [])
        if not isinstance(ops, list):
            ops = []

        if not rel:
            results.append({"file": "", "status": "skipped", "reason": "missing path"})
            continue

        if not _is_safe_rel_path(rel):
            results.append({"file": rel, "status": "skipped", "reason": "unsafe path"})
            continue

        p = repo_root / rel

        # Optional: allow create new file (off by default)
        allow_create = bool(getattr(SETTINGS, "ALLOW_CREATE_FILES", False))
        if not p.exists():
            if allow_create:
                # create empty baseline
                p.parent.mkdir(parents=True, exist_ok=True)
                original = ""
            else:
                results.append({"file": rel, "status": "skipped", "reason": "not found"})
                continue
        else:
            if not p.is_file():
                results.append({"file": rel, "status": "skipped", "reason": "not a file"})
                continue
            if not _ensure_text_allowed(p):
                results.append({"file": rel, "status": "skipped", "reason": "file type not allowed"})
                continue
            original = p.read_text(encoding="utf-8", errors="replace")

        updated = original
        op_log: List[Dict[str, Any]] = []

        try:
            for op in ops:
                t = op.get("type")
                if t == "replace_range":
                    updated = _replace_range(
                        updated,
                        int(op["start_line"]),
                        int(op["end_line"]),
                        str(op["new_text"])
                    )
                elif t == "delete_range":
                    updated = _delete_range(updated, int(op["start_line"]), int(op["end_line"]))
                elif t == "replace_text":
                    updated = _replace_text(updated, str(op["find"]), str(op["replace"]), op.get("count"))
                elif t == "insert_after":
                    updated = _insert_after(updated, str(op["match"]), str(op["insert_text"]), bool(op.get("once", True)))
                elif t == "insert_before":
                    updated = _insert_before(updated, str(op["match"]), str(op["insert_text"]), bool(op.get("once", True)))
                elif t == "append":
                    updated = _append_text(updated, str(op.get("text", "")))
                else:
                    raise ValueError(f"Unknown op type: {t}")

                op_log.append({"type": t, "ok": True})
        except Exception as e:
            results.append({"file": rel, "status": "failed", "reason": str(e)})
            continue

        if updated == original:
            results.append({"file": rel, "status": "noop", "ops": len(ops)})
            continue

        diff_txt = _unified_diff(rel, original, updated, context_lines=3)
        changed_lines = _changed_line_count(original, updated)

        if dry_run:
            results.append({
                "file": rel,
                "status": "would_update",
                "ops": len(ops),
                "changed_lines": changed_lines,
                "diff_unified": diff_txt,
            })
        else:
            if p.exists():
                _backup_file(repo_root, rel, backup_dir)
            _atomic_write(p, updated)
            results.append({
                "file": rel,
                "status": "updated",
                "ops": len(ops),
                "changed_lines": changed_lines,
                "diff_unified": diff_txt,
            })

    log = {
        "run_id": run_id,
        "dry_run": bool(dry_run),
        "backup_dir": str(backup_dir),
        "results": results
    }

    (state_dir / "last_apply_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log
