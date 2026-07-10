# Container Data Extractor

A Streamlit app that turns messy shipping paperwork — commercial invoices,
packing lists, customs declarations — into a clean, mergeable table of
**container number, seal number, bale count, and net/gross/tare weight**.

Supported input formats: **PDF** (text-based or scanned), **DOCX**
(including tables pasted in as embedded Excel objects), **XLSX/XLSM**,
**CSV**, **JPG**, **PNG**. Upload several files at once and results merge
into one editable table.

## Why this uses AI instead of regex

Real supplier documents vary too much for hand-rolled per-format rules:
some are scanned images with no text layer, some are Word docs with a
spreadsheet pasted in as an embedded Excel object rather than a native Word
table, some are clean spreadsheet exports, and some commercial invoices only
give a single MT weight figure per container instead of a net/gross/tare
breakdown. Instead of chasing every layout with regex, every file is
converted to text and/or images and handed to an AI model with a strict
extraction prompt.

You can pick either **Claude (Anthropic)** or **Gemini (Google)** as the
extraction engine from the sidebar — same file-reading pipeline, same output
schema, different model behind it. Handy for comparing accuracy/cost, or as
a fallback if one provider is rate-limited or down.

Every extraction batch is auto-saved to a Supabase (Postgres) table, and the
**History** page lets you filter, search, export, and delete anything ever
extracted — across sessions, browser refreshes, and redeploys. See
[Persistent history (Supabase)](#persistent-history-supabase) below.

## How it works

1. **`extractors.py`** reads whatever format you upload and produces raw
   text blocks and/or images — it does no parsing of container data itself:
   - **PDF** — [pdfplumber](https://github.com/jsvine/pdfplumber) pulls text
     per page. Any page with no real text layer (a scan) is rasterized to a
     PNG with [PyMuPDF](https://pymupdf.readthedocs.io/) and queued as a
     vision image instead.
   - **DOCX** — [python-docx](https://python-docx.readthedocs.io/) pulls
     paragraph text and native Word tables. The `.docx` is also unzipped
     directly to check `word/embeddings/` for embedded `.xlsx`/`.xlsm`
     objects (parsed with [openpyxl](https://openpyxl.readthedocs.io/)),
     and `word/media/` for embedded images bigger than 150px in either
     dimension (smaller ones are treated as logos/letterhead and skipped).
   - **XLSX/XLSM** — every sheet is dumped as text via openpyxl with no
     assumption about header row or column order.
   - **CSV** — decoded and passed through as raw text.
   - **JPG/PNG** — sent straight through as vision input.
2. **`extraction_common.py`** holds the extraction instructions, output
   schema, and shared helpers (JSON normalization, dataclasses) used by both
   AI backends, so results are identical in shape no matter which provider
   produced them.
3. **`ai_extract.py`** sends each file's text/images to the **Anthropic
   API** (Claude) with those instructions wrapped as a system prompt asking
   for one JSON array of objects — one per container — with keys
   `container_number`, `seal_number`, `bales`, `net_weight`, `gross_weight`,
   `tare_weight`, `weight_unit`, `notes` — `null` for anything not present
   in the source document, never invented.
4. **`gemini_extract.py`** does the same against the **Gemini API**, using
   Gemini's native structured-output mode (a real JSON Schema passed via
   `response_mime_type`/`response_json_schema`) instead of prompt-based JSON
   framing.
5. **`db.py`** is the Supabase persistence layer: inserts newly-extracted
   rows, updates them when you correct something in the editable table,
   fetches/filters history, and deletes bad records. Every function raises
   a clear, catchable error if Supabase isn't configured or a call fails,
   so the rest of the app degrades gracefully (extraction/export still work
   without a database at all — you just won't get a History tab).
6. **`theme.py`** holds the maritime/logistics visual theme: injected CSS
   (navy/teal/amber palette, Inter font, stat cards, hero header, the
   History table's sticky header/zebra striping/monospace columns) and
   inline SVG icons used in place of emoji for section headers.
7. **`app.py`** is the Streamlit UI: pick a provider, upload, process,
   review/edit results in a table, export to CSV or Excel, and switch to
   the History tab to browse everything ever extracted.

## Local setup

```bash
git clone <your-repo-url>
cd container-data-extractor
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Provide an API key for whichever provider(s) you want to use — you only need
one to get started, but both can be configured side by side:

- Pasting it into the sidebar password field at runtime (session-only, never
  written to disk), **or**
- Copying `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and
  filling in your key(s):

  ```bash
  cp .streamlit/secrets.toml.example .streamlit/secrets.toml
  ```

Then run:

```bash
streamlit run app.py
```

- Get a Claude/Anthropic API key at
  [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
  (requires billing to be set up — separate from any Claude.ai subscription).
- Get a Gemini API key at
  [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

## Deploying to Streamlit Community Cloud

1. Push this repo to GitHub (public or private).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in, and
   click **New app**.
3. Pick your repo, branch, and set the main file path to `app.py`.
4. Before (or after) deploying, open **Settings -> Secrets** for the app and
   paste in whichever key(s) you want available without users having to
   enter one manually:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-api03-your-real-key"
   GEMINI_API_KEY = "your-real-gemini-key"
   SUPABASE_URL = "https://your-project-ref.supabase.co"
   SUPABASE_KEY = "your-supabase-key"
   ```

   These make `st.secrets["ANTHROPIC_API_KEY"]` / `st.secrets["GEMINI_API_KEY"]`
   available as the fallback whenever the sidebar field for that provider is
   left blank — handy if you want to use the deployed app without
   re-entering a key every session, or want a key pre-set for other users on
   your team without exposing it to them in plaintext. You only need to set
   the secret for the provider(s) you actually plan to use. `SUPABASE_URL` /
   `SUPABASE_KEY` enable the History tab — see the next section for how to
   get them. The app still works for one-off extraction/export without
   them; you just won't get persistent history.
5. Click **Deploy**. Streamlit Community Cloud installs everything from
   `requirements.txt` automatically.

**Important:** Streamlit Community Cloud's filesystem is ephemeral — a local
SQLite file or any other on-disk write does **not** survive an app restart
or redeploy. That's why history is stored in Supabase (a hosted Postgres
database) rather than locally; see below.

## Persistent history (Supabase)

Results are stored in a single Postgres table, `extracted_documents`, in a
Supabase project you control. Supabase's free tier is plenty for this.

### 1. Create the Supabase project

1. Go to [supabase.com](https://supabase.com), sign in, and create a new
   project (pick any name/region; note the database password somewhere
   safe, though this app doesn't need it directly).
2. Once the project is provisioned, open **Project Settings -> API**. You'll
   need two values for Streamlit secrets:
   - **Project URL** -> `SUPABASE_URL`
   - An API key -> `SUPABASE_KEY`. Either the `anon` `public` key (paired
     with the row-level-security policy below, which is included in the
     SQL and required for anon requests to work) or the `service_role`
     key (bypasses row-level security entirely). Since this app runs
     server-side in Streamlit and secrets are never sent to the browser,
     using `service_role` is simpler and fine for a single-team internal
     tool; use `anon` + the policy below if you'd rather keep the key
     lower-privilege.

### 2. Create the table

I chose **"create it yourself via the SQL Editor"** over shipping a Python
migration script: this is one table, created once, with no schema that's
expected to evolve on a regular cadence — a migration runner (Alembic-style
versioned migrations, etc.) would be more machinery than the problem
warrants. If the schema needs to change later, treat the ALTER statement as
a one-off, run the same way.

In your Supabase project, open **SQL Editor -> New query**, paste the
following, and run it once:

```sql
create extension if not exists pgcrypto;

create table if not exists extracted_documents (
    id             uuid primary key default gen_random_uuid(),
    batch_id       uuid not null,
    source_file    text not null,
    container_number text,
    seal_number    text,
    bales          integer,
    net_weight     numeric,
    gross_weight   numeric,
    tare_weight    numeric,
    unit           text,
    notes          text,
    extracted_at   timestamptz not null default now(),
    edited         boolean not null default false
);

create index if not exists idx_extracted_documents_extracted_at
    on extracted_documents (extracted_at desc);
create index if not exists idx_extracted_documents_batch_id
    on extracted_documents (batch_id);
create index if not exists idx_extracted_documents_container_number
    on extracted_documents (container_number);
create index if not exists idx_extracted_documents_source_file
    on extracted_documents (source_file);

-- Only needed if you're using the `anon` key rather than `service_role`.
-- Enables row-level security, then adds one permissive policy so the app
-- (which is the only client talking to this table) can read/write freely.
alter table extracted_documents enable row level security;

create policy "app full access"
    on extracted_documents
    for all
    using (true)
    with check (true);
```

### 3. Add the secrets

Locally: copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
and fill in `SUPABASE_URL` / `SUPABASE_KEY` (never commit the real file —
it's already gitignored). On Streamlit Community Cloud: add the same two
keys under **Settings -> Secrets** (see the deploy section above).

### 4. Save behavior

I went with **auto-save + an explicit re-save button**, not one or the
other:

- **Auto-save on extraction.** The moment a batch finishes processing, its
  rows are written to Supabase with `edited = false`. This is the
  "guarantee nothing is lost" path — it doesn't depend on the user
  remembering to click anything before closing the tab.
- **Explicit "Save edits to history" button** for corrections made
  afterward in the editable table. Writing to Supabase on every keystroke
  in `st.data_editor` would mean a network round-trip per cell edit, which
  is both slow and noisy (and would mark rows "edited" the instant you
  touched them, even before you'd finished typing). A button gives you a
  clear, deliberate point to commit corrections, and the app tracks which
  rows are saved vs. not (see the caption under the Results table) so it's
  obvious when you still need to click it.

Rows you edit get `edited = true` in the History table so you can always
tell AI output from a human correction.

### Verifying it survives a redeploy

Extract something, confirm it shows up on the History tab, then redeploy
(or just restart) the app on Streamlit Community Cloud and check History
again — the record should still be there, since it lives in Supabase, not
on the app's local filesystem.

## Using the app

1. Open the sidebar and pick an **AI provider** — Claude or Gemini. Confirm
   an API key is set for that provider, and pick a model:
   - Claude: default `claude-sonnet-4-6`, with `claude-opus-4-8` and
     `claude-haiku-4-5-20251001` also offered. Check
     [docs.claude.com](https://docs.claude.com/en/docs/about-claude/models)
     for current model IDs before deploying, since these change over time.
   - Gemini: default `gemini-3.5-flash`, with `gemini-2.5-pro` and
     `gemini-2.5-flash-lite` also offered. Check
     [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models)
     for current model IDs.
   - Both providers also have a free-text "custom model ID" override in case
     a newer model has shipped since this was built.
2. Upload one or more files.
3. Click **Extract data**. A progress indicator shows per-file status;
   errors on one file (bad key, unreadable file, no data found) are reported
   inline and don't stop the rest of the batch. Rows are auto-saved to
   history at this point (if Supabase is configured).
4. Review the **Results** table — it's fully editable
   (`st.data_editor`), so fix any misread field before exporting. Click
   **Save edits to history** to push your corrections back to Supabase
   (flagged `edited`).
5. Check the stat cards (containers found / total bales / files processed)
   as a sanity check against the source paperwork.
6. Download **CSV** or **Excel**.
7. **Clear all results** resets the Extract page for a new batch (it does
   not delete anything from history).
8. Switch to the **History** tab (sidebar nav) to browse everything ever
   extracted: filter by date range, container number, or source file;
   search across all fields; export the filtered view as CSV/XLSX; or
   delete a bad record.

## Notes on accuracy

- Both providers are instructed to never invent values — anything not
  present in the source document comes back as `null`, with an explanation
  in `notes` when something's ambiguous (e.g. "only a single MT figure
  given, not split into net/gross/tare").
- Summary/total rows are excluded automatically; only per-container records
  are returned.
- Scanned PDFs are rasterized at 200 DPI before being sent to the vision
  input — legibility depends on the quality of the original scan.
- Gemini enforces the output shape natively via a JSON Schema; Claude is
  instructed via prompt to return JSON-only and the response is parsed
  defensively (code fences stripped, etc.). Both are normalized to the same
  row shape before hitting the table, so results are directly comparable.
- Always spot-check the editable table against the source document,
  especially for scanned or handwritten paperwork, regardless of provider.

## Repo structure

```
.
├── app.py                        # Streamlit UI: Extract + History pages, provider selection
├── extractors.py                 # File-reading logic (PDF/DOCX/XLSX/CSV/images)
├── extraction_common.py          # Shared schema, prompt text, and helpers
├── ai_extract.py                 # Claude (Anthropic) extraction backend
├── gemini_extract.py             # Gemini (Google) extraction backend
├── db.py                         # Supabase persistence layer (insert/update/fetch/delete)
├── theme.py                      # CSS injection, SVG icons, hero/stat-card/table components
├── requirements.txt
├── .streamlit/
│   ├── config.toml               # Base theme (dark, navy/teal) — committed, no secrets in it
│   └── secrets.toml.example      # Template — copy to secrets.toml, don't commit the real one
├── .gitignore
└── README.md
```
