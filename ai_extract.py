"""
ai_extract.py — Claude (Anthropic) extraction backend for Container Data
Extractor.

Takes the normalized output of extractors.py (text blocks + images for one
uploaded file) and asks Claude to pull out one structured record per
container. This is deliberately NOT regex/rules based: real supplier
documents are too inconsistent (scanned vs. digital, embedded spreadsheets,
single MT figures vs. full net/gross/tare breakdowns, different column
orders and header wording) for hand-rolled parsing to hold up. Claude reads
the raw text/tables/images the same way a person would and returns strict
JSON.

Anthropic API docs: https://docs.claude.com

See gemini_extract.py for the equivalent Gemini backend and
extraction_common.py for the schema/prompt shared by both.
"""

from __future__ import annotations

import base64
import json
import re

import anthropic

from extractors import FileExtraction
from extraction_common import (
    MAX_OUTPUT_TOKENS,
    EXTRACTION_INSTRUCTIONS,
    AIExtractionError,
    ExtractionResult,
    normalize_records,
)

# Claude has no first-class JSON-schema-constrained decoding mode via this
# simple messages API path, so the shared instructions are wrapped with an
# explicit "output only JSON" framing.
SYSTEM_PROMPT = (
    EXTRACTION_INSTRUCTIONS
    + "\n\nReturn ONLY a single JSON array (no prose, no markdown code fences, no explanation before "
    "or after). Output ONLY the JSON array."
)


def _build_content_blocks(fx: FileExtraction) -> list:
    blocks = []

    for img in fx.images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.b64encode(img.data).decode("ascii"),
                },
            }
        )
        blocks.append({"type": "text", "text": f"(the image above is: {img.label})"})

    text = fx.combined_text
    if text:
        blocks.append(
            {
                "type": "text",
                "text": f"Extracted text/tables from '{fx.filename}':\n\n{text}",
            }
        )

    if not blocks:
        blocks.append(
            {
                "type": "text",
                "text": f"No text or images could be extracted from '{fx.filename}'.",
            }
        )

    return blocks


def _strip_code_fences(raw: str) -> str:
    raw = raw.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw


def extract_containers(
    api_key: str,
    model: str,
    file_extraction: FileExtraction,
) -> ExtractionResult:
    """Send one file's extracted content to Claude and return parsed records.

    Raises AIExtractionError on any failure (auth, network, malformed
    response) so callers can handle it per-file without killing the batch.
    """
    if not api_key:
        raise AIExtractionError("No Anthropic API key provided.")

    client = anthropic.Anthropic(api_key=api_key)
    content_blocks = _build_content_blocks(file_extraction)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content_blocks}],
        )
    except anthropic.AuthenticationError as exc:
        raise AIExtractionError(
            "Invalid Anthropic API key. Check the key in the sidebar."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise AIExtractionError(
            f"Permission denied by Anthropic API (check model access for '{model}')."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise AIExtractionError(
            "Rate limited by the Anthropic API. Wait a moment and try again."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise AIExtractionError(f"Could not reach the Anthropic API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise AIExtractionError(f"Anthropic API error ({exc.status_code}): {exc.message}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIExtractionError(f"Unexpected error calling Claude: {exc}") from exc

    raw_text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIExtractionError(
            f"Claude's response wasn't valid JSON for '{file_extraction.filename}'. "
            f"Raw response started with: {raw_text[:200]!r}"
        ) from exc

    if not isinstance(parsed, list):
        raise AIExtractionError(
            f"Expected a JSON array from Claude for '{file_extraction.filename}', got {type(parsed).__name__}."
        )

    return ExtractionResult(
        filename=file_extraction.filename,
        records=normalize_records(parsed),
        raw_warnings=list(file_extraction.warnings),
    )
