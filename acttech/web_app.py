# web_app.py (UPGRADED: editable editor + save + terminal panel + run endpoint + auto-log plan/run_commands)
from __future__ import annotations

import json
import os
import hashlib
import subprocess
import time
from pathlib import Path
from typing import Any, List, Dict, Optional

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
    return rel and not (p.is_absolute() or ".." in p.parts)

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

def read_file_text(rel_path: str, max_chars: int = 160_000) -> str:
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

def write_file_text(rel_path: str, content: str) -> None:
    if not is_safe_rel_path(rel_path):
        raise HTTPException(400, "Invalid path.")
    p = repo_root() / rel_path
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found.")
    if p.suffix.lower() not in SETTINGS.TEXT_EXT:
        raise HTTPException(400, "File type not allowed.")
    p.write_text(content, encoding="utf-8")

def file_meta(rel_path: str) -> Dict[str, Any]:
    p = repo_root() / rel_path
    full = p.read_text(encoding="utf-8", errors="replace")
    sha256 = hashlib.sha256(full.encode("utf-8", errors="replace")).hexdigest()
    return {
        "path": rel_path,
        "size": p.stat().st_size,
        "lines": full.count("\n") + 1,
        "sha256": sha256,
    }

def _ts() -> str:
    return time.strftime("%H:%M:%S")

def terminal_log_path() -> Path:
    return state_dir() / "terminal.log"

def term_append(text: str) -> None:
    terminal_log_path().parent.mkdir(parents=True, exist_ok=True)
    with terminal_log_path().open("a", encoding="utf-8", errors="replace") as f:
        f.write(text)

def term_line(msg: str) -> None:
    term_append(f"[{_ts()}] {msg}\n")

def term_clear() -> None:
    terminal_log_path().write_text("", encoding="utf-8")

def _run_command(cmd: str, cwd: Path, timeout: Optional[int] = None) -> Dict[str, Any]:
    """
    Runs a shell command in repo_root. Captures stdout/stderr.
    On Windows, shell=True lets users type like 'python -m uvicorn ...'
    """
    t0 = time.time()
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout
    )
    dt = time.time() - t0
    return {
        "cmd": cmd,
        "returncode": p.returncode,
        "elapsed_sec": round(dt, 3),
        "stdout": p.stdout or "",
        "stderr": p.stderr or "",
    }

