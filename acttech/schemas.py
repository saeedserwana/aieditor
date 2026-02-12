from __future__ import annotations

PATCH_PLAN_JSON_SCHEMA = {
  "name": "patch_plan",
  "schema": {
    "type": "object",
    "additionalProperties": False,
    "properties": {
      # Replit-like high-level summary (optional)
      "summary": {"type": "string"},
      "notes": {
        "type": "array",
        "items": {"type": "string"}
      },
      "risk_level": {
        "type": "string",
        "enum": ["low", "medium", "high"]
      },

      # Replit-like: what to run in the terminal after apply
      "run_commands": {
        "type": "array",
        "items": {"type": "string"}
      },
      "expected_output": {
        "type": "array",
        "items": {"type": "string"}
      },
      "verification_steps": {
        "type": "array",
        "items": {"type": "string"}
      },

      # Core patch plan (same as before)
      "files": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": False,
          "properties": {
            "path": {"type": "string"},
            "why": {"type": "string"},  # why this file is being changed (optional)

            "ops": {
              "type": "array",
              "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                  "type": {
                    "type": "string",
                    "enum": [
                      "replace_text",
                      "replace_range",
                      "insert_after",
                      "insert_before",
                      "append",
                      "delete_range"
                    ]
                  },

                  # replace_text
                  "find": {"type": "string"},
                  "replace": {"type": "string"},
                  "count": {"type": ["integer", "null"]},

                  # replace_range / delete_range
                  "start_line": {"type": "integer"},
                  "end_line": {"type": "integer"},
                  "new_text": {"type": "string"},

                  # insert_after / insert_before
                  "match": {"type": "string"},
                  "insert_text": {"type": "string"},
                  "once": {"type": "boolean"},

                  # append
                  "text": {"type": "string"},

                  # Replit-like: per-op justification (optional)
                  "why": {"type": "string"}
                },
                "required": ["type"]
              }
            }
          },
          "required": ["path", "ops"]
        }
      }
    },

    # ✅ Backward compatible: only "files" is required
    "required": ["files"]
  }
}
