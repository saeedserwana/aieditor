from __future__ import annotations

PATCH_PLAN_JSON_SCHEMA = {
  "name": "patch_plan",
  "schema": {
    "type": "object",
    "additionalProperties": False,
    "properties": {
      "files": {
        "type": "array",
        "items": {
          "type": "object",
          "additionalProperties": False,
          "properties": {
            "path": {"type": "string"},
            "ops": {
              "type": "array",
              "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                  "type": {"type": "string", "enum": ["replace_text", "replace_range", "insert_after"]},

                  "find": {"type": "string"},
                  "replace": {"type": "string"},
                  "count": {"type": ["integer", "null"]},

                  "start_line": {"type": "integer"},
                  "end_line": {"type": "integer"},
                  "new_text": {"type": "string"},

                  "match": {"type": "string"},
                  "insert_text": {"type": "string"},
                  "once": {"type": "boolean"}
                },
                "required": ["type"]
              }
            }
          },
          "required": ["path", "ops"]
        }
      }
    },
    "required": ["files"]
  }
}
