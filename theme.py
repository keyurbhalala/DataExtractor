"""
theme.py — maritime/logistics visual theme for Container Data Extractor.

Centralizes the CSS injection, inline SVG iconography, and small HTML
component builders (hero header, stat cards) used by app.py, so the Extract
and History views share one consistent look instead of drifting apart.

Design intent (see the upgrade brief):
- Deep navy background, teal/cyan primary accent, amber secondary accent.
- Inter for typography instead of the default Streamlit font stack.
- Lucide-style inline SVG icons instead of emoji for section headers.
- Bordered/elevated stat cards instead of plain st.metric stacks.
- A read-only, fully-styled HTML table for History (sticky header, zebra
  striping, monospace container/seal columns) — see the note in app.py
  about why the *editable* Extract results table can't get the same
  per-column font treatment (st.data_editor renders to a canvas grid, which
  CSS can't reach column-by-column; it still inherits the app's theme
  colors and font via .streamlit/config.toml).
"""

from __future__ import annotations

import html as _html

NAVY_BG = "#0B1220"
NAVY_SURFACE = "#111827"
NAVY_SURFACE_2 = "#16213A"
NAVY_BORDER = "#1F2A44"
TEAL = "#14B8A6"
TEAL_SOFT = "#0F766E"
CYAN = "#06B6D4"
AMBER = "#F59E0B"
TEXT_PRIMARY = "#E5E7EB"
TEXT_MUTED = "#94A3B8"

ICONS = {
    "container": (
        '<rect x="2" y="6" width="20" height="12" rx="1"></rect>'
        '<line x1="2" y1="10" x2="22" y2="10"></line>'
        '<line x1="2" y1="14" x2="22" y2="14"></line>'
        '<line x1="7" y1="6" x2="7" y2="18"></line>'
        '<line x1="12" y1="6" x2="12" y2="18"></line>'
        '<line x1="17" y1="6" x2="17" y2="18"></line>'
    ),
    "ship": (
        '<path d="M3 17l1.5-6h15L21 17"></path>'
        '<path d="M5 17l-2 3h18l-2-3"></path>'
        '<line x1="12" y1="2" x2="12" y2="11"></line>'
        '<path d="M12 2c2 0 4 1 5 3l-5 2-5-2c1-2 3-3 5-3z"></path>'
    ),
    "pallet": (
        '<rect x="3" y="4" width="18" height="6" rx="1"></rect>'
        '<line x1="3" y1="16" x2="5" y2="16"></line>'
        '<line x1="9" y1="16" x2="11" y2="16"></line>'
        '<line x1="15" y1="16" x2="17" y2="16"></line>'
        '<line x1="21" y1="16" x2="21" y2="16"></line>'
        '<line x1="5" y1="10" x2="5" y2="20"></line>'
        '<line x1="19" y1="10" x2="19" y2="20"></line>'
    ),
    "crane": (
        '<line x1="4" y1="21" x2="4" y2="6"></line>'
        '<line x1="4" y1="6" x2="19" y2="6"></line>'
        '<line x1="4" y1="10" x2="14" y2="6"></line>'
        '<line x1="15" y1="6" x2="15" y2="12"></line>'
        '<line x1="4" y1="21" x2="10" y2="21"></line>'
    ),
    "clipboard": (
        '<rect x="6" y="3" width="12" height="18" rx="2"></rect>'
        '<rect x="9" y="1.5" width="6" height="3" rx="1"></rect>'
        '<line x1="9" y1="10" x2="15" y2="10"></line>'
        '<line x1="9" y1="14" x2="15" y2="14"></line>'
        '<line x1="9" y1="18" x2="12" y2="18"></line>'
    ),
    "search": (
        '<circle cx="10.5" cy="10.5" r="6.5"></circle>'
        '<line x1="20" y1="20" x2="15.3" y2="15.3"></line>'
    ),
    "filter": (
        '<polygon points="4 4 20 4 14 12 14 18 10 20 10 12 4 4"></polygon>'
    ),
    "history": (
        '<path d="M3 12a9 9 0 1 0 3-6.7"></path>'
        '<polyline points="3 4 3 9 8 9"></polyline>'
        '<polyline points="12 7 12 12 16 14"></polyline>'
    ),
    "upload": (
        '<path d="M12 3v12"></path>'
        '<polyline points="7 8 12 3 17 8"></polyline>'
        '<path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"></path>'
    ),
    "trash": (
        '<polyline points="3 6 5 6 21 6"></polyline>'
        '<path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"></path>'
        '<path d="M19 6l-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6"></path>'
        '<line x1="10" y1="10" x2="10" y2="17"></line>'
        '<line x1="14" y1="10" x2="14" y2="17"></line>'
    ),
    "download": (
        '<path d="M12 3v12"></path>'
        '<polyline points="7 11 12 16 17 11"></polyline>'
        '<path d="M4 18v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"></path>'
    ),
    "settings": (
        '<circle cx="12" cy="12" r="3"></circle>'
        '<path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.6 1H21a2 2 0 1 1 0 4h-.2a1.7 1.7 0 0 0-1.5 1z"></path>'
    ),
    "boxes": (
        '<path d="M2.5 8.5 12 4l9.5 4.5L12 13z"></path>'
        '<path d="M2.5 8.5V16L12 20.5V13"></path>'
        '<path d="M21.5 8.5V16L12 20.5"></path>'
    ),
    "check": '<polyline points="20 6 9 17 4 12"></polyline>',
}


