"""Lenient JSON extraction for LLM responses.

Anthropic via LiteLLM frequently ignores response_format=json_object and
returns markdown fences or prose-then-JSON. Strict json.loads fails 100%.

This parser handles:
- Plain JSON object
- ```json ... ``` fenced blocks (with or without language tag)
- Prose before/after a JSON object (uses brace counting)
"""
import json
import re


def parse_json_lenient(content: str) -> dict | None:
    if not content:
        return None
    s = content.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(s)):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
