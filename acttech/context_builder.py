# context_builder.py (REPLIT-LIKE++ IMPROVED)
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set, Optional

from config import SETTINGS

# ----------------------------
# Heuristics / constants
# ----------------------------

_STOP = {
    "a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "with", "by", "as",
    "is", "are", "was", "were", "be", "been", "it", "this", "that", "these", "those",
    "from", "at", "into", "over", "under", "then", "than", "but", "if", "else",
    "add", "make", "update", "fix", "improve", "refactor", "change", "create", "build",
    "please", "need", "want", "like", "similar", "replit", "offline",
}

# Strong “entrypoint / control plane” hints (prefer these)
_ENTRYPOINT_HINTS = [
    # Python app entrypoints
    "main.py", "app.py", "server.py", "web_app.py", "api.py", "routes.py", "router.py",
    "wsgi.py", "asgi.py", "manage.py",
    # JS/TS entrypoints
    "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
    # Config/docs
    "pyproject.toml", "requirements.txt", "package.json", "README.md", ".env",
    "dockerfile", "docker-compose", "compose",
]

# Files that often matter for “how to run”
_RUNFILES_HINTS = [
    "requirements.txt", "pyproject.toml", "setup.py", "pipfile", "poetry.lock",
    "package.json", "pnpm-lock", "yarn.lock",
    "dockerfile", "docker-compose", "compose", ".env", "readme.md",
]

# Directory preferences/penalties
_CORE_DIR_BONUS = ("/src/", "/app/", "/api/", "/server/", "/backend/", "/frontend/", "/web/")
_PENALTY_DIRS = ("/tests/", "/test/", "/migrations/", "/dist/", "/build/", "/.next/", "/node_modules/")

# Regex for imports (Python + JS/TS)
_RE_PY_FROM = re.compile(r"^\s*from\s+([a-zA-Z0-9_\.]+)\s+import\s+", re.MULTILINE)
_RE_PY_IMPORT = re.compile(r"^\s*import\s+([a-zA-Z0-9_\.]+)", re.MULTILINE)
_RE_JS_IMPORT = re.compile(r"^\s*import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]", re.MULTILINE)
_RE_JS_REQ = re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", re.MULTILINE)

# ----------------------------
# Utilities
# ----------------------------

def _tokenize_goal(goal: str) -> List[str]:
    """
    Replit-like: extract useful tokens including:
    - words
    - snake_case
    - paths (foo/bar.py)
    - kebab-case
    """
    g = goal.lower()
    raw = re.findall(r"[a-z0-9_./-]+", g)
    toks = []
    for t in raw:
        if len(t) < 3:
            continue
        if t in _STOP:
            continue
        toks.append(t)
    # Dedup while preserving order
    seen = set()
    out = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _read_text(repo_root: Path, rel_path: str) -> str:
    p = repo_root / rel_path
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _read_snippet(repo_root: Path, rel_path: str, max_chars: int) -> str:
    """
    Replit-like peek:
    - small file => full
    - large file => head+tail
    """
    txt = _read_text(repo_root, rel_path)
    if not txt:
        return ""
    if len(txt) <= max_chars:
        return txt
    half = max_chars // 2
    return txt[:half] + "\n\n... [snip] ...\n\n" + txt[-half:]


