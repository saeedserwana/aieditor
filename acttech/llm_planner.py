from __future__ import annotations

import json
import os
from typing import Dict, Any

from openai import OpenAI

from config import SETTINGS
from schemas import PATCH_PLAN_JSON_SCHEMA

SYSTEM_INSTRUCTIONS = """You are a code-change planner.
You MUST output ONLY valid JSON matching the provided JSON Schema.

Goal:
- Choose the correct file(s) and create precise patch ops.
- Be minimal: change as little as possible.
- Do NOT invent files that do not exist in the provided repo list/snippets.
- If the request is unclear, output {"files": []}.
"""

def plan_patches(goal: str, context_text: str) -> Dict[str, Any]:
    api_key = SETTINGS.OPENAI_API_KEY.strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Put it in config.py or set env var OPENAI_API_KEY.")

    client = OpenAI(api_key=api_key)

    resp = client.responses.create(
        model=SETTINGS.MODEL,
        instructions=SYSTEM_INSTRUCTIONS,
        input=f"GOAL:\n{goal}\n\nCONTEXT:\n{context_text}\n",
        response_format={
            "type": "json_schema",
            "json_schema": PATCH_PLAN_JSON_SCHEMA
        }
    )

    text = resp.output_text
    return json.loads(text)
