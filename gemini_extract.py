"""
gemini_extract.py — Google Gemini extraction backend for Container Data
Extractor.

Mirrors ai_extract.py (the Claude backend) but talks to the Gemini API via
the `google-genai` SDK. Uses Gemini's native structured-output mode
(response_mime_type="application/json" + a JSON Schema) rather than
prompt-based "return only JSON" framing, since Gemini can enforce the shape
directly.

Gemini API docs: https://ai.google.dev/gemini-api/docs
"""

from __future__ import annotations

import json

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from extractors import FileExtraction
from extraction_common import (
    MAX_OUTPUT_TOKENS,
    EXTRACTION_INSTRUCTIONS,
    RESPONSE_JSON_SCHEMA,
    AIExtractionError,
    ExtractionResult,
    normalize_records,
)


def _build_contents(fx: FileExtraction) -> list:
    parts = []

    for img in fx.images:
        parts.append(genai_types.Part.from_text(text=f"(the image below is: {img.label})"))
        parts.append(genai_types.Part.from_bytes(data=img.data, mime_type=img.media_type))

    text = fx.combined_text
    if text:
        parts.append(
            genai_types.Part.from_text(
                text=f"Extracted text/tables from '{fx.filename}':\n\n{text}"
            )
        )

    if not parts:
        parts.append(
            genai_types.Part.from_text(
                text=f"No text or images could be extracted from '{fx.filename}'."
            )
        )

    return parts


def extract_containers(
    api_key: str,
    model: str,
    file_extraction: FileExtraction,
) -> ExtractionResult:
    """Send one file's extracted content to Gemini and return parsed records.

    Raises AIExtractionError on any failure (auth, network, malformed
    response) so callers can handle it per-file without killing the batch.
    """
    if not api_key:
        raise AIExtractionError("No Gemini API key provided.")

    client = genai.Client(api_key=api_key)
    contents = _build_contents(file_extraction)

    config = genai_types.GenerateContentConfig(
        system_instruction=EXTRACTION_INSTRUCTIONS,
        response_mime_type="application/json",
        response_json_schema=RESPONSE_JSON_SCHEMA,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
    except genai_errors.ClientError as exc:
        code = getattr(exc, "code", None)
        if code in (401, 403):
            raise AIExtractionError(
                "Invalid or unauthorized Gemini API key. Check the key in the sidebar."
            ) from exc
        if code == 429:
            raise AIExtractionError(
                "Rate limited by the Gemini API. Wait a moment and try again."
            ) from exc
        raise AIExtractionError(f"Gemini API error ({code}): {getattr(exc, 'message', exc)}") from exc
    except genai_errors.ServerError as exc:
        raise AIExtractionError(
            f"Gemini API server error ({getattr(exc, 'code', '?')}): {getattr(exc, 'message', exc)}"
        ) from exc
    except genai_errors.APIError as exc:
        raise AIExtractionError(f"Gemini API error: {getattr(exc, 'message', exc)}") from exc
    except Exception as exc:  # noqa: BLE001
        raise AIExtractionError(f"Unexpected error calling Gemini: {exc}") from exc

    raw_text = getattr(response, "text", None)
    if raw_text is None:
        raise AIExtractionError(
            f"Gemini returned no text output for '{file_extraction.filename}' "
            "(the response may have been blocked by safety filters)."
        )

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AIExtractionError(
            f"Gemini's response wasn't valid JSON for '{file_extraction.filename}'. "
            f"Raw response started with: {raw_text[:200]!r}"
        ) from exc

    if not isinstance(parsed, list):
        raise AIExtractionError(
            f"Expected a JSON array from Gemini for '{file_extraction.filename}', got {type(parsed).__name__}."
        )

    return ExtractionResult(
        filename=file_extraction.filename,
        records=normalize_records(parsed),
        raw_warnings=list(file_extraction.warnings),
    )
