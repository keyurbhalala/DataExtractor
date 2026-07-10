"""
db.py — Supabase (Postgres) persistence layer for Container Data Extractor.

Streamlit Community Cloud's filesystem is ephemeral: anything written to
local disk (including a local SQLite file) disappears on app restart or
redeploy. This module persists extracted records to a Supabase Postgres
project instead, using SUPABASE_URL / SUPABASE_KEY read from Streamlit
secrets (never hardcoded, never committed).

See README.md → "Persistent history (Supabase)" for the exact SQL used to
create the `extracted_documents` table in your own Supabase project.

Save behavior (see README for the full rationale):
- Rows are auto-saved to Supabase immediately after extraction completes,
  with `edited = false`. This guarantees nothing is lost even if the user
  never touches the results table or closes the tab.
- An explicit "Save edits to history" button re-syncs the *current* state
  of the editable table back to those same rows (by id) and flags any
  changed row `edited = true`. New rows the user adds by hand in the editor
  are inserted fresh; this only fires on an explicit click so we're not
  hammering the database on every keystroke in `st.data_editor`.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st

TABLE = "extracted_documents"

# App-side dataframe column -> Supabase column name. Only "weight_unit"
# differs: the extraction pipeline already produces a `weight_unit` column
# in the session dataframe, so we keep that name in the UI and map it to
# the agreed-upon `unit` column when talking to the database.
APP_TO_DB = {
    "source_file": "source_file",
    "container_number": "container_number",
    "seal_number": "seal_number",
    "bales": "bales",
    "net_weight": "net_weight",
    "gross_weight": "gross_weight",
    "tare_weight": "tare_weight",
    "weight_unit": "unit",
    "notes": "notes",
}
DB_TO_APP = {v: k for k, v in APP_TO_DB.items()}

HISTORY_COLUMNS = [
    "id",
    "batch_id",
    "source_file",
    "container_number",
    "seal_number",
    "bales",
    "net_weight",
    "gross_weight",
    "tare_weight",
    "unit",
    "notes",
    "extracted_at",
    "edited",
]

TEXT_SEARCH_COLUMNS = ["source_file", "container_number", "seal_number", "unit", "notes"]


class SupabaseNotConfigured(RuntimeError):
    """Raised when SUPABASE_URL / SUPABASE_KEY aren't present in secrets."""


class SupabaseError(RuntimeError):
    """Raised when a Supabase call fails (network, permissions, schema, etc.)."""


@st.cache_resource(show_spinner=False)
def get_client():
    """Return a cached Supabase client, or raise SupabaseNotConfigured."""
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception as exc:  # noqa: BLE001 - secrets.toml may not exist locally
        raise SupabaseNotConfigured(
            "SUPABASE_URL / SUPABASE_KEY not found in Streamlit secrets."
        ) from exc

    if not url or not key:
        raise SupabaseNotConfigured("SUPABASE_URL / SUPABASE_KEY are empty.")

    try:
        from supabase import create_client
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SupabaseNotConfigured(
            "The `supabase` package isn't installed. Add it to requirements.txt."
        ) from exc

    return create_client(url, key)


def is_configured() -> bool:
    try:
        get_client()
        return True
    except SupabaseNotConfigured:
        return False
    except Exception:  # noqa: BLE001
        return False


def _clean(value):
    """Convert pandas NaN/NaT and empty strings to None for JSON/Postgres."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _row_to_db(row: dict, batch_id: str, edited: bool = False) -> dict:
    db_row = {"batch_id": batch_id}
    for app_col, db_col in APP_TO_DB.items():
        db_row[db_col] = _clean(row.get(app_col))
    if db_row.get("bales") is not None:
        try:
            db_row["bales"] = int(db_row["bales"])
        except (TypeError, ValueError):
            db_row["bales"] = None
    db_row["edited"] = edited
    return db_row


def insert_rows(rows: list[dict], batch_id: str) -> list[dict]:
    """Insert new rows for one extraction batch.

    Returns the inserted rows as stored by Supabase (including their new
    `id`s), in the same order as `rows`.
    """
    if not rows:
        return []
    client = get_client()
    payload = [_row_to_db(r, batch_id) for r in rows]
    try:
        resp = client.table(TABLE).insert(payload).execute()
    except Exception as exc:  # noqa: BLE001
        raise SupabaseError(f"Could not save records to Supabase: {exc}") from exc
    return resp.data or []


def update_row(row_id: str, row: dict, batch_id: str) -> None:
    """Overwrite a previously-saved row (used when re-saving edits) and
    flag it as manually edited."""
    client = get_client()
    db_row = _row_to_db(row, batch_id, edited=True)
    try:
        client.table(TABLE).update(db_row).eq("id", row_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise SupabaseError(f"Could not update record {row_id} in Supabase: {exc}") from exc


def delete_row(row_id) -> None:
    client = get_client()
    try:
        client.table(TABLE).delete().eq("id", row_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise SupabaseError(f"Could not delete record {row_id} from Supabase: {exc}") from exc


def fetch_history(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    container_query: str = "",
    source_file_query: str = "",
    search_text: str = "",
    limit: int = 5000,
) -> pd.DataFrame:
    """Fetch historical records, most recent first.

    Date range, container-number partial match, and source-file partial
    match are pushed down as Postgres filters. The free-text search box
    (which spans several text columns at once) is applied client-side after
    the fetch — simpler and more portable than composing an OR-across-columns
    filter through the Python client, and the row counts here are small
    enough that it's not a performance concern.
    """
    client = get_client()
    q = client.table(TABLE).select("*").order("extracted_at", desc=True).limit(limit)

    if date_from is not None:
        q = q.gte("extracted_at", date_from.isoformat())
    if date_to is not None:
        q = q.lte("extracted_at", f"{date_to.isoformat()}T23:59:59.999")
    if container_query:
        q = q.ilike("container_number", f"%{container_query}%")
    if source_file_query:
        q = q.ilike("source_file", f"%{source_file_query}%")

    try:
        resp = q.execute()
    except Exception as exc:  # noqa: BLE001
        raise SupabaseError(f"Could not load history from Supabase: {exc}") from exc

    df = pd.DataFrame(resp.data or [])
    for col in HISTORY_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[HISTORY_COLUMNS]

    if search_text and not df.empty:
        mask = pd.Series(False, index=df.index)
        for col in TEXT_SEARCH_COLUMNS:
            mask |= df[col].astype(str).str.contains(search_text, case=False, na=False, regex=False)
        df = df[mask]

    return df
