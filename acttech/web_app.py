# web_app.py
from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from typing import Any, List

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from config import SETTINGS
from repo_scan import scan_repo
from repo_diff import diff_maps
from context_builder import build_llm_context
from llm_planner import plan_patches
from patch_apply import apply_patch_plan


app = FastAPI(title="Local Repo LLM Updater (Replit-like)")


# ----------------------------
# Helpers
# ----------------------------

def repo_root() -> Path:
    return Path(SETTINGS.REPO_ROOT).resolve()

def state_dir() -> Path:
    d = repo_root() / SETTINGS.STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d

def load_json(p: Path, default: Any = None) -> Any:
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))

def save_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2), encoding="utf-8")

def require_token(req: Request) -> None:
    if not SETTINGS.ADMIN_TOKEN:
        return
    got = req.headers.get("x-admin-token") or req.query_params.get("token") or ""
    if got != SETTINGS.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized (bad admin token)")

def is_safe_rel_path(rel: str) -> bool:
    p = Path(rel)
    return not (p.is_absolute() or ".." in p.parts)

def list_repo_files() -> List[str]:
    root = repo_root()
    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SETTINGS.IGNORE_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if p.suffix.lower() in SETTINGS.TEXT_EXT:
                out.append(rel)
    out.sort()
    return out

def read_file_text(rel_path: str, max_chars: int = 120_000) -> str:
    if not is_safe_rel_path(rel_path):
        raise HTTPException(400, "Invalid path.")
    p = repo_root() / rel_path
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found.")
    if p.suffix.lower() not in SETTINGS.TEXT_EXT:
        raise HTTPException(400, "File type not allowed.")
    txt = p.read_text(encoding="utf-8", errors="replace")
    if len(txt) > max_chars:
        return txt[:max_chars] + "\n\n... [TRUNCATED FOR VIEWER] ..."
    return txt


# ----------------------------
# UI (single-file HTML) - Replit-like
# ----------------------------

