from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Any


def _index(repo_map: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {f["path"]: f for f in repo_map.get("files", []) if "path" in f}


def _ext(path: str) -> str:
    e = Path(path).suffix.lower()
    return e if e else "(none)"


def _top_dirs(paths, k=8):
    c = Counter()
    for p in paths:
        p = p.replace("\\", "/")
        d = p.split("/", 1)[0] if "/" in p else "."
        c[d] += 1
    return [{"dir": d, "count": n} for d, n in c.most_common(k)]


def _rename_hints(added, removed, before_index, after_index):
    removed_by_sha = defaultdict(list)

    for p in removed:
        sha = before_index.get(p, {}).get("sha256")
        if sha:
            removed_by_sha[sha].append(p)

    renames = []

    for p in added:
        sha = after_index.get(p, {}).get("sha256")
        if not sha:
            continue

        matches = removed_by_sha.get(sha, [])
        for old in matches:
            renames.append({
                "from": old,
                "to": p,
                "sha256": sha
            })

    return renames[:50]


def diff_maps(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:

    b = _index(before)
    a = _index(after)

    added = sorted([p for p in a if p not in b])
    removed = sorted([p for p in b if p not in a])
    modified = sorted([
        p for p in a
        if p in b and a[p].get("sha256") != b[p].get("sha256")
    ])

    changed_all = added + removed + modified

    dir_summary = _top_dirs(changed_all, k=10)

    ext_counts = Counter(_ext(p) for p in changed_all)
    top_exts = [{"ext": e, "count": n} for e, n in ext_counts.most_common(10)]

    renames = _rename_hints(added, removed, b, a)

    # Estimate magnitude using metadata (safe + fast)
    magnitudes = []
    for p in modified:
        before_meta = b.get(p, {})
        after_meta = a.get(p, {})

        before_lines = int(before_meta.get("lines", 0))
        after_lines = int(after_meta.get("lines", 0))

        delta = abs(after_lines - before_lines)

        magnitudes.append({
            "path": p,
            "line_delta_estimate": delta
        })

    magnitudes_sorted = sorted(
        magnitudes,
        key=lambda x: -x["line_delta_estimate"]
    )

    return {
        "repo_root": after.get("repo_root"),
        "generated_at": after.get("generated_at"),

        "added": added,
        "removed": removed,
        "modified": modified,

        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        },

        "summary": {
            "top_dirs": dir_summary,
            "top_exts": top_exts,
            "renames": renames,
            "top_changed_files": magnitudes_sorted[:15],
        }
    }
