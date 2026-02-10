from __future__ import annotations

import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import SETTINGS

def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")

def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

def _git_is_clean(repo_root: Path) -> bool:
    try:
        p = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True, text=True)
        if p.returncode != 0:
            return True
        return p.stdout.strip() == ""
    except Exception:
        return True

def _backup_file(repo_root: Path, rel: str, backup_dir: Path) -> None:
    src = repo_root / rel
    dst = backup_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

def _replace_range(text: str, start_line: int, end_line: int, new_text: str) -> str:
    lines = text.splitlines(True)
    s = max(1, start_line) - 1
    e = min(len(lines), end_line)
    return "".join(lines[:s]) + new_text + "".join(lines[e:])

def _replace_text(text: str, find: str, replace: str, count: Optional[int]) -> str:
    return text.replace(find, replace) if count is None else text.replace(find, replace, count)

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

def apply_patch_plan(repo_root: Path, plan: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    if SETTINGS.REQUIRE_CLEAN_GIT and not _git_is_clean(repo_root):
        raise RuntimeError("Repo has uncommitted changes. Commit/stash or set REQUIRE_CLEAN_GIT=False.")

    state_dir = repo_root / SETTINGS.STATE_DIR
    backup_root = repo_root / SETTINGS.BACKUP_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)

    run_id = _ts()
    backup_dir = backup_root / run_id
    backup_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for f in plan.get("files", []):
        rel = f.get("path", "")
        ops = f.get("ops", [])
        p = repo_root / rel

        if not rel:
            results.append({"file": "", "status": "skipped", "reason": "missing path"})
            continue

        if not p.exists():
            results.append({"file": rel, "status": "skipped", "reason": "not found"})
            continue

        original = p.read_text(encoding="utf-8", errors="replace")
        updated = original

        try:
            for op in ops:
                t = op["type"]
                if t == "replace_range":
                    updated = _replace_range(updated, int(op["start_line"]), int(op["end_line"]), str(op["new_text"]))
                elif t == "replace_text":
                    updated = _replace_text(updated, str(op["find"]), str(op["replace"]), op.get("count"))
                elif t == "insert_after":
                    updated = _insert_after(updated, str(op["match"]), str(op["insert_text"]), bool(op.get("once", True)))
                else:
                    raise ValueError(f"Unknown op type: {t}")
        except Exception as e:
            results.append({"file": rel, "status": "failed", "reason": str(e)})
            continue

        if updated == original:
            results.append({"file": rel, "status": "noop", "ops": len(ops)})
            continue

        if dry_run:
            results.append({"file": rel, "status": "would_update", "ops": len(ops)})
        else:
            _backup_file(repo_root, rel, backup_dir)
            _atomic_write(p, updated)
            results.append({"file": rel, "status": "updated", "ops": len(ops)})

    log = {
        "run_id": run_id,
        "dry_run": dry_run,
        "backup_dir": str(backup_dir),
        "results": results
    }

    (state_dir / "last_apply_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    return log
