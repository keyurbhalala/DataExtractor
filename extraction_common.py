"""
extraction_common.py — shared types, schema, and prompt text used by both
AI extraction backends (ai_extract.py for Claude, gemini_extract.py for
Gemini). Keeping this in one place means the two providers stay in sync on
what fields they extract and how they're instructed, so results are
comparable/mergeable regardless of which model produced them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from extractors import FileExtraction

# Fields every extracted container record will have (value or null — never
# invented).
RECORD_FIELDS = [
    "container_number",
    "seal_number",
    "bales",
    "net_weight",
    "gross_weight",
    "tare_weight",
    "weight_unit",
    "notes",
]

MAX_OUTPUT_TOKENS = 8192

# Provider-agnostic instructions. Claude gets this as its system prompt with
# an added "return only JSON" wrapper (it has no native JSON-schema mode).
# Gemini gets this as its system instruction alongside an actual JSON schema
# passed via response_mime_type/response_json_schema, so it doesn't need the
# "output only JSON" framing repeated in the same way.
EXTRACTION_INSTRUCTIONS = """You are a meticulous data-extraction assistant for international shipping and \
freight-forwarding paperwork (commercial invoices, packing lists, customs declarations, weighbridge \
tickets). You will be shown the raw text, tables, and/or images from ONE supplier document.

Your job: find every shipping container referenced in the document and return one JSON object per \
container describing it.

If the document contains no container-level shipping data at all, return an empty array: []

Each object must have exactly these keys:
- "container_number": string, e.g. "CSNU6300427". Null if not present.
- "seal_number": string. Null if not present.
- "bales": integer count of bales/packages for that container. Null if not present.
- "net_weight": number. Null if not present.
- "gross_weight": number. Null if not present.
- "tare_weight": number. Null if not present.
- "weight_unit": string, e.g. "kg", "MT", "lbs" — whatever unit the source document actually uses. \
Null if no weight at all is present.
- "notes": short free-text string for anything ambiguous, e.g. "only a single MT figure given, not \
split into net/gross/tare — recorded as net_weight" or "bale count only given as a document-level \
total, not per container" or "seal number illegible in scan". Null if nothing noteworthy.

Critical rules:
1. NEVER invent or guess a value. If a field isn't in the document, its value is null. Do not compute \
tare_weight from gross minus net (or any other derived math) unless the document already states it — \
only extract what's written, though you may note in "notes" that it's derivable.
2. Some commercial invoices give only a single weight figure per container (often in metric tons, \
"MT") rather than a net/gross/tare breakdown. In that case put the figure in "net_weight" (the closest \
common-sense mapping — an invoice quantity is typically the net/shipped weight) and explain the \
ambiguity in "notes". Do not split one figure across multiple fields.
3. Skip summary/total rows (e.g. a "TOTAL" or "TOTALS" line aggregating all containers) — only emit one \
record per actual container.
4. If the same container appears more than once in the document (e.g. listed in both a table and a \
paragraph), merge into a single record rather than duplicating it.
5. Numbers should be plain JSON numbers (no thousands separators, no currency symbols, no units baked \
into the string).
6. Container numbers should be transcribed exactly as written (they follow the ISO 6346 pattern of 4 \
letters + 7 digits, e.g. "TRHU8236331", but transcribe what's actually on the page even if it looks \
slightly off — don't "correct" it).
7. When multiple images are provided for the same document (e.g. multiple scanned pages), treat them \
as one continuous document and de-duplicate containers across pages."""

# JSON Schema for the array-of-container-records output. Used directly by
# Gemini's native structured-output mode; Claude gets the equivalent shape
# described in prose within EXTRACTION_INSTRUCTIONS since it has no
# JSON-schema-constrained decoding mode via this simple API path.
RESPONSE_JSON_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "container_number": {"type": ["string", "null"]},
            "seal_number": {"type": ["string", "null"]},
            "bales": {"type": ["integer", "null"]},
            "net_weight": {"type": ["number", "null"]},
            "gross_weight": {"type": ["number", "null"]},
            "tare_weight": {"type": ["number", "null"]},
            "weight_unit": {"type": ["string", "null"]},
            "notes": {"type": ["string", "null"]},
        },
        "required": list(RECORD_FIELDS),
    },
}


class AIExtractionError(RuntimeError):
    """Raised for any AI-provider call failure (bad key, rate limit, bad response, etc.)."""


@dataclass
class ExtractionResult:
    filename: str
    records: List[dict]
    raw_warnings: List[str]


def normalize_record(rec: dict) -> dict:
    normalized = {}
    for key in RECORD_FIELDS:
        value = rec.get(key)
        if value == "":
            value = None
        normalized[key] = value

    if normalized["bales"] is not None:
        try:
            normalized["bales"] = int(float(normalized["bales"]))
        except (TypeError, ValueError):
            pass  # leave as-is; user can fix in the editable table

    for weight_key in ("net_weight", "gross_weight", "tare_weight"):
        if normalized[weight_key] is not None:
            try:
                normalized[weight_key] = float(normalized[weight_key])
            except (TypeError, ValueError):
                pass

    return normalized


def normalize_records(records: list) -> List[dict]:
    return [normalize_record(rec) for rec in records if isinstance(rec, dict)]
