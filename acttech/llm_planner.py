from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Any, Optional, Tuple

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # fallback safety

from config import SETTINGS
from schemas import PATCH_PLAN_JSON_SCHEMA

SYSTEM_INSTRUCTIONS = """You are a code-change planner inside an offline IDE (Replit-like).
You MUST output ONLY valid JSON matching the provided JSON Schema. No extra text.

Your job:
- Choose correct existing file(s) from the provided repo list/snippets.
- Create precise patch operations (minimal changes).
- Never invent files or paths that do not exist.
- Prefer small, surgical edits.
- If unclear, output {"files": []}.
"""


# ----------------------------
# Terminal Logging
# ----------------------------

def _log(msg: str) -> None:
    print(f"[planner] {msg}", flush=True)


# ----------------------------
# JSON Extraction
# ----------------------------

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

def _extract_json_object(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "{}"

    if text.startswith("{") and text.endswith("}"):
        return text

    m = _JSON_OBJ_RE.search(text)
    if m:
        return m.group(0).strip()

    return "{}"


def _basic_plan_sanity(plan: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(plan, dict):
        return False, "plan is not dict"

    if "files" not in plan:
        return False, "missing files key"

    if not isinstance(plan["files"], list):
        return False, "files is not list"

    for i, f in enumerate(plan["files"]):
        if not isinstance(f, dict):
            return False, f"files[{i}] not object"

    return True, "ok"


# ----------------------------
# Prompt Trimming
# ----------------------------

def _trim_context(goal: str, context_text: str, max_chars: int) -> str:
    goal = (goal or "").strip()
    context_text = (context_text or "").strip()

    combined = f"GOAL:\n{goal}\n\nCONTEXT:\n{context_text}"

    if len(combined) <= max_chars:
        return combined

    head = combined[: max_chars // 2]
    tail = combined[-(max_chars // 2) :]
    return head + "\n\n...[TRIMMED]...\n\n" + tail


# ----------------------------
# OpenAI Client
# ----------------------------

def _client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("openai package not installed. Run: pip install openai>=1.0.0")

    api_key = (
        getattr(SETTINGS, "OPENAI_API_KEY", "") or
        os.getenv("OPENAI_API_KEY", "")
    ).strip()

    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY.")

    base_url = getattr(SETTINGS, "OPENAI_BASE_URL", "") or None

    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)

    return OpenAI(api_key=api_key)


def _call_model(client: OpenAI, prompt: str) -> str:
    resp = client.responses.create(
        model=SETTINGS.MODEL,
        instructions=SYSTEM_INSTRUCTIONS,
        input=prompt,
        response_format={
            "type": "json_schema",
            "json_schema": PATCH_PLAN_JSON_SCHEMA
        }
    )
    return resp.output_text or ""


# ----------------------------
# Main Planner
# ----------------------------

def plan_patches(goal: str, context_text: str) -> Dict[str, Any]:

    client = _client()

    max_chars = int(getattr(SETTINGS, "PLANNER_MAX_CHARS", 200_000))
    prompt = _trim_context(goal, context_text, max_chars)

    _log("Planning patches...")
    _log(f"Model: {SETTINGS.MODEL}")
    _log(f"Prompt size: {len(prompt):,} chars")

    last_text: Optional[str] = None

    for attempt in range(1, 4):

        if attempt == 1:
            raw = _call_model(client, prompt)
        else:
            _log(f"Repair attempt {attempt-1}")
            repair_prompt = (
                "Return ONLY valid JSON matching the schema.\n\n"
                f"GOAL:\n{goal}\n\n"
                f"PREVIOUS OUTPUT:\n{last_text}"
            )
            raw = _call_model(client, repair_prompt)

        last_text = raw
        extracted = _extract_json_object(raw)

        try:
            plan = json.loads(extracted)
        except Exception as e:
            _log(f"JSON parse error: {e}")
            time.sleep(0.4)
            continue

        ok, reason = _basic_plan_sanity(plan)
        if ok:
            _log(f"Plan OK. Files: {len(plan.get('files', []))}")
            return plan

        _log(f"Schema check failed: {reason}")
        time.sleep(0.4)

    _log("Failed after retries. Returning empty plan.")
    return {"files": []}