INDEX_HTML = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Local IDE Updater</title>
  <style>
    :root{
      --bg:#0b1020; --panel:#0f1733; --panel2:#111c3d;
      --text:#e7e7e7; --muted:#9aa4c0; --border:rgba(255,255,255,.08);
      --accent:#4ea1ff; --ok:#2ecc71; --warn:#f1c40f; --bad:#e74c3c;
      --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      --sans: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    }
    * { box-sizing:border-box; }
    body { margin:0; font-family:var(--sans); background:var(--bg); color:var(--text); }
    .topbar{
      height:54px; display:flex; align-items:center; gap:12px;
      padding:0 14px; border-bottom:1px solid var(--border); background:rgba(255,255,255,.02);
    }
    .logo{ font-weight:700; letter-spacing:.2px; }
    .pill{
      display:inline-flex; align-items:center; gap:8px;
      padding:6px 10px; border:1px solid var(--border); border-radius:999px;
      background:rgba(255,255,255,.03); color:var(--muted); font-size:12px;
      max-width: 50vw;
      overflow:hidden;
      text-overflow:ellipsis;
      white-space:nowrap;
    }
    .kbd{
      font-family:var(--mono); font-size:11px; padding:2px 6px;
      border:1px solid var(--border); border-bottom-color:rgba(255,255,255,.18);
      border-radius:6px; background:rgba(0,0,0,.25); color:var(--muted);
    }
    .wrap{
      height:calc(100vh - 54px);
      display:grid;
      grid-template-columns: 320px 1fr 420px;
      gap:0;
    }
    .pane{
      border-right:1px solid var(--border);
      overflow:hidden;
      display:flex; flex-direction:column;
      background:var(--panel);
    }
    .pane.right{ border-right:0; border-left:1px solid var(--border); background:var(--panel); }
    .pane.center{ background:var(--panel2); }
    .paneHeader{
      padding:10px 12px; border-bottom:1px solid var(--border);
      display:flex; align-items:center; justify-content:space-between;
      font-size:13px; color:var(--muted);
      gap: 10px;
    }
    .paneBody{ padding:10px 12px; overflow:auto; }
    .btnrow{ display:flex; flex-wrap:wrap; gap:8px; }
    button{
      font-family:var(--sans);
      padding:9px 10px; border-radius:10px;
      border:1px solid var(--border);
      background:rgba(255,255,255,.04);
      color:var(--text);
      cursor:pointer;
    }
    button:hover{ border-color:rgba(255,255,255,.18); }
    button.primary{ background:rgba(78,161,255,.18); border-color:rgba(78,161,255,.35); }
    button.danger{ background:rgba(231,76,60,.16); border-color:rgba(231,76,60,.35); }
    input, textarea{
      width:100%; border-radius:10px; border:1px solid var(--border);
      background:rgba(0,0,0,.25); color:var(--text);
      padding:10px; outline:none;
      font-family:var(--sans);
    }
    textarea{ min-height:140px; resize:vertical; }
    .small{ font-size:12px; color:var(--muted); line-height:1.4; }
    .divider{ height:1px; background:var(--border); margin:10px 0; }
    .fileSearch{ position:sticky; top:0; background:var(--panel); padding-bottom:10px; z-index: 2; }
    .fileList{ display:flex; flex-direction:column; gap:4px; }
    .fileItem{
      padding:8px 10px; border-radius:10px; cursor:pointer;
      border:1px solid transparent; color:var(--text);
      font-family:var(--mono); font-size:12px;
      background:rgba(255,255,255,.02);
    }
    .fileItem:hover{ border-color:rgba(255,255,255,.12); }
    .fileItem.active{ border-color:rgba(78,161,255,.55); background:rgba(78,161,255,.12); }
    .tabs{ display:flex; gap:8px; flex-wrap:wrap; }
    .tab{
      padding:7px 10px; border-radius:999px; border:1px solid var(--border);
      background:rgba(255,255,255,.03); color:var(--muted); cursor:pointer; font-size:12px;
    }
    .tab.active{ background:rgba(78,161,255,.18); color:var(--text); border-color:rgba(78,161,255,.35); }
    .editor{
      font-family:var(--mono);
      white-space:pre; overflow:auto;
      padding:12px; border-radius:12px;
      border:1px solid var(--border);
      background:rgba(0,0,0,.35);
      min-height:60vh;
      line-height:1.45;
      font-size:12px;
    }
    .status{
      padding:10px 12px; border-radius:12px; border:1px solid var(--border);
      background:rgba(255,255,255,.03);
      margin-bottom:10px;
      white-space: pre-wrap;
    }
    .status.ok{ border-color:rgba(46,204,113,.45); background:rgba(46,204,113,.10); }
    .status.warn{ border-color:rgba(241,196,15,.45); background:rgba(241,196,15,.10); }
    .status.bad{ border-color:rgba(231,76,60,.45); background:rgba(231,76,60,.10); }
    pre.json{
      font-family:var(--mono); font-size:12px;
      padding:12px; border-radius:12px;
      border:1px solid var(--border);
      background:rgba(0,0,0,.35);
      overflow:auto;
      max-height:70vh;
      white-space:pre;
    }
    .metaRight{ text-align:right; }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="logo">⚡ Local IDE Updater</div>
    <div class="pill"><span>Repo:</span> <span id="repoPath">loading…</span></div>
    <div style="flex:1"></div>
    <div class="pill">Shortcuts: <span class="kbd">Ctrl</span>+<span class="kbd">K</span> search • <span class="kbd">Ctrl</span>+<span class="kbd">Enter</span> plan</div>
  </div>

  <div class="wrap">
    <!-- LEFT -->
    <div class="pane">
      <div class="paneHeader">
        <div>Files</div>
        <div class="small metaRight" id="fileCount"></div>
      </div>
      <div class="paneBody">
        <div class="fileSearch">
          <input id="fileSearch" placeholder="Search files…" />
          <div class="small" style="margin-top:8px;">
            Click a file to open (read-only). Next: editor + diff view.
          </div>
          <div class="divider"></div>
        </div>
        <div class="fileList" id="fileList"></div>
      </div>
    </div>

    <!-- CENTER -->
    <div class="pane center">
      <div class="paneHeader">
        <div class="tabs">
          <div class="tab active" id="tabEditor" onclick="showTab('editor')">Editor</div>
          <div class="tab" id="tabGoal" onclick="showTab('goal')">Prompt</div>
        </div>
        <div class="small metaRight" id="activeFileMeta"></div>
      </div>

      <div class="paneBody" id="viewEditor">
        <div class="small" id="activeFileName">(no file selected)</div>
        <div style="height:8px;"></div>
        <div class="btnrow">
          <button onclick="refreshActiveFile()">Refresh</button>
          <button onclick="copyActiveFile()">Copy</button>
        </div>
        <div style="height:10px;"></div>
        <div class="editor" id="fileContent">// select a file from the left</div>
      </div>

      <div class="paneBody" id="viewGoal" style="display:none;">
        <div class="small">Tell the LLM what to change, then generate a patch plan.</div>
        <div style="height:8px;"></div>
        <textarea id="goal" placeholder="Example: Add a /health endpoint, update router, and document in README."></textarea>

        <div style="height:10px;"></div>
        <div class="btnrow">
          <button onclick="scan()">Scan</button>
          <button onclick="diff()">Diff</button>
          <button class="primary" onclick="plan()">Plan patches (LLM)</button>
          <button onclick="dryRunApply()">Dry-run apply</button>
          <button class="danger" onclick="applyReal()">Apply for real</button>
        </div>

        <div class="divider"></div>
        <div class="small">Admin token (optional)</div>
        <input id="token" type="password" placeholder="If set in config.py"/>
        <div class="small" style="margin-top:8px;">
          Safety: always dry-run first. Apply creates backups under <b>.autoupdater_backups/</b>.
        </div>
      </div>
    </div>

    <!-- RIGHT -->
    <div class="pane right">
      <div class="paneHeader">
        <div>Report</div>
        <div class="small metaRight">JSON</div>
      </div>
      <div class="paneBody">
        <div id="statusBox" class="status warn">Loading…</div>
        <pre id="out" class="json">{}</pre>
      </div>
    </div>
  </div>