def icon(name: str, size: int = 18, color: str = TEXT_PRIMARY, stroke_width: float = 1.8) -> str:
    body = ICONS.get(name, ICONS["boxes"])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-4px">{body}</svg>'
    )


def section_heading(icon_name: str, text: str, subtitle: str = "") -> str:
    sub = f'<div class="cde-section-sub">{_html.escape(subtitle)}</div>' if subtitle else ""
    return (
        '<div class="cde-section-heading">'
        f'<div class="cde-section-title">{icon(icon_name, 20, TEAL)}'
        f'<span>{_html.escape(text)}</span></div>{sub}</div>'
    )


def stat_card(icon_name: str, label: str, value: str, accent: str = TEAL) -> str:
    # NOTE: must be a single line with no blank lines inside it. Streamlit's
    # markdown renderer (CommonMark) treats a run of `<div>`-based HTML as a
    # "type 6" HTML block, which terminates at the first blank line — unlike
    # `<style>`/`<script>`/`<pre>` blocks, which only end at their matching
    # closing tag. Since stat_cards_row() concatenates several of these,
    # any embedded blank line (or even just a whitespace-only line) between
    # fragments causes everything after it to fall back to being rendered
    # as literal text instead of HTML.
    return (
        f'<div class="cde-stat-card" style="--accent:{accent}">'
        f'<div class="cde-stat-icon">{icon(icon_name, 22, accent)}</div>'
        f'<div class="cde-stat-body">'
        f'<div class="cde-stat-value">{_html.escape(str(value))}</div>'
        f'<div class="cde-stat-label">{_html.escape(label)}</div>'
        f'</div></div>'
    )


def stat_cards_row(cards: list[tuple[str, str, str, str]]) -> str:
    """cards: list of (icon_name, label, value, accent)."""
    inner = "".join(stat_card(i, l, v, a) for i, l, v, a in cards)
    return f'<div class="cde-stat-row">{inner}</div>'


def hero(title: str, subtitle: str) -> str:
    # Single line for the same reason as stat_card() above.
    hero_svg = _HERO_SVG.replace("\n", "")
    return (
        f'<div class="cde-hero">'
        f'<div class="cde-hero-bg">{hero_svg}</div>'
        f'<div class="cde-hero-content">'
        f'<div class="cde-hero-title">{icon("ship", 30, TEAL, 2)}<span>{_html.escape(title)}</span></div>'
        f'<div class="cde-hero-subtitle">{_html.escape(subtitle)}</div>'
        f'</div></div>'
    )


_HERO_SVG = """
<svg viewBox="0 0 900 120" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
  <line x1="0" y1="100" x2="900" y2="100" stroke="#1F2A44" stroke-width="1"/>
  <g stroke="#14B8A6" stroke-width="1.6" fill="none" opacity="0.35">
    <rect x="40" y="60" width="46" height="30"/>
    <rect x="92" y="60" width="46" height="30"/>
    <rect x="40" y="30" width="46" height="28"/>
    <rect x="180" y="66" width="40" height="24"/>
    <rect x="222" y="66" width="40" height="24"/>
    <rect x="700" y="55" width="46" height="35"/>
    <rect x="752" y="55" width="46" height="35"/>
    <rect x="700" y="18" width="46" height="35"/>
    <rect x="820" y="62" width="40" height="28"/>
  </g>
  <path d="M300 95 L340 95 L332 82 L308 82 Z" fill="none" stroke="#F59E0B" stroke-width="1.4" opacity="0.4"/>
  <line x1="320" y1="82" x2="320" y2="40" stroke="#F59E0B" stroke-width="1.4" opacity="0.4"/>
  <path d="M0 108 Q 225 100 450 108 T 900 108" fill="none" stroke="#06B6D4" stroke-width="1.2" opacity="0.3"/>
</svg>
"""


def inject_css() -> str:
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}}

