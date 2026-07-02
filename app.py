"""
Container Data Extractor — Streamlit app.

Upload shipping paperwork (commercial invoices, packing lists, customs
declarations) in PDF / DOCX / XLSX / XLSM / CSV / JPG / PNG format and get
back a clean, mergeable, editable table of container number, seal number,
weight (net/gross/tare), and bale quantity — extracted by an AI model
(Claude or Gemini, your choice) rather than brittle per-format regex rules.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from extractors import extract_file, ExtractionError, SUPPORTED_EXTENSIONS
from extraction_common import RECORD_FIELDS, AIExtractionError
import ai_extract
import gemini_extract

st.set_page_config(
    page_title="Container Data Extractor",
    page_icon="🚢",
    layout="wide",
)

PROVIDERS = {
    "Claude (Anthropic)": {
        "module": ai_extract,
        "secret_key": "ANTHROPIC_API_KEY",
        "key_label": "Anthropic API key",
        "key_help": (
            "Session-only — never stored or logged. Leave blank to fall back "
            "to the ANTHROPIC_API_KEY value in Streamlit secrets, if set. "
            "Get a key at console.anthropic.com/settings/keys."
        ),
        "models": ["claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5-20251001"],
    },
    "Gemini (Google)": {
        "module": gemini_extract,
        "secret_key": "GEMINI_API_KEY",
        "key_label": "Gemini API key",
        "key_help": (
            "Session-only — never stored or logged. Leave blank to fall back "
            "to the GEMINI_API_KEY value in Streamlit secrets, if set. "
            "Get a key at aistudio.google.com/apikey."
        ),
        "models": ["gemini-3.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
    },
}

DISPLAY_COLUMNS = [
    "source_file",
    "container_number",
    "seal_number",
    "bales",
    "net_weight",
    "gross_weight",
    "tare_weight",
    "weight_unit",
    "notes",
]

# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

if "results" not in st.session_state:
    st.session_state.results = pd.DataFrame(columns=DISPLAY_COLUMNS)
if "file_log" not in st.session_state:
    st.session_state.file_log = []  # list of dicts: filename, status, detail
if "processed_signatures" not in st.session_state:
    st.session_state.processed_signatures = set()


def _resolve_api_key(secret_key: str) -> str:
    try:
        return st.secrets[secret_key]
    except Exception:  # noqa: BLE001 - secrets.toml may not exist locally
        return ""


def _file_signature(uploaded_file, provider_name: str) -> tuple:
    return (uploaded_file.name, uploaded_file.size, provider_name)


def _records_to_rows(filename: str, records: list[dict]) -> list[dict]:
    rows = []
    for rec in records:
        row = {"source_file": filename}
        for field_name in RECORD_FIELDS:
            row[field_name] = rec.get(field_name)
        rows.append(row)
    return rows


def _to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name="Containers")
        worksheet = writer.sheets["Containers"]
        for i, col in enumerate(df.columns):
            width = max(12, min(40, int(df[col].astype(str).map(len).max() if len(df) else 10) + 2))
            worksheet.set_column(i, i, width)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")

    provider_name = st.radio("AI provider", list(PROVIDERS.keys()), index=0)
    provider = PROVIDERS[provider_name]

    api_key = _resolve_api_key(provider["secret_key"])

    if api_key:
        st.success("API key set (from secrets)")
    else:
        st.warning(f"No {provider['key_label']} found in Streamlit secrets.")

    model = st.selectbox("Model", provider["models"], index=0, key=f"model_select__{provider_name}")
    with st.expander("Use a different model ID"):
        custom_model = st.text_input(
            "Custom model ID (overrides dropdown if filled in)",
            value="",
            help=(
                "Check docs.claude.com/en/docs/about-claude/models or "
                "ai.google.dev/gemini-api/docs/models for current model IDs."
            ),
            key=f"custom_model__{provider_name}",
        )
    if custom_model.strip():
        model = custom_model.strip()

    st.caption(f"Active: **{provider_name}** — `{model}`")

    st.divider()
    st.caption(
        "Supported formats: " + ", ".join(sorted(f".{e}" for e in SUPPORTED_EXTENSIONS))
    )
    st.caption(
        "Files are sent to the selected AI provider's API for extraction. "
        "Nothing is stored server-side beyond this browser session."
    )

# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

st.title("🚢 Container Data Extractor")
st.markdown(
    "Upload commercial invoices, packing lists, or customs declarations — "
    "any mix of PDF, DOCX, XLSX/XLSM, CSV, JPG, or PNG — and get a merged, "
    "editable table of container number, seal number, bale count, and "
    "net/gross/tare weight."
)

# --------------------------------------------------------------------------
# Upload + process
# --------------------------------------------------------------------------

uploaded_files = st.file_uploader(
    "Upload shipping documents",
    type=sorted(SUPPORTED_EXTENSIONS),
    accept_multiple_files=True,
)

col_a, col_b = st.columns([1, 1])
with col_a:
    process_clicked = st.button("Extract data", type="primary", disabled=not uploaded_files)
with col_b:
    clear_clicked = st.button("Clear all results")

if clear_clicked:
    st.session_state.results = pd.DataFrame(columns=DISPLAY_COLUMNS)
    st.session_state.file_log = []
    st.session_state.processed_signatures = set()
    st.rerun()

if process_clicked:
    if not api_key:
        st.error(f"Add {provider['secret_key']} to this app's Streamlit secrets first.")
    else:
        new_files = [
            f for f in uploaded_files
            if _file_signature(f, provider_name) not in st.session_state.processed_signatures
        ]
        if not new_files:
            st.info("All uploaded files have already been processed with this provider. Upload new files, switch provider, or clear results to re-run.")
        else:
            progress = st.progress(0.0, text="Starting…")
            status_box = st.status("Processing files…", expanded=True)
            all_new_rows = []

            for idx, uf in enumerate(new_files, start=1):
                progress.progress((idx - 1) / len(new_files), text=f"Reading {uf.name}…")
                file_bytes = uf.getvalue()

                sig = _file_signature(uf, provider_name)

                try:
                    fx = extract_file(uf.name, file_bytes)
                except ExtractionError as exc:
                    status_box.write(f"❌ **{uf.name}** — could not read file: {exc}")
                    st.session_state.file_log.append(
                        {"filename": uf.name, "status": "error", "detail": str(exc)}
                    )
                    st.session_state.processed_signatures.add(sig)
                    continue

                for w in fx.warnings:
                    status_box.write(f"⚠️ **{uf.name}** — {w}")

                progress.progress((idx - 0.5) / len(new_files), text=f"Extracting with {provider_name}: {uf.name}…")

                try:
                    result = provider["module"].extract_containers(
                        api_key=api_key, model=model, file_extraction=fx
                    )
                except AIExtractionError as exc:
                    status_box.write(f"❌ **{uf.name}** — {provider_name} extraction failed: {exc}")
                    st.session_state.file_log.append(
                        {"filename": uf.name, "status": "error", "detail": str(exc)}
                    )
                    st.session_state.processed_signatures.add(sig)
                    continue

                n_records = len(result.records)
                if n_records == 0:
                    status_box.write(f"ℹ️ **{uf.name}** — no container records found.")
                else:
                    status_box.write(f"✅ **{uf.name}** — found {n_records} container record(s).")

                all_new_rows.extend(_records_to_rows(uf.name, result.records))
                st.session_state.file_log.append(
                    {"filename": uf.name, "status": "ok", "detail": f"{n_records} record(s)"}
                )
                st.session_state.processed_signatures.add(sig)

            progress.progress(1.0, text="Done.")
            status_box.update(label="Processing complete", state="complete", expanded=False)

            if all_new_rows:
                new_df = pd.DataFrame(all_new_rows, columns=DISPLAY_COLUMNS)
                st.session_state.results = pd.concat(
                    [st.session_state.results, new_df], ignore_index=True
                )

# --------------------------------------------------------------------------
# Per-file log (errors/info), shown if anything happened
# --------------------------------------------------------------------------

if st.session_state.file_log:
    with st.expander(f"File processing log ({len(st.session_state.file_log)} file(s))", expanded=False):
        for entry in st.session_state.file_log:
            icon = "✅" if entry["status"] == "ok" else "❌"
            st.write(f"{icon} **{entry['filename']}** — {entry['detail']}")

# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

results_df = st.session_state.results
n_containers = len(results_df)
n_bales = pd.to_numeric(results_df["bales"], errors="coerce").sum() if n_containers else 0
n_files = len({e["filename"] for e in st.session_state.file_log if e["status"] == "ok"})

m1, m2, m3 = st.columns(3)
m1.metric("Containers found", n_containers)
m2.metric("Total bales", int(n_bales) if pd.notna(n_bales) else 0)
m3.metric("Files processed", n_files)

# --------------------------------------------------------------------------
# Editable results table
# --------------------------------------------------------------------------

st.subheader("Results")
st.caption("Fix any misread field directly in the table before exporting.")

edited_df = st.data_editor(
    st.session_state.results,
    num_rows="dynamic",
    use_container_width=True,
    key="results_editor",
    column_config={
        "source_file": st.column_config.TextColumn("Source file", disabled=True),
        "container_number": st.column_config.TextColumn("Container #"),
        "seal_number": st.column_config.TextColumn("Seal #"),
        "bales": st.column_config.NumberColumn("Bales", format="%d"),
        "net_weight": st.column_config.NumberColumn("Net weight"),
        "gross_weight": st.column_config.NumberColumn("Gross weight"),
        "tare_weight": st.column_config.NumberColumn("Tare weight"),
        "weight_unit": st.column_config.TextColumn("Unit"),
        "notes": st.column_config.TextColumn("Notes", width="large"),
    },
)
st.session_state.results = edited_df

# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

st.subheader("Export")
if edited_df.empty:
    st.caption("Nothing to export yet — process some files first.")
else:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_bytes = edited_df.to_csv(index=False).encode("utf-8")
    excel_bytes = _to_excel_bytes(edited_df)

    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button(
            "Download CSV",
            data=csv_bytes,
            file_name=f"container_data_{timestamp}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with ec2:
        st.download_button(
            "Download Excel (.xlsx)",
            data=excel_bytes,
            file_name=f"container_data_{timestamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