def _file_meta_index(repo_map: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {f.get("path"): f for f in repo_map.get("files", []) if f.get("path")}


def _suffix(rel: str) -> str:
    return Path(rel).suffix.lower()


def _is_text_allowed(rel: str) -> bool:
    return _suffix(rel) in SETTINGS.TEXT_EXT


def _score_path(rel: str, goal_tokens: List[str]) -> float:
    """
    Replit-like path scoring:
    - entrypoints heavy
    - goal tokens in path strong
    - core dir bonus
    - penalty dirs penalty
    - runfiles small boost
    """
    p = rel.lower()
    s = 0.0

    # Entry points: huge boost
    for hint in _ENTRYPOINT_HINTS:
        if hint in p:
            s += 14.0
            break

    # Run/config files: boost
    for hint in _RUNFILES_HINTS:
        if hint in p:
            s += 8.0
            break

    # Core directories: boost
    if any(seg in p for seg in _CORE_DIR_BONUS):
        s += 3.0

    # Penalty directories: penalty
    if any(seg in p for seg in _PENALTY_DIRS):
        s -= 4.0

    # Goal token hits in path
    for t in goal_tokens:
        if t in p:
            s += 6.5

    # Prefer root-level files slightly (often entrypoints)
    if "/" not in p:
        s += 1.2

    return s


def _score_content(text: str, goal_tokens: List[str]) -> float:
    """
    Cheap content scoring with word-boundary-ish matching.
    """
    if not text or not goal_tokens:
        return 0.0
    lo = text.lower()
    score = 0.0
    for t in goal_tokens:
        # treat tokens like identifiers; cap contribution
        hits = len(re.findall(rf"(?<![a-z0-9_]){re.escape(t)}(?![a-z0-9_])", lo))
        if hits:
            score += min(10, hits) * 1.4
        else:
            # fallback substring count (less weight)
            sub = lo.count(t)
            if sub:
                score += min(6, sub) * 0.6
    return score


def _prefer_small(meta: Dict[str, Any]) -> float:
    """
    Prefer smaller files to keep context efficient (unless clearly relevant).
    """
    lines = int(meta.get("lines", 10**9))
    size = int(meta.get("size", 10**9))
    s = 0.0
    if lines <= 250:
        s += 1.6
    elif lines <= 600:
        s += 0.8
    elif lines >= 2000:
        s -= 2.2

    if size <= 60_000:
        s += 1.1
    elif size >= 500_000:
        s -= 2.2
    return s


def _project_overview(repo_map: Dict[str, Any]) -> str:
    files = repo_map.get("files", [])
    total = repo_map.get("file_count", len(files))
    ext_counts: Dict[str, int] = {}
    top_dirs: Dict[str, int] = {}

    for f in files:
        p = (f.get("path") or "").replace("\\", "/")
        if not p:
            continue
        ext = Path(p).suffix.lower() or "(none)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        d = p.split("/", 1)[0] if "/" in p else "."
        top_dirs[d] = top_dirs.get(d, 0) + 1

    exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    dirs = sorted(top_dirs.items(), key=lambda x: x[1], reverse=True)[:8]

    ext_line = ", ".join([f"{k}:{v}" for k, v in exts])
    dir_line = ", ".join([f"{k}:{v}" for k, v in dirs])

    return (
        f"Project files: {total}\n"
        f"Top dirs: {dir_line}\n"
        f"Top extensions: {ext_line}\n"
    )


def _detect_entrypoints(all_files: List[str]) -> List[str]:
    """
    Replit-like: try to find the “run path” quickly.
    """
    lows = {p.lower(): p for p in all_files}
    picks = []

    # direct common names
    for name in ("web_app.py", "app.py", "main.py", "server.py"):
        if name in lows:
            picks.append(lows[name])

    # any *app.py under common dirs
    for p in all_files:
        lp = p.lower()
        if lp.endswith("app.py") and any(seg in lp for seg in ("/app/", "/src/", "/server/", "/api/")):
            picks.append(p)

    # de-dup, keep order
    seen = set()
    out = []
    for p in picks:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out[:6]


def _resolve_python_module_to_path(module: str, all_set: Set[str]) -> List[str]:
    """
    Map 'foo.bar' to possible repo file paths:
      foo/bar.py
      foo/bar/__init__.py
    """
    mod = module.strip().lstrip(".")
    if not mod:
        return []
    parts = mod.split(".")
    candidates = []
    candidates.append("/".join(parts) + ".py")
    candidates.append("/".join(parts) + "/__init__.py")

    # only keep if exists
    return [c for c in candidates if c in all_set]


def _resolve_js_import_to_paths(spec: str, rel_from: str, all_set: Set[str]) -> List[str]:
    """
    Resolve relative JS imports like './utils' from 'src/app.ts'
    Try extensions and index files.
    """
    s = spec.strip()
    if not s.startswith("."):
        return []  # ignore npm packages

    base_dir = str(Path(rel_from).parent).replace("\\", "/")
    raw = str((Path(base_dir) / s).as_posix())

    # try direct
    cand = []
    for ext in (".ts", ".tsx", ".js", ".jsx", ".json"):
        cand.append(raw + ext)
    cand.append(raw + "/index.ts")
    cand.append(raw + "/index.tsx")
    cand.append(raw + "/index.js")
    cand.append(raw + "/index.jsx")

    return [c for c in cand if c in all_set]


def _expand_neighbors_by_imports(
    repo_root: Path,
    chosen: List[str],
    all_files: List[str],
    meta: Dict[str, Dict[str, Any]],
    max_new: int = 10
) -> List[str]:
    """
    Replit-like: open a file, then also pull its “neighbors” (local imports).
    This dramatically improves patch quality because the LLM sees the referenced helpers.
    """
    all_set = set(all_files)
    added: List[str] = []
    seen = set(chosen)

    def maybe_add(p: str):
        if p in seen:
            return
        if not _is_text_allowed(p):
            return
        # avoid huge files unless small-ish
        m = meta.get(p, {})
        if int(m.get("lines", 0)) > 2500:
            return
        seen.add(p)
        added.append(p)

    for rel in chosen:
        if len(added) >= max_new:
            break
        suf = _suffix(rel)
        if suf == ".py":
            txt = _read_text(repo_root, rel)
            for mod in _RE_PY_FROM.findall(txt) + _RE_PY_IMPORT.findall(txt):
                for p in _resolve_python_module_to_path(mod, all_set):
                    maybe_add(p)
                    if len(added) >= max_new:
                        break
        elif suf in (".js", ".jsx", ".ts", ".tsx"):
            txt = _read_text(repo_root, rel)
            specs = _RE_JS_IMPORT.findall(txt) + _RE_JS_REQ.findall(txt)
            for spec in specs:
                for p in _resolve_js_import_to_paths(spec, rel, all_set):
                    maybe_add(p)
                    if len(added) >= max_new:
                        break

    return chosen + added


# ----------------------------
# Selection
# ----------------------------

def choose_files_for_context(repo_map: Dict[str, Any], diff: Dict[str, Any], goal: str) -> List[str]:
    """
    Replit-like selection pipeline:
    1) Always include changed files (modified/added)
    2) Always include likely entrypoints + run/config files
    3) Score remaining by path relevance + (cheap) content relevance
    4) Expand neighbors via local imports
    """
    goal_tokens = _tokenize_goal(goal)
    meta = _file_meta_index(repo_map)

    all_files = [f.get("path") for f in repo_map.get("files", []) if f.get("path")]
    all_files = [p.replace("\\", "/") for p in all_files if isinstance(p, str)]
    all_files = [p for p in all_files if _is_text_allowed(p)]

    # 1) Changed files first
    changed = []
    for k in ("modified", "added"):
        for p in diff.get(k, []) or []:
            if isinstance(p, str):
                p = p.replace("\\", "/")
                if p in all_files:
                    changed.append(p)

    # 2) Entrypoints
    entrypoints = _detect_entrypoints(all_files)

    # 3) Run/config files (present in repo)
    runfiles = []
    lows = {p.lower(): p for p in all_files}
    for hint in _RUNFILES_HINTS:
        for lp, orig in lows.items():
            if hint in lp:
                runfiles.append(orig)
    # De-dup runfiles
    seen_rf = set()
    runfiles2 = []
    for p in runfiles:
        if p not in seen_rf:
            seen_rf.add(p)
            runfiles2.append(p)
    runfiles = runfiles2[:6]

    # 4) Score everything
    scored: List[Tuple[float, str]] = []
    for rel in all_files:
        m = meta.get(rel, {})
        s = _score_path(rel, goal_tokens) + _prefer_small(m)
        scored.append((s, rel))
    scored.sort(reverse=True, key=lambda x: x[0])

    # 5) Add content scoring for top candidates (cheap, bounded)
    top_for_content = [r for _, r in scored[:120]]
    rescored: List[Tuple[float, str]] = []
    for rel in top_for_content:
        m = meta.get(rel, {})
        base = _score_path(rel, goal_tokens) + _prefer_small(m)

        # changed bump
        if rel in changed:
            base += 15.0
        if rel in entrypoints:
            base += 8.0
        if rel in runfiles:
            base += 6.0

        # only scan content for reasonably sized files
        size = int(m.get("size", 0))
        if goal_tokens and size <= 140_000:
            # NOTE: repo_root is not available here; content scoring will be done in build_llm_context phase
            # We'll keep base as-is.
            pass

        rescored.append((base, rel))
    rescored.sort(reverse=True, key=lambda x: x[0])

    # 6) Merge priority buckets
    merged: List[str] = []
    seen: Set[str] = set()

    def add_many(items: List[str]):
        for p in items:
            if p in seen:
                continue
            seen.add(p)
            merged.append(p)
            if len(merged) >= SETTINGS.MAX_FILES_TO_SHOW:
                return

    add_many(changed)
    add_many(entrypoints)
    add_many(runfiles)
    add_many([r for _, r in rescored])

    return merged[: SETTINGS.MAX_FILES_TO_SHOW]


# ----------------------------
# Context builder
# ----------------------------

def build_llm_context(
    repo_root: Path,
    repo_map: Dict[str, Any],
    diff: Dict[str, Any],
    goal: str
) -> Tuple[str, List[str]]:
    """
    Replit-like LLM context:
    - Project Overview
    - Run/Entrypoint hint
    - Diff summary
    - Selected files list (like tabs)
    - Snippets with “peek” head+tail
    - Neighbor expansion via imports (local)
    """
    meta = _file_meta_index(repo_map)
    all_files = [f.get("path") for f in repo_map.get("files", []) if f.get("path")]
    all_files = [p.replace("\\", "/") for p in all_files if isinstance(p, str)]
    all_files = [p for p in all_files if _is_text_allowed(p)]

    goal_tokens = _tokenize_goal(goal)

    # Base selection (path-based)
    chosen = choose_files_for_context(repo_map, diff, goal)

    # Re-score chosen using real content now (bounded)
    rescored: List[Tuple[float, str]] = []
    for rel in chosen:
        m = meta.get(rel, {})
        base = _score_path(rel, goal_tokens) + _prefer_small(m)

        # changed bump
        if rel in (diff.get("modified", []) or []):
            base += 12.0
        if rel in (diff.get("added", []) or []):
            base += 10.0

        size = int(m.get("size", 0))
        if goal_tokens and size <= 140_000:
            txt = _read_text(repo_root, rel)
            base += _score_content(txt, goal_tokens)

        rescored.append((base, rel))

    rescored.sort(reverse=True, key=lambda x: x[0])
    chosen = [r for _, r in rescored][: SETTINGS.MAX_FILES_TO_SHOW]

    # Import neighbor expansion (very Replit-like)
    chosen = _expand_neighbors_by_imports(
        repo_root=repo_root,
        chosen=chosen,
        all_files=all_files,
        meta=meta,
        max_new=12,
    )

    # Compact repo list (sidebar-like)
    short_list = all_files[:300]

    # Entrypoints hint
    entrypoints = _detect_entrypoints(all_files)

    # Build the final context text
    parts: List[str] = []
    parts.append("SYSTEM: You are an offline IDE assistant operating on a local repo.")
    parts.append("IMPORTANT: Output ONLY valid JSON matching the provided JSON schema. No prose.\n")

    parts.append("=== USER GOAL ===")
    parts.append(goal.strip() or "(empty)")
    parts.append("")

    parts.append("=== PROJECT OVERVIEW ===")
    parts.append(_project_overview(repo_map).rstrip())
    parts.append("")

    parts.append("=== LIKELY ENTRYPOINTS (how to run) ===")
    parts.append("\n".join(entrypoints) if entrypoints else "(not detected)")
    parts.append("")

    parts.append("=== REPO FILE LIST (first 300, like sidebar) ===")
    parts.append("\n".join(short_list))
    parts.append("")

    parts.append("=== DIFF SUMMARY (since last scan) ===")
    parts.append(str(diff))
    parts.append("")

    parts.append("=== HARD RULES ===")
    parts.append("- Only reference/modify files that exist in the repo file list.")
    parts.append("- Use minimal edits: replace_range / replace_text / insert_after / insert_before.")
    parts.append("- If the goal is unclear or unsafe, output {\"files\": []}.")
    parts.append("- Do NOT invent filenames, folders, dependencies, or commands.")
    parts.append("")

    parts.append("=== OPEN FILES (selected for context) ===")
    parts.append("\n".join(chosen))
    parts.append("")

    # File snippets (tabs-like)
    total = 0
    parts.append("=== FILE SNIPPETS (peek) ===")
    for rel in chosen:
        m = meta.get(rel, {})
        hdr = f"\n--- FILE: {rel} (lines={m.get('lines','?')}, size={m.get('size','?')}) ---\n"
        snippet = _read_snippet(repo_root, rel, SETTINGS.MAX_CHARS_PER_FILE)
        chunk = hdr + (snippet or "") + "\n"

        if total + len(chunk) > SETTINGS.MAX_TOTAL_CONTEXT_CHARS:
            parts.append("\n[CONTEXT BUDGET HIT: remaining files omitted]\n")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts), chosen
