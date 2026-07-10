"""
Container Data Extractor — Streamlit app.

Upload shipping paperwork (commercial invoices, packing lists, customs
declarations) in PDF / DOCX / XLSX / XLSM / CSV / JPG / PNG format and get
back a clean, mergeable, editable table of container number, seal number,
weight (net/gross/tare), and bale quantity — extracted by an AI model
(Claude or Gemini, your choice) rather than brittle per-format regex rules.

Results are auto-saved to a Supabase (Postgres) table as soon as extraction
completes, and again on demand (via "Save edits to history") after you
correct anything in the table, so nothing is lost between sessions or
redeploys. See the History page for everything ever extracted.
"""

from __future__ import annotations

import io
import uuid
from datetime import datetime, date, timedelta

import pandas as pd
import streamlit as st

from extractors import extract_file, ExtractionError, SUPPORTED_EXTENSIONS
from extraction_common import RECORD_FIELDS, AIExtractionError
import ai_extract
import gemini_extract
import db
import theme

st.set_page_config(
    page_title="Container Data Extractor",
    page_icon="🚢",
    layout="wide",
)

st.markdown(theme.inject_css(), unsafe_allow_html=True)

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

HISTORY_EXPORT_COLUMNS = [
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

# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

if "results" not in st.session_state:
    st.session_state.results = pd.DataFrame(columns=DISPLAY_COLUMNS)
if "file_log" not in st.session_state:
    st.session_state.file_log = []  # list of dicts: filename, status, detail
if "processed_signatures" not in st.session_state:
    st.session_state.processed_signatures = set()
if "row_ids" not in st.session_state:
    st.session_state.row_ids = {}  # df index -> Supabase row id (or None if unsaved)
if "row_batch" not in st.session_state:
    st.session_state.row_batch = {}  # df index -> batch_id
if "row_saved_values" not in st.session_state:
    st.session_state.row_saved_values = {}  # df index -> dict of last-saved field values
if "nav" not in st.session_state:
    st.session_state.nav = "Extract"


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


def _to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Containers") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        worksheet = writer.sheets[sheet_name]
        for i, col in enumerate(df.columns):
            width = max(12, min(40, int(df[col].astype(str).map(len).max() if len(df) else 10) + 2))
            worksheet.set_column(i, i, width)
    return buf.getvalue()


def _reset_results():
    st.session_state.results = pd.DataFrame(columns=DISPLAY_COLUMNS)
    st.session_state.file_log = []
    st.session_state.processed_signatures = set()
    st.session_state.row_ids = {}
    st.session_state.row_batch = {}
    st.session_state.row_saved_values = {}


def _autosave_new_rows(new_df: pd.DataFrame) -> tuple[int, str | None]:
    """Insert freshly-extracted rows into Supabase under one batch_id.
    Returns (n_saved, error_message)."""
    if new_df.empty:
        return 0, None
    if not db.is_configured():
        return 0, None
    batch_id = str(uuid.uuid4())
    rows = [new_df.loc[i].to_dict() for i in new_df.index]
    try:
        inserted = db.insert_rows(rows, batch_id)
    except db.SupabaseError as exc:
        return 0, str(exc)

    for idx, db_row in zip(new_df.index, inserted):
        st.session_state.row_ids[idx] = db_row.get("id")
        st.session_state.row_batch[idx] = batch_id
        st.session_state.row_saved_values[idx] = new_df.loc[idx].to_dict()
    return len(inserted), None


def _sync_edits_to_history(edited_df: pd.DataFrame) -> tuple[int, int, list[str]]:
    """Push the current state of the editable table back to Supabase.
    Existing saved rows that changed are updated (edited=true). Rows that
    were never saved (e.g. added by hand in the editor, or extracted while
    Supabase wasn't configured) are inserted fresh. Returns
    (n_updated, n_inserted, errors)."""
    n_updated = 0
    n_inserted = 0
    errors: list[str] = []
    fresh_batch_id = str(uuid.uuid4())

    for idx in edited_df.index:
        row = edited_df.loc[idx].to_dict()
        row_id = st.session_state.row_ids.get(idx)
        if row_id:
            if st.session_state.row_saved_values.get(idx) != row:
                batch_id = st.session_state.row_batch.get(idx, fresh_batch_id)
                try:
                    db.update_row(row_id, row, batch_id)
                    st.session_state.row_saved_values[idx] = row
                    n_updated += 1
                except db.SupabaseError as exc:
                    errors.append(str(exc))
        else:
            try:
                inserted = db.insert_rows([row], fresh_batch_id)
                if inserted:
                    st.session_state.row_ids[idx] = inserted[0].get("id")
                    st.session_state.row_batch[idx] = fresh_batch_id
                    st.session_state.row_saved_values[idx] = row
                    n_inserted += 1
            except db.SupabaseError as exc:
                errors.append(str(exc))

    return n_updated, n_inserted, errors


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;font-weight:700;'
        f'font-size:16px;margin-bottom:14px;">{theme.icon("container", 22, theme.TEAL)}'
        f'<span>Container Extractor</span></div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="cde-nav-label">Navigate</div>', unsafe_allow_html=True)
    nav_choice = st.radio(
        "Navigate",
        ["Extract", "History"],
        index=0 if st.session_state.nav == "Extract" else 1,
        label_visibility="collapsed",
        key="nav_radio",
    )
    st.session_state.nav = nav_choice

    history_ok = db.is_configured()
    status_color = theme.TEAL if history_ok else theme.AMBER
    status_text = "History storage connected" if history_ok else "History storage not configured"
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:6px;font-size:12.5px;'
        f'color:{status_color};margin:2px 0 16px 2px;">{theme.icon("check" if history_ok else "settings", 13, status_color)}'
        f'<span>{status_text}</span></div>',
        unsafe_allow_html=True,
    )

    st.divider()

    with st.container(border=True):
        st.markdown(theme.section_heading("settings", "AI Provider"), unsafe_allow_html=True)

        provider_name = st.radio("AI provider", list(PROVIDERS.keys()), index=0, label_visibility="collapsed")
        provider = PROVIDERS[provider_name]

        api_key = _resolve_api_key(provider["secret_key"])

        if api_key:
            st.success("API key set (from secrets)", icon="✅")
        else:
            st.warning(f"No {provider['key_label']} found in Streamlit secrets.", icon="⚠️")

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
        "Extracted rows are saved to your Supabase history store if configured."
    )

# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

st.markdown(
    theme.hero(
        "Container Data Extractor",
        "Upload commercial invoices, packing lists, or customs declarations — any mix of "
        "PDF, DOCX, XLSX/XLSM, CSV, JPG, or PNG — and get a merged, editable table of "
        "container number, seal number, bale count, and net/gross/tare weight.",
    ),
    unsafe_allow_html=True,
)

# ==========================================================================
# EXTRACT PAGE
# ==========================================================================

if st.session_state.nav == "Extract":

    st.markdown(theme.section_heading("upload", "Upload documents"), unsafe_allow_html=True)

    uploaded_files = st.file_uploader(
        "Upload shipping documents",
        type=sorted(SUPPORTED_EXTENSIONS),
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        process_clicked = st.button("Extract data", type="primary", disabled=not uploaded_files, use_container_width=True)
    with col_b:
        clear_clicked = st.button("Clear all results", type="secondary", use_container_width=True)

    if clear_clicked:
        _reset_results()
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

                if all_new_rows:
                    start_idx = (st.session_state.results.index.max() + 1) if len(st.session_state.results) else 0
                    new_df = pd.DataFrame(all_new_rows, columns=DISPLAY_COLUMNS)
                    new_df.index = range(start_idx, start_idx + len(new_df))
                    st.session_state.results = pd.concat([st.session_state.results, new_df])

                    n_saved, save_err = _autosave_new_rows(new_df)
                    if save_err:
                        status_box.write(f"⚠️ Could not auto-save to history: {save_err}")
                    elif n_saved:
                        status_box.write(f"💾 Auto-saved {n_saved} record(s) to history.")
                    elif not db.is_configured():
                        status_box.write("ℹ️ History storage not configured — results will only persist for this session. See README.")

                status_box.update(label="Processing complete", state="complete", expanded=False)

    # ----------------------------------------------------------------
    # Per-file log
    # ----------------------------------------------------------------

    if st.session_state.file_log:
        with st.expander(f"File processing log ({len(st.session_state.file_log)} file(s))", expanded=False):
            for entry in st.session_state.file_log:
                icon = "✅" if entry["status"] == "ok" else "❌"
                st.write(f"{icon} **{entry['filename']}** — {entry['detail']}")

    # ----------------------------------------------------------------
    # Stat cards
    # ----------------------------------------------------------------

    results_df = st.session_state.results
    n_containers = len(results_df)
    n_bales = pd.to_numeric(results_df["bales"], errors="coerce").sum() if n_containers else 0
    n_files = len({e["filename"] for e in st.session_state.file_log if e["status"] == "ok"})

    st.markdown(
        theme.stat_cards_row([
            ("container", "Containers found", str(n_containers), theme.TEAL),
            ("pallet", "Total bales", str(int(n_bales) if pd.notna(n_bales) else 0), theme.AMBER),
            ("clipboard", "Files processed", str(n_files), theme.CYAN),
        ]),
        unsafe_allow_html=True,
    )

    # ----------------------------------------------------------------
    # Editable results table
    # ----------------------------------------------------------------

    st.markdown(
        theme.section_heading("boxes", "Results", "Fix any misread field directly in the table before exporting."),
        unsafe_allow_html=True,
    )

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

    n_saved_rows = sum(1 for i in edited_df.index if st.session_state.row_ids.get(i))
    n_unsaved_rows = len(edited_df) - n_saved_rows

    save_col, status_col = st.columns([1, 2])
    with save_col:
        save_clicked = st.button(
            "💾 Save edits to history",
            type="primary",
            use_container_width=True,
            disabled=edited_df.empty or not db.is_configured(),
        )
    with status_col:
        if not db.is_configured():
            st.caption("Configure SUPABASE_URL / SUPABASE_KEY in secrets to enable history.")
        elif n_containers:
            st.caption(f"{n_saved_rows} saved to history · {n_unsaved_rows} not yet saved.")

    if save_clicked:
        n_upd, n_ins, errs = _sync_edits_to_history(edited_df)
        if errs:
            st.error(f"Some rows failed to save: {errs[0]}")
        msg_parts = []
        if n_upd:
            msg_parts.append(f"{n_upd} row(s) updated")
        if n_ins:
            msg_parts.append(f"{n_ins} row(s) newly saved")
        if msg_parts:
            st.success("Saved to history — " + ", ".join(msg_parts) + ".")
        elif not errs:
            st.info("Nothing to save — table already matches history.")

    # ----------------------------------------------------------------
    # Export
    # ----------------------------------------------------------------

    st.markdown(theme.section_heading("download", "Export"), unsafe_allow_html=True)
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

# ==========================================================================
# HISTORY PAGE
# ==========================================================================

else:

    st.markdown(
        theme.section_heading("history", "Extraction history", "Everything ever extracted, across every session."),
        unsafe_allow_html=True,
    )

    if not db.is_configured():
        st.warning(
            "History storage isn't configured yet. Add `SUPABASE_URL` and `SUPABASE_KEY` to "
            "`.streamlit/secrets.toml` (locally) or your Streamlit Cloud app's Secrets, then "
            "reload. See the README's **Persistent history (Supabase)** section for the exact "
            "setup steps and SQL.",
            icon="⚠️",
        )
    else:
        col_toggle, col_range = st.columns([1, 3])
        with col_toggle:
            all_time = st.checkbox("All time", value=False)
        with col_range:
            if all_time:
                date_from, date_to = None, None
                st.caption("Showing all dates.")
            else:
                default_range = (date.today() - timedelta(days=90), date.today())
                date_range = st.date_input(
                    "Date range", value=default_range, key="history_date_range", label_visibility="collapsed"
                )
                if isinstance(date_range, tuple) and len(date_range) == 2:
                    date_from, date_to = date_range
                else:
                    date_from, date_to = default_range

        c1, c2, c3 = st.columns(3)
        with c1:
            container_query = st.text_input(
                "Container number contains", value="", placeholder="e.g. CSNU"
            )
        with c2:
            source_file_query = st.text_input(
                "Source file contains", value="", placeholder="e.g. invoice_042.pdf"
            )
        with c3:
            search_text = st.text_input(
                "Search all fields", value="", placeholder="seal #, notes, unit…"
            )

        try:
            history_df = db.fetch_history(
                date_from=date_from,
                date_to=date_to,
                container_query=container_query.strip(),
                source_file_query=source_file_query.strip(),
                search_text=search_text.strip(),
            )
        except db.SupabaseError as exc:
            st.error(f"Could not load history: {exc}")
            history_df = pd.DataFrame(columns=db.HISTORY_COLUMNS)

        st.markdown(
            theme.stat_cards_row([
                ("container", "Matching records", str(len(history_df)), theme.TEAL),
                (
                    "pallet",
                    "Total bales (filtered)",
                    str(int(pd.to_numeric(history_df["bales"], errors="coerce").sum())) if len(history_df) else "0",
                    theme.AMBER,
                ),
                (
                    "clipboard",
                    "Edited records",
                    str(int(history_df["edited"].fillna(False).sum())) if len(history_df) else "0",
                    theme.CYAN,
                ),
            ]),
            unsafe_allow_html=True,
        )

        # -- render as a fully-styled HTML table (sticky header, zebra
        # striping, monospace container/seal columns) --------------------
        if history_df.empty:
            st.markdown('<div class="cde-empty">No records match these filters.</div>', unsafe_allow_html=True)
        else:
            def _fmt(v, kind="text"):
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return ""
                if kind == "num":
                    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
                return str(v)

            rows_html = []
            for _, r in history_df.iterrows():
                extracted_at = str(r["extracted_at"])[:19].replace("T", " ")
                edited_badge = '<span class="cde-edited-badge">edited</span>' if r["edited"] else ""
                rows_html.append(
                    "<tr>"
                    f"<td>{_fmt(extracted_at)}</td>"
                    f"<td>{_fmt(r['source_file'])}</td>"
                    f"<td class='cde-mono'>{_fmt(r['container_number'])}</td>"
                    f"<td class='cde-mono'>{_fmt(r['seal_number'])}</td>"
                    f"<td>{_fmt(r['bales'])}</td>"
                    f"<td>{_fmt(r['net_weight'], 'num')}</td>"
                    f"<td>{_fmt(r['gross_weight'], 'num')}</td>"
                    f"<td>{_fmt(r['tare_weight'], 'num')}</td>"
                    f"<td>{_fmt(r['unit'])}</td>"
                    f"<td>{_fmt(r['notes'])}{edited_badge}</td>"
                    "</tr>"
                )

            table_html = f"""
            <div class="cde-table-wrap">
            <table class="cde-table">
                <thead><tr>
                    <th>Extracted at</th><th>Source file</th>
                    <th class="cde-mono">Container #</th><th class="cde-mono">Seal #</th>
                    <th>Bales</th><th>Net</th><th>Gross</th><th>Tare</th><th>Unit</th><th>Notes</th>
                </tr></thead>
                <tbody>{''.join(rows_html)}</tbody>
            </table>
            </div>
            """
            st.markdown(table_html, unsafe_allow_html=True)

        st.caption(f"{len(history_df)} record(s) match the current filters.")

        # -- export filtered view -----------------------------------------
        st.markdown(theme.section_heading("download", "Export filtered history"), unsafe_allow_html=True)
        if history_df.empty:
            st.caption("Nothing to export for these filters.")
        else:
            export_df = history_df[[c for c in HISTORY_EXPORT_COLUMNS if c in history_df.columns]]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            hc1, hc2 = st.columns(2)
            with hc1:
                st.download_button(
                    "Download CSV",
                    data=export_df.to_csv(index=False).encode("utf-8"),
                    file_name=f"container_history_{timestamp}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with hc2:
                st.download_button(
                    "Download Excel (.xlsx)",
                    data=_to_excel_bytes(export_df, sheet_name="History"),
                    file_name=f"container_history_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )

        # -- delete a bad record -------------------------------------------
        with st.expander("🗑️ Delete a record", expanded=False):
            if history_df.empty:
                st.caption("No records to delete.")
            else:
                labels = {
                    f"{str(r['extracted_at'])[:19].replace('T', ' ')} — {r['source_file']} — {r['container_number'] or '(no container #)'}": r["id"]
                    for _, r in history_df.iterrows()
                }
                selected_label = st.selectbox("Select a record", list(labels.keys()))
                confirm = st.checkbox("I'm sure — this can't be undone.")
                if st.button("Delete selected record", type="secondary", disabled=not confirm):
                    try:
                        db.delete_row(labels[selected_label])
                        st.success("Record deleted.")
                        st.rerun()
                    except db.SupabaseError as exc:
                        st.error(f"Could not delete record: {exc}")