.stApp {{
    background: linear-gradient(180deg, {NAVY_BG} 0%, #0D1526 100%);
    color: {TEXT_PRIMARY};
}}

section[data-testid="stSidebar"] {{
    background: {NAVY_SURFACE};
    border-right: 1px solid {NAVY_BORDER};
}}

h1, h2, h3, h4 {{
    font-family: 'Inter', sans-serif;
    color: {TEXT_PRIMARY};
    letter-spacing: -0.01em;
}}

/* Primary buttons -> teal */
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {{
    background: {TEAL};
    border: 1px solid {TEAL};
    color: #04211E;
    font-weight: 600;
}}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button[kind="primary"]:hover {{
    background: {TEAL_SOFT};
    border-color: {TEAL_SOFT};
    color: #E5FBF8;
}}

/* Secondary buttons -> outlined, amber on hover for caution actions */
.stButton > button[kind="secondary"] {{
    background: transparent;
    border: 1px solid {NAVY_BORDER};
    color: {TEXT_PRIMARY};
}}
.stButton > button[kind="secondary"]:hover {{
    border-color: {AMBER};
    color: {AMBER};
}}

/* Hero */
.cde-hero {{
    position: relative;
    border: 1px solid {NAVY_BORDER};
    border-radius: 14px;
    background: linear-gradient(135deg, {NAVY_SURFACE} 0%, {NAVY_SURFACE_2} 100%);
    overflow: hidden;
    padding: 22px 26px 18px 26px;
    margin-bottom: 18px;
}}
.cde-hero-bg {{
    position: absolute;
    inset: 0;
    opacity: 0.55;
    pointer-events: none;
}}
.cde-hero-bg svg {{ width: 100%; height: 100%; }}
.cde-hero-content {{ position: relative; z-index: 1; }}
.cde-hero-title {{
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 26px;
    font-weight: 700;
    color: {TEXT_PRIMARY};
}}
.cde-hero-subtitle {{
    margin-top: 6px;
    color: {TEXT_MUTED};
    font-size: 14.5px;
    max-width: 720px;
}}

/* Section headings */
.cde-section-heading {{ margin: 6px 0 2px 0; }}
.cde-section-title {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 17px;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}
.cde-section-sub {{ color: {TEXT_MUTED}; font-size: 13px; margin: 2px 0 8px 26px; }}

/* Stat cards */
.cde-stat-row {{ display: flex; gap: 14px; margin: 6px 0 18px 0; flex-wrap: wrap; }}
.cde-stat-card {{
    flex: 1 1 180px;
    display: flex;
    align-items: center;
    gap: 12px;
    background: {NAVY_SURFACE};
    border: 1px solid {NAVY_BORDER};
    border-left: 3px solid var(--accent, {TEAL});
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
}}
.cde-stat-icon {{
    background: rgba(20, 184, 166, 0.08);
    border-radius: 8px;
    padding: 8px;
    display: flex;
}}
.cde-stat-value {{ font-size: 22px; font-weight: 700; color: {TEXT_PRIMARY}; line-height: 1.1; }}
.cde-stat-label {{ font-size: 12.5px; color: {TEXT_MUTED}; margin-top: 2px; }}

/* Data editor / dataframe wrapper */
[data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {{
    border: 1px solid {NAVY_BORDER} !important;
    border-radius: 10px !important;
    overflow: hidden;
}}

/* Custom HTML history table */
.cde-table-wrap {{
    max-height: 560px;
    overflow: auto;
    border: 1px solid {NAVY_BORDER};
    border-radius: 10px;
}}
table.cde-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13.5px;
}}
table.cde-table thead th {{
    position: sticky;
    top: 0;
    background: {NAVY_SURFACE_2};
    color: {TEXT_PRIMARY};
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid {NAVY_BORDER};
    font-weight: 600;
    white-space: nowrap;
    z-index: 2;
}}
table.cde-table tbody td {{
    padding: 8px 12px;
    border-bottom: 1px solid {NAVY_BORDER};
    color: {TEXT_PRIMARY};
    vertical-align: top;
}}
table.cde-table tbody tr:nth-child(even) {{ background: rgba(255,255,255,0.02); }}
table.cde-table tbody tr:hover {{ background: rgba(20,184,166,0.06); }}
table.cde-table td.cde-mono, table.cde-table th.cde-mono {{
    font-family: 'JetBrains Mono', ui-monospace, monospace;
    letter-spacing: 0.02em;
}}
.cde-edited-badge {{
    display: inline-block;
    font-size: 10.5px;
    font-weight: 600;
    color: {AMBER};
    border: 1px solid {AMBER};
    border-radius: 999px;
    padding: 1px 7px;
    margin-left: 6px;
}}
.cde-empty {{ color: {TEXT_MUTED}; padding: 24px 8px; text-align: center; }}

/* Sidebar nav pills */
.cde-nav-label {{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {TEXT_MUTED};
    margin: 4px 0 2px 2px;
}}
</style>
"""