<script>
  let ALL_FILES = [];
  let ACTIVE_FILE = "";

  function adminHeaders() {
    const tokenEl = document.getElementById("token");
    const t = tokenEl ? tokenEl.value.trim() : "";
    return t ? {"x-admin-token": t} : {};
  }

  function setOut(obj) {
    document.getElementById("out").textContent = JSON.stringify(obj, null, 2);
  }

  function setStatus(text, kind="warn") {
    const box = document.getElementById("statusBox");
    box.className = "status " + kind;
    box.textContent = text;
  }

  function showTab(which){
    const viewEditor = document.getElementById("viewEditor");
    const viewGoal = document.getElementById("viewGoal");
    const tabEditor = document.getElementById("tabEditor");
    const tabGoal = document.getElementById("tabGoal");

    if(which === "goal"){
      viewEditor.style.display="none";
      viewGoal.style.display="block";
      tabEditor.classList.remove("active");
      tabGoal.classList.add("active");
    }else{
      viewEditor.style.display="block";
      viewGoal.style.display="none";
      tabGoal.classList.remove("active");
      tabEditor.classList.add("active");
    }
  }

  async function loadStatus() {
    const r = await fetch("/api/status", {headers: adminHeaders()});
    const j = await r.json();
    document.getElementById("repoPath").textContent = j.repo_root || "";
    setOut(j);
    setStatus("Ready. Load files, then Scan/Diff when needed.", "ok");
  }

  async function loadFiles() {
    const r = await fetch("/api/files", {headers: adminHeaders()});
    const j = await r.json();
    ALL_FILES = j.files || [];
    document.getElementById("fileCount").textContent = (j.count ?? ALL_FILES.length) + " files";
    renderFileList();
  }

  function renderFileList() {
    const q = (document.getElementById("fileSearch").value || "").trim().toLowerCase();
    const list = document.getElementById("fileList");
    list.innerHTML = "";

    const filtered = q ? ALL_FILES.filter(p => p.toLowerCase().includes(q)) : ALL_FILES;
    const show = filtered.slice(0, 900);

    for (const p of show) {
      const div = document.createElement("div");
      div.className = "fileItem" + (p === ACTIVE_FILE ? " active" : "");
      div.textContent = p;
      div.onclick = () => openFile(p);
      list.appendChild(div);
    }
  }

  async function openFile(path) {
    ACTIVE_FILE = path;
    renderFileList();
    showTab("editor");
    await fetchAndShowFile(path);
  }

  async function fetchAndShowFile(path) {
    document.getElementById("activeFileName").textContent = path;
    document.getElementById("activeFileMeta").textContent = "Loading…";
    document.getElementById("fileContent").textContent = "";

    const r = await fetch("/api/file?path=" + encodeURIComponent(path), {headers: adminHeaders()});
    const j = await r.json();
    if (j.error) {
      document.getElementById("activeFileMeta").textContent = j.error;
      document.getElementById("fileContent").textContent = "";
      return;
    }
    document.getElementById("activeFileMeta").textContent =
      `${j.lines} lines • ${j.size} bytes • ${j.sha256.slice(0,12)}…`;
    document.getElementById("fileContent").textContent = j.content;
  }

  async function refreshActiveFile() {
    if (!ACTIVE_FILE) return;
    await fetchAndShowFile(ACTIVE_FILE);
  }

  async function copyActiveFile() {
    const txt = document.getElementById("fileContent").textContent;
    await navigator.clipboard.writeText(txt);
    setStatus("Copied file content to clipboard.", "ok");
  }

  async function scan() {
    setStatus("Scanning repo…", "warn");
    const r = await fetch("/api/scan", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Scan saved. Now run Diff.", "ok");
    await loadFiles();
    if (ACTIVE_FILE) await refreshActiveFile();
  }

  async function diff() {
    setStatus("Computing diff…", "warn");
    const r = await fetch("/api/diff", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Diff saved. Now Plan patches.", "ok");
  }

  async function plan() {
    const goal = document.getElementById("goal").value.trim();
    if (!goal) { alert("Write what you want to change first."); return; }
    setStatus("Asking LLM to plan patches…", "warn");

    const r = await fetch("/api/plan", {
      method:"POST",
      headers: {"Content-Type":"application/json", ...adminHeaders()},
      body: JSON.stringify({goal})
    });
    const j = await r.json();
    setOut(j);
    if (j.error) {
      setStatus("Plan failed: " + j.error, "bad");
      return;
    }
    setStatus("Patch plan generated. Dry-run apply next.", "ok");
  }

  async function dryRunApply() {
    setStatus("Dry-run applying patches…", "warn");
    const r = await fetch("/api/apply?dry_run=1", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Dry-run done. If correct, Apply for real.", "ok");
    if (ACTIVE_FILE) await refreshActiveFile();
  }

  async function applyReal() {
    if (!confirm("Apply patches for real? This edits files and creates backups.")) return;
    setStatus("Applying patches for real…", "warn");
    const r = await fetch("/api/apply?dry_run=0", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Applied. Backups saved. Refresh file to see changes.", "ok");
    await loadFiles();
    if (ACTIVE_FILE) await refreshActiveFile();
  }

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      document.getElementById("fileSearch").focus();
    }
    if (e.ctrlKey && e.key === "Enter") {
      const goalEl = document.getElementById("goal");
      if (goalEl && document.activeElement === goalEl) plan();
    }
  });

  document.getElementById("fileSearch").addEventListener("input", renderFileList);

  (async () => {
    await loadStatus();
    await loadFiles();
  })();
</script>
</body>
</html>
"""


# ----------------------------
# Routes
# ----------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML

@app.get("/api/status")
def api_status(req: Request) -> JSONResponse:
    require_token(req)
    sd = state_dir()
    return JSONResponse({
        "repo_root": str(repo_root()),
        "state_dir": str(sd),
        "has_before": (sd / "repo_map_before.json").exists(),
        "has_after": (sd / "repo_map_after.json").exists(),
        "has_diff": (sd / "diff.json").exists(),
        "has_patches": (sd / "patches.json").exists(),
        "last_apply_log": load_json(sd / "last_apply_log.json", default=None),
        "viewer_limits": {
            "max_files_shown_in_list": 800,
            "max_chars_per_file_view": 120_000
        }
    })

@app.get("/api/files")
def api_files(req: Request) -> JSONResponse:
    require_token(req)
    files = list_repo_files()
    return JSONResponse({"files": files, "count": len(files)})

@app.get("/api/file")
def api_file(req: Request, path: str) -> JSONResponse:
    require_token(req)
    try:
        content = read_file_text(path, max_chars=120_000)
        p = repo_root() / path
        size = p.stat().st_size
        full = p.read_text(encoding="utf-8", errors="replace")
        sha256 = hashlib.sha256(full.encode("utf-8", errors="replace")).hexdigest()
        return JSONResponse({
            "path": path,
            "size": size,
            "lines": full.count("\n") + 1,
            "sha256": sha256,
            "content": content
        })
    except HTTPException as e:
        return JSONResponse({"error": e.detail}, status_code=e.status_code)

@app.post("/api/scan")
def api_scan(req: Request) -> JSONResponse:
    require_token(req)
    sd = state_dir()
    before_path = sd / "repo_map_before.json"
    after_path = sd / "repo_map_after.json"

    if not before_path.exists():
        before = scan_repo(repo_root())
        save_json(before_path, before)

    after = scan_repo(repo_root())
    save_json(after_path, after)

    return JSONResponse({
        "saved_before": str(before_path),
        "saved_after": str(after_path),
        "after_file_count": after.get("file_count"),
    })

@app.post("/api/diff")
def api_diff(req: Request) -> JSONResponse:
    require_token(req)
    sd = state_dir()
    before = load_json(sd / "repo_map_before.json")
    after = load_json(sd / "repo_map_after.json")
    if before is None or after is None:
        raise HTTPException(400, "Scan first.")

    d = diff_maps(before, after)
    save_json(sd / "diff.json", d)
    return JSONResponse({"diff": d, "saved": str(sd / "diff.json")})

@app.post("/api/plan")
async def api_plan(req: Request) -> JSONResponse:
    require_token(req)
    payload = await req.json()
    goal = (payload.get("goal") or "").strip()
    if not goal:
        raise HTTPException(400, "Missing goal.")

    sd = state_dir()
    after = load_json(sd / "repo_map_after.json")
    diff = load_json(sd / "diff.json")

    if after is None:
        raise HTTPException(400, "Scan first.")
    if diff is None:
        raise HTTPException(400, "Diff first.")

    # IMPORTANT: pass goal
    context_text, chosen = build_llm_context(repo_root(), after, diff, goal)
    plan = plan_patches(goal, context_text)

    save_json(sd / "patches.json", plan)
    save_json(sd / "chosen_files.json", {"chosen_files": chosen})

    return JSONResponse({
        "chosen_files": chosen,
        "patch_plan": plan,
        "saved_patches": str(sd / "patches.json"),
    })

@app.post("/api/apply")
def api_apply(req: Request, dry_run: int = 1) -> JSONResponse:
    require_token(req)
    sd = state_dir()
    plan = load_json(sd / "patches.json")
    if plan is None:
        raise HTTPException(400, "Plan patches first.")

    log = apply_patch_plan(repo_root(), plan, dry_run=bool(dry_run))
    save_json(sd / "last_apply_log.json", log)
    return JSONResponse(log)
