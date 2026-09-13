"""Validate model-written sections before verification or template iteration.

Accept a paragraph field returned as one string by wrapping/splitting it into
paragraphs, never by calling list(text). This preserves every numeric token.
Other structural errors are rejected and sent through the generation retry loop.
"""
from __future__ import annotations
import json
import re
from markupsafe import Markup, escape


def emphasised_html(text: str) -> Markup:
    """Only paired **bold** is markup; all model-supplied HTML is escaped."""
    return Markup(re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", str(escape(text))))

TEXT_FIELDS = ("greeting", "performance", "macro", "coverage", "closing")
PARAGRAPH_FIELDS = ("highlights", "recommendations")


def normalize_sections(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Letter must be a JSON object")
    required = set(TEXT_FIELDS + PARAGRAPH_FIELDS)
    if set(value) != required:
        raise ValueError(f"Missing fields: {sorted(required - set(value))}; unexpected fields: {sorted(set(value) - required)}")
    result = {}
    for field in TEXT_FIELDS:
        text = value[field]
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{field} must be a nonempty string")
        result[field] = text.strip()
    for field in PARAGRAPH_FIELDS:
        paragraphs = value[field]
        if isinstance(paragraphs, str):
            paragraphs = re.split(r"\n\s*\n", paragraphs.strip())
        if (not isinstance(paragraphs, list) or not paragraphs
                or any(not isinstance(p, str) or not p.strip() for p in paragraphs)):
            raise ValueError(f"{field} must be an array of nonempty paragraph strings")
        result[field] = [p.strip() for p in paragraphs]
    return result


def parse_sections(raw: str) -> dict:
    text = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.I)
    if fence:
        text = fence.group(1)
    return normalize_sections(json.loads(text))


def flatten_sections(sections: dict, advisor: str) -> str:
    sections = normalize_sections(sections)
    parts = sections["highlights"] + [sections[k] for k in ("greeting", "performance", "macro")]
    parts += sections["recommendations"]
    parts += [sections["coverage"], sections["closing"], advisor]
    return re.sub(r"\*\*([^*]+)\*\*", r"\1", "\n\n".join(parts))
