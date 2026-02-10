from __future__ import annotations
from typing import Dict, Any

def _index(repo_map: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {f["path"]: f for f in repo_map.get("files", [])}

def diff_maps(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    b = _index(before)
    a = _index(after)

    added = sorted([p for p in a if p not in b])
    removed = sorted([p for p in b if p not in a])
    modified = sorted([p for p in a if p in b and a[p]["sha256"] != b[p]["sha256"]])

    return {
        "repo_root": after.get("repo_root"),
        "generated_at": after.get("generated_at"),
        "added": added,
        "removed": removed,
        "modified": modified,
        "counts": {"added": len(added), "removed": len(removed), "modified": len(modified)}
    }
