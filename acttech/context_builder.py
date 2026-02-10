from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, List, Tuple

from config import SETTINGS

def _read_snippet(repo_root: Path, rel_path: str, max_chars: int) -> str:
    p = repo_root / rel_path
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if len(txt) <= max_chars:
        return txt
    half = max_chars // 2
    return txt[:half] + "\n\n... [snip] ...\n\n" + txt[-half:]

def choose_files_for_context(repo_map: Dict[str, Any], diff: Dict[str, Any], goal: str) -> List[str]:
    # 1) Start with modified files (most relevant)
    candidates = list(diff.get("modified", [])) + list(diff.get("added", []))

    # 2) If none, choose smaller files (often entrypoints/config)
    if not candidates:
        files = repo_map.get("files", [])
        files_sorted = sorted(files, key=lambda f: (f.get("lines", 10**9), f.get("path", "")))
        candidates = [f["path"] for f in files_sorted[:SETTINGS.MAX_FILES_TO_SHOW]]

    # 3) Heuristic bump: prioritize common entry files if present
    prefer = []
    common = [
        "main.py", "app.py", "server.py",
        "index.js", "index.ts", "app.ts", "app.js",
        "routes.py", "router.py", "api.py",
        "README.md",
    ]
    for f in candidates:
        if any(f.endswith(x) for x in common):
            prefer.append(f)

    merged = prefer + [c for c in candidates if c not in prefer]
    return merged[:SETTINGS.MAX_FILES_TO_SHOW]

def build_llm_context(repo_root: Path, repo_map: Dict[str, Any], diff: Dict[str, Any], goal: str) -> Tuple[str, List[str]]:
    chosen = choose_files_for_context(repo_map, diff, goal)

    # Keep a compact list of repo files (not too huge)
    all_paths = [f["path"] for f in repo_map.get("files", [])]
    short_list = all_paths[:300]

    parts: List[str] = []
    parts.append("You are updating a local code repo. You MUST propose a patch plan JSON only.")
    parts.append("\nREPO FILE LIST (first 300):\n" + "\n".join(short_list))
    parts.append("\nDIFF SUMMARY:\n" + str(diff))
    parts.append("\nIMPORTANT RULES:")
    parts.append("- Only modify files that exist in the repo.")
    parts.append("- Use minimal changes. Prefer replace_range/replace_text.")
    parts.append("- If uncertain, output {\"files\": []} (do not guess).")

    total = 0
    parts.append("\nFILE SNIPPETS:")
    for rel in chosen:
        snippet = _read_snippet(repo_root, rel, SETTINGS.MAX_CHARS_PER_FILE)
        chunk = f"\n--- FILE: {rel} ---\n{snippet}\n"
        if total + len(chunk) > SETTINGS.MAX_TOTAL_CONTEXT_CHARS:
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts), chosen