def _format_plan_for_terminal(plan: Dict[str, Any]) -> str:
    lines = []
    summary = (plan.get("summary") or "").strip()
    if summary:
        lines.append("PLAN SUMMARY:")
        lines.append(f"- {summary}")

    notes = plan.get("notes") or []
    if isinstance(notes, list) and notes:
        lines.append("\nNOTES:")
        for n in notes[:20]:
            lines.append(f"- {str(n)}")

    risk = plan.get("risk_level")
    if risk:
        lines.append(f"\nRISK: {risk}")

    cmds = plan.get("run_commands") or []
    if isinstance(cmds, list) and cmds:
        lines.append("\nRUN COMMANDS:")
        for c in cmds[:20]:
            lines.append(f"$ {c}")

    exp = plan.get("expected_output") or []
    if isinstance(exp, list) and exp:
        lines.append("\nEXPECTED OUTPUT:")
        for e in exp[:30]:
            lines.append(f"- {str(e)}")

    ver = plan.get("verification_steps") or []
    if isinstance(ver, list) and ver:
        lines.append("\nVERIFY:")
        for v in ver[:30]:
            lines.append(f"- {str(v)}")

    # Always include touched files overview
    files = plan.get("files") or []
    if isinstance(files, list) and files:
        lines.append("\nFILES TO CHANGE:")
        for f in files[:40]:
            path = f.get("path", "")
            why = (f.get("why") or "").strip()
            if why:
                lines.append(f"- {path}  # {why}")
            else:
                lines.append(f"- {path}")
    return "\n".join(lines).strip() + "\n"


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

    .layout{
      height:calc(100vh - 54px);
      display:grid;
      grid-template-rows: 1fr 260px;
    }

    .wrap{
      display:grid;
      grid-template-columns: 320px 1fr 420px;
      gap:0;
      min-height:0;
    }

    .pane{
      border-right:1px solid var(--border);
      overflow:hidden;
      display:flex; flex-direction:column;
      background:var(--panel);
      min-height:0;
    }
    .pane.right{ border-right:0; border-left:1px solid var(--border); background:var(--panel); }
    .pane.center{ background:var(--panel2); }
    .paneHeader{
      padding:10px 12px; border-bottom:1px solid var(--border);
      display:flex; align-items:center; justify-content:space-between;
      font-size:13px; color:var(--muted);
      gap: 10px;
    }
    .paneBody{ padding:10px 12px; overflow:auto; min-height:0; }
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
      user-select:none;
    }
    .fileItem:hover{ border-color:rgba(255,255,255,.12); }
    .fileItem.active{ border-color:rgba(78,161,255,.55); background:rgba(78,161,255,.12); }

    .tabs{ display:flex; gap:8px; flex-wrap:wrap; }
    .tab{
      padding:7px 10px; border-radius:999px; border:1px solid var(--border);
      background:rgba(255,255,255,.03); color:var(--muted); cursor:pointer; font-size:12px;
      user-select:none;
    }
    .tab.active{ background:rgba(78,161,255,.18); color:var(--text); border-color:rgba(78,161,255,.35); }

    .editorBox{
      display:flex; flex-direction:column;
      gap:8px;
      min-height:0;
    }
    .editorMetaRow{
      display:flex; align-items:center; justify-content:space-between; gap:10px;
    }
    .editorArea{
      font-family:var(--mono);
      padding:12px; border-radius:12px;
      border:1px solid var(--border);
      background:rgba(0,0,0,.35);
      min-height:50vh;
      line-height:1.45;
      font-size:12px;
      resize:none;
      outline:none;
      color:var(--text);
      width:100%;
      flex:1;
      white-space:pre;
      overflow:auto;
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

    /* Terminal */
    .terminalPane{
      border-top:1px solid var(--border);
      background:rgba(0,0,0,.25);
      display:flex;
      flex-direction:column;
      min-height:0;
    }
    .terminalHeader{
      display:flex;
      align-items:center;
      justify-content:space-between;
      padding:10px 12px;
      border-bottom:1px solid var(--border);
      color:var(--muted);
      font-size:13px;
    }
    .terminalBody{
      padding:10px 12px;
      overflow:auto;
      min-height:0;
      flex:1;
    }
    pre.terminal{
      font-family:var(--mono);
      font-size:12px;
      line-height:1.45;
      white-space:pre-wrap;
      margin:0;
    }
    .cmdRow{
      display:flex; gap:8px; align-items:center;
      padding:10px 12px; border-top:1px solid var(--border);
      background:rgba(255,255,255,.02);
    }
    .cmdRow input{
      font-family:var(--mono);
    }
  </style>
</head>
<body>
  <div class="topbar">
    <div class="logo">⚡ Local IDE Updater</div>
    <div class="pill"><span>Repo:</span> <span id="repoPath">loading…</span></div>
    <div style="flex:1"></div>
    <div class="pill">Shortcuts: <span class="kbd">Ctrl</span>+<span class="kbd">K</span> search • <span class="kbd">Ctrl</span>+<span class="kbd">S</span> save • <span class="kbd">Ctrl</span>+<span class="kbd">Enter</span> plan</div>
  </div>

  <div class="layout">
    <div class="wrap">
      <!-- LEFT -->
      <div class="pane">
        <div class="paneHeader">
          <div>Files</div>
          <div class="small" id="fileCount"></div>
        </div>
        <div class="paneBody">
          <div class="fileSearch">
            <input id="fileSearch" placeholder="Search files…" />
            <div class="small" style="margin-top:8px;">
              Click a file to open. Edit + Save in the center.
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
            <div class="tab" id="tabGoal" onclick="showTab('goal')">Agent</div>
          </div>
          <div class="small" id="activeFileMeta"></div>
        </div>

        <div class="paneBody" id="viewEditor">
          <div class="editorBox">
            <div class="editorMetaRow">
              <div class="small" id="activeFileName">(no file selected)</div>
              <div class="btnrow">
                <button onclick="refreshActiveFile()">Reload</button>
                <button class="primary" onclick="saveActiveFile()">Save</button>
              </div>
            </div>
            <textarea class="editorArea" id="fileEditor" spellcheck="false" placeholder="// select a file from the left"></textarea>
            <div class="small" id="dirtyHint"></div>
          </div>
        </div>

        <div class="paneBody" id="viewGoal" style="display:none;">
          <div class="small">Describe what you want. The agent will plan patches and (optionally) suggest run commands.</div>
          <div style="height:8px;"></div>
          <textarea id="goal" placeholder="Example: Add a /health endpoint, update router, and document in README."></textarea>

          <div style="height:10px;"></div>
          <div class="btnrow">
            <button onclick="scan()">Scan</button>
            <button onclick="diff()">Diff</button>
            <button class="primary" onclick="plan()">Plan (LLM)</button>
            <button onclick="dryRunApply()">Dry-run apply</button>
            <button class="danger" onclick="applyReal()">Apply</button>
            <button onclick="runPlanCommands()">Run plan commands</button>
          </div>

          <div class="divider"></div>
          <div class="small">Admin token (optional)</div>
          <input id="token" type="password" placeholder="If set in config.py"/>
          <div class="small" style="margin-top:8px;">
            Tip: Plan prints summary + run_commands into the terminal panel automatically.
          </div>
        </div>
      </div>

      <!-- RIGHT -->
      <div class="pane right">
        <div class="paneHeader">
          <div>Report</div>
          <div class="small">JSON</div>
        </div>
        <div class="paneBody">
          <div id="statusBox" class="status warn">Loading…</div>
          <pre id="out" class="json">{}</pre>
        </div>
      </div>
    </div>

    <!-- TERMINAL -->
    <div class="terminalPane">
      <div class="terminalHeader">
        <div>Terminal</div>
        <div class="btnrow">
          <button onclick="clearTerminal()">Clear</button>
          <button onclick="refreshTerminal()">Refresh</button>
        </div>
      </div>
      <div class="terminalBody">
        <pre class="terminal" id="terminalOut">(loading...)</pre>
      </div>
      <div class="cmdRow">
        <input id="cmd" placeholder="Run command in repo (e.g., python -m uvicorn web_app:app --reload --port 8787)" />
        <button class="primary" onclick="runCmd()">Run</button>
      </div>
    </div>
  </div>

<script>
  let ALL_FILES = [];
  let ACTIVE_FILE = "";
  let ACTIVE_SHA = "";
  let DIRTY = false;
  let LAST_PLAN = null;

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

  function setDirty(v){
    DIRTY = v;
    const el = document.getElementById("dirtyHint");
    if (!ACTIVE_FILE) { el.textContent = ""; return; }
    el.textContent = DIRTY ? "• Unsaved changes" : "";
  }

  async function loadStatus() {
    const r = await fetch("/api/status", {headers: adminHeaders()});
    const j = await r.json();
    document.getElementById("repoPath").textContent = j.repo_root || "";
    setOut(j);
    setStatus("Ready.", "ok");
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
    if (DIRTY && ACTIVE_FILE && !confirm("You have unsaved changes. Discard and open another file?")) return;
    ACTIVE_FILE = path;
    renderFileList();
    showTab("editor");
    await fetchAndShowFile(path);
  }

  async function fetchAndShowFile(path) {
    document.getElementById("activeFileName").textContent = path;
    document.getElementById("activeFileMeta").textContent = "Loading…";

    const r = await fetch("/api/file?path=" + encodeURIComponent(path), {headers: adminHeaders()});
    const j = await r.json();
    if (j.error) {
      document.getElementById("activeFileMeta").textContent = j.error;
      document.getElementById("fileEditor").value = "";
      ACTIVE_SHA = "";
      setDirty(false);
      return;
    }
    document.getElementById("activeFileMeta").textContent =
      `${j.lines} lines • ${j.size} bytes • ${j.sha256.slice(0,12)}…`;
    document.getElementById("fileEditor").value = j.content;
    ACTIVE_SHA = j.sha256;
    setDirty(false);
  }

  async function refreshActiveFile() {
    if (!ACTIVE_FILE) return;
    if (DIRTY && !confirm("Discard unsaved changes and reload from disk?")) return;
    await fetchAndShowFile(ACTIVE_FILE);
  }

  async function saveActiveFile() {
    if (!ACTIVE_FILE) { alert("No file selected."); return; }
    const content = document.getElementById("fileEditor").value;

    setStatus("Saving...", "warn");
    const r = await fetch("/api/save_file", {
      method:"POST",
      headers: {"Content-Type":"application/json", ...adminHeaders()},
      body: JSON.stringify({path: ACTIVE_FILE, content})
    });
    const j = await r.json();
    setOut(j);
    if (j.error) {
      setStatus("Save failed: " + j.error, "bad");
      return;
    }
    setStatus("Saved.", "ok");
    await fetchAndShowFile(ACTIVE_FILE);
    setDirty(false);
  }

  async function scan() {
    setStatus("Scanning repo…", "warn");
    const r = await fetch("/api/scan", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Scan saved. Now run Diff.", "ok");
    await loadFiles();
    if (ACTIVE_FILE) await fetchAndShowFile(ACTIVE_FILE);
    await refreshTerminal();
  }

  async function diff() {
    setStatus("Computing diff…", "warn");
    const r = await fetch("/api/diff", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Diff saved. Now Plan.", "ok");
    await refreshTerminal();
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
    LAST_PLAN = j.patch_plan || null;
    setStatus("Patch plan generated. Dry-run apply next.", "ok");
    await refreshTerminal();
  }

  async function dryRunApply() {
    setStatus("Dry-run applying patches…", "warn");
    const r = await fetch("/api/apply?dry_run=1", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Dry-run done. Review terminal diff then Apply.", "ok");
    await loadFiles();
    if (ACTIVE_FILE) await fetchAndShowFile(ACTIVE_FILE);
    await refreshTerminal();
  }

  async function applyReal() {
    if (!confirm("Apply patches for real? This edits files and creates backups.")) return;
    setStatus("Applying patches for real…", "warn");
    const r = await fetch("/api/apply?dry_run=0", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus("Applied. Backups saved.", "ok");
    await loadFiles();
    if (ACTIVE_FILE) await fetchAndShowFile(ACTIVE_FILE);
    await refreshTerminal();
  }

  async function refreshTerminal() {
    const r = await fetch("/api/terminal", {headers: adminHeaders()});
    const j = await r.json();
    document.getElementById("terminalOut").textContent = j.text || "";
  }

  async function clearTerminal() {
    await fetch("/api/terminal_clear", {method:"POST", headers: adminHeaders()});
    await refreshTerminal();
  }

  async function runCmd() {
    const cmd = document.getElementById("cmd").value.trim();
    if (!cmd) return;
    setStatus("Running command…", "warn");
    const r = await fetch("/api/run", {
      method:"POST",
      headers: {"Content-Type":"application/json", ...adminHeaders()},
      body: JSON.stringify({cmd})
    });
    const j = await r.json();
    setOut(j);
    setStatus(j.ok ? "Command finished." : "Command failed.", j.ok ? "ok" : "bad");
    await refreshTerminal();
  }

  async function runPlanCommands() {
    setStatus("Running plan commands…", "warn");
    const r = await fetch("/api/run_plan", {method:"POST", headers: adminHeaders()});
    const j = await r.json();
    setOut(j);
    setStatus(j.ok ? "Plan commands finished." : "Plan commands failed.", j.ok ? "ok" : "bad");
    await refreshTerminal();
  }

  // keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === "k") {
      e.preventDefault();
      document.getElementById("fileSearch").focus();
    }
    if (e.ctrlKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveActiveFile();
    }
    if (e.ctrlKey && e.key === "Enter") {
      const goalEl = document.getElementById("goal");
      if (goalEl && document.activeElement === goalEl) plan();
    }
  });

  document.getElementById("fileSearch").addEventListener("input", renderFileList);

  document.getElementById("fileEditor").addEventListener("input", () => {
    if (!ACTIVE_FILE) return;
    setDirty(true);
  });

  // Auto-refresh terminal every 2s
  setInterval(refreshTerminal, 2000);

  (async () => {
    await loadStatus();
    await loadFiles();
    await refreshTerminal();
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
        "terminal_log": str(terminal_log_path()),
        "viewer_limits": {
            "max_files_shown_in_list": 800,
            "max_chars_per_file_view": 160_000
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
        content = read_file_text(path, max_chars=160_000)
        meta = file_meta(path)
        return JSONResponse({
            **meta,
            "content": content
        })
    except HTTPException as e:
        return JSONResponse({"error": e.detail}, status_code=e.status_code)

@app.post("/api/save_file")
async def api_save_file(req: Request) -> JSONResponse:
    require_token(req)
    payload = await req.json()
    path = (payload.get("path") or "").strip()
    content = payload.get("content")
    if not path or content is None:
        raise HTTPException(400, "Missing path/content.")
    try:
        write_file_text(path, str(content))
        term_line(f"Saved file: {path}")
        return JSONResponse({"ok": True, "saved": path, "meta": file_meta(path)})
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
        term_line("Scan (before) created.")

    after = scan_repo(repo_root())
    save_json(after_path, after)
    term_line(f"Scan complete. Files: {after.get('file_count')}")

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
    term_line(f"Diff computed. Added={d['counts']['added']} Modified={d['counts']['modified']} Removed={d['counts']['removed']}")
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

    context_text, chosen = build_llm_context(repo_root(), after, diff, goal)
    term_line("Planning patches (LLM)...")
    plan = plan_patches(goal, context_text)

    save_json(sd / "patches.json", plan)
    save_json(sd / "chosen_files.json", {"chosen_files": chosen})

    # Replit-like: print plan summary/run_commands into terminal
    term_append("\n" + "="*72 + "\n")
    term_line("LLM PATCH PLAN READY")
    term_append(_format_plan_for_terminal(plan))
    term_append("="*72 + "\n\n")

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

    term_line(f"Applying patches (dry_run={bool(dry_run)})...")
    log = apply_patch_plan(repo_root(), plan, dry_run=bool(dry_run))
    save_json(sd / "last_apply_log.json", log)

    # print diffs if available (from upgraded patch_apply.py)
    term_append("\n")
    term_line("APPLY RESULTS:")
    for r in log.get("results", []):
        term_line(f"- {r.get('status')}: {r.get('file')} (ops={r.get('ops')})")
        diff_txt = r.get("diff_unified")
        if diff_txt:
            term_append(diff_txt + "\n")

    return JSONResponse(log)

@app.get("/api/terminal")
def api_terminal(req: Request) -> JSONResponse:
    require_token(req)
    p = terminal_log_path()
    if not p.exists():
        p.write_text("", encoding="utf-8")
    txt = p.read_text(encoding="utf-8", errors="replace")
    # keep response bounded
    max_chars = int(getattr(SETTINGS, "TERMINAL_MAX_CHARS", 200_000))
    if len(txt) > max_chars:
        txt = txt[-max_chars:]
    return JSONResponse({"text": txt})

@app.post("/api/terminal_clear")
def api_terminal_clear(req: Request) -> JSONResponse:
    require_token(req)
    term_clear()
    term_line("Terminal cleared.")
    return JSONResponse({"ok": True})

@app.post("/api/run")
async def api_run(req: Request) -> JSONResponse:
    require_token(req)
    payload = await req.json()
    cmd = (payload.get("cmd") or "").strip()
    if not cmd:
        raise HTTPException(400, "Missing cmd.")
    term_append("\n")
    term_line(f"$ {cmd}")
    res = _run_command(cmd, cwd=repo_root(), timeout=getattr(SETTINGS, "RUN_TIMEOUT_SEC", None))
    if res["stdout"]:
        term_append(res["stdout"] + ("\n" if not res["stdout"].endswith("\n") else ""))
    if res["stderr"]:
        term_append(res["stderr"] + ("\n" if not res["stderr"].endswith("\n") else ""))
    ok = (res["returncode"] == 0)
    term_line(f"Command exit code: {res['returncode']} (elapsed {res['elapsed_sec']}s)")
    return JSONResponse({"ok": ok, **res})

@app.post("/api/run_plan")
def api_run_plan(req: Request) -> JSONResponse:
    require_token(req)
    sd = state_dir()
    plan = load_json(sd / "patches.json")
    if not plan:
        raise HTTPException(400, "No patch plan found. Plan first.")
    cmds = plan.get("run_commands") or []
    if not isinstance(cmds, list) or not cmds:
        return JSONResponse({"ok": True, "note": "Plan has no run_commands.", "results": []})

    results = []
    term_append("\n")
    term_line("RUNNING PLAN COMMANDS...")
    for c in cmds:
        c = str(c).strip()
        if not c:
            continue
        term_line(f"$ {c}")
        res = _run_command(c, cwd=repo_root(), timeout=getattr(SETTINGS, "RUN_TIMEOUT_SEC", None))
        if res["stdout"]:
            term_append(res["stdout"] + ("\n" if not res["stdout"].endswith("\n") else ""))
        if res["stderr"]:
            term_append(res["stderr"] + ("\n" if not res["stderr"].endswith("\n") else ""))
        term_line(f"exit={res['returncode']} elapsed={res['elapsed_sec']}s")
        results.append(res)
        # stop if one fails
        if res["returncode"] != 0 and bool(getattr(SETTINGS, "STOP_ON_RUN_FAIL", True)):
            term_line("Stopping run_plan: command failed.")
            break

    ok = all(r.get("returncode") == 0 for r in results) if results else True
    return JSONResponse({"ok": ok, "results": results})
