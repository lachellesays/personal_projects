"""
Usage:
    python3 generate_results.py

Edit the CONFIG section below, then run from the terminal.
"""

# ── CONFIG ───────────────────────────────────────────────────────────────────
SPREADSHEET_PATH  = "ResultsSaturday.xlsx"
OUTPUT_PDF        = "results.pdf"
HIGHLIGHT_HANDLER = None          # e.g. "Lachelle Henderson", or None
TRIAL_TITLE       = "UKI Agility Trial — Results"
SHOW_DATE         = "6/6/2026"    # only rows matching this date are included
# ─────────────────────────────────────────────────────────────────────────────

import sys, re
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    sys.exit("Missing: pip install pandas")
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle,
        Paragraph, Spacer, HRFlowable,
    )
except ImportError:
    sys.exit("Missing: pip install reportlab")


# ── Constants ─────────────────────────────────────────────────────────────────
GAMES_CLASSES = frozenset({"Gamblers", "Snooker"})

CLASS_ORDER = [
    "Agility", "Agility 2", "Jumping", "Jumping 2",
    "Gamblers", "Snooker", "Speedstakes", "Speedstakes 2",
    "Masters Agility", "Masters Jumping",
]

LEVEL_ORDER = ["Beginner", "Novice", "Senior", "Champ"]

NQ_VALUES = frozenset({"E", "ABS", "NFC"})


# ── Colors (matches generate_run_order.py) ────────────────────────────────────
C_DARK_BLUE = colors.HexColor("#1E3A8A")
C_MED_BLUE  = colors.HexColor("#3B82F6")
C_HIGHLIGHT = colors.HexColor("#FEF9C3")
C_ALT_ROW   = colors.HexColor("#EFF6FF")
C_SUBHEADER = colors.HexColor("#BFDBFE")
C_GRID      = colors.HexColor("#CBD5E1")
C_NQ_BG     = colors.HexColor("#F3F4F6")
C_NQ_TEXT   = colors.HexColor("#9CA3AF")


# ── Helpers ───────────────────────────────────────────────────────────────────
def height_sort_key(h: str) -> int:
    m = re.search(r"\d+", str(h))
    return int(m.group()) if m else 999


def is_nq(val) -> bool:
    return str(val).strip().upper() in NQ_VALUES


def fmt_faults(val) -> str:
    if is_nq(val):
        return str(val).strip()
    try:
        n = float(val)
        return str(int(n)) if n == int(n) else f"{n:.2f}"
    except (ValueError, TypeError):
        return str(val)


def fmt_time(val) -> str:
    try:
        t = float(val)
        return f"{t:.2f}" if t > 0 else "—"
    except (ValueError, TypeError):
        return "—"


def fmt_place(val) -> str:
    try:
        p = int(val)
        return str(p) if p > 0 else "—"
    except (ValueError, TypeError):
        return "—"


def fmt_int(val) -> str:
    try:
        n = int(float(val))
        return str(n) if n > 0 else "—"
    except (ValueError, TypeError):
        return "—"


# ── Data loading ──────────────────────────────────────────────────────────────
def load_data(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name="Raw Results", engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    df["Class"]       = df["Class"].astype(str).str.strip()
    df["Level"]       = df["Level"].astype(str).str.strip()
    df["Height"]      = df["Height"].astype(str).str.strip()
    df["Member Name"] = df["Member Name"].astype(str).str.strip()
    df["Dog Name"]    = df["Dog Name"].astype(str).str.strip()

    # Normalize dates: parse to datetime then compare, handles both string and
    # Excel date serial formats
    target = pd.to_datetime(SHOW_DATE)
    df = df[pd.to_datetime(df["Date"], errors="coerce") == target].copy()

    # Drop not-yet-run entries (SCT = 0)
    df = df[pd.to_numeric(df["SCT"], errors="coerce").fillna(0) > 0].copy()
    df.reset_index(drop=True, inplace=True)
    print(f"Loaded {len(df)} rows for {SHOW_DATE}")
    return df


# ── Sorting within a level+height group ──────────────────────────────────────
def sort_group(group_df: pd.DataFrame, is_games: bool) -> pd.DataFrame:
    nq_mask    = group_df["Faults"].apply(is_nq)
    qualifying = group_df[~nq_mask].copy()
    non_qual   = group_df[nq_mask].sort_values("Member Name")

    placed   = qualifying[qualifying["Place"] > 0].sort_values("Place")
    unplaced = qualifying[qualifying["Place"] == 0].copy()

    if is_games:
        unplaced = unplaced.sort_values(
            ["Games Points", "Time"], ascending=[False, True]
        )
    else:
        unplaced["_f"] = pd.to_numeric(unplaced["Faults"], errors="coerce").fillna(0)
        unplaced = unplaced.sort_values(["_f", "Time"])

    return pd.concat([placed, unplaced, non_qual], ignore_index=True)


# ── Table builder ─────────────────────────────────────────────────────────────
_COL_WIDTHS = [
    0.45 * inch,   # Pl.
    2.8  * inch,   # Handler
    1.8  * inch,   # Dog
    0.55 * inch,   # SCT
    0.7  * inch,   # Faults / Pts
    0.8  * inch,   # Time
    0.65 * inch,   # Lvl Pts
]


def _make_table(table_data, col_widths, *, highlight_rows=(), subheader_rows=(), nq_rows=()):
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_DARK_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 10),
        ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 1), (-1, -1), 9),
        ("GRID",          (0, 0), (-1, -1), 0.4, C_GRID),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (0, 1), (0, -1), "CENTER"),   # Pl.
        ("ALIGN",         (3, 1), (6, -1), "CENTER"),   # SCT / score / time / lvl pts
    ]

    for i in range(1, len(table_data)):
        if i not in subheader_rows and i not in nq_rows and i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_ALT_ROW))

    for i in subheader_rows:
        cmds += [
            ("SPAN",          (0, i), (-1, i)),
            ("BACKGROUND",    (0, i), (-1, i), C_SUBHEADER),
            ("TEXTCOLOR",     (0, i), (-1, i), C_DARK_BLUE),
            ("FONTNAME",      (0, i), (-1, i), "Helvetica-BoldOblique"),
            ("FONTSIZE",      (0, i), (-1, i), 9),
            ("ALIGN",         (0, i), (-1, i), "LEFT"),
            ("TOPPADDING",    (0, i), (-1, i), 3),
            ("BOTTOMPADDING", (0, i), (-1, i), 3),
        ]

    for i in nq_rows:
        if i not in subheader_rows:
            cmds += [
                ("BACKGROUND", (0, i), (-1, i), C_NQ_BG),
                ("TEXTCOLOR",  (0, i), (-1, i), C_NQ_TEXT),
            ]

    for i in highlight_rows:
        if i not in subheader_rows:
            cmds += [
                ("BACKGROUND", (0, i), (-1, i), C_HIGHLIGHT),
                ("FONTNAME",   (0, i), (-1, i), "Helvetica-Bold"),
                ("TEXTCOLOR",  (0, i), (-1, i), C_DARK_BLUE),
            ]

    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(cmds))
    return tbl


# ── Single class section ──────────────────────────────────────────────────────
def _class_flowables(class_name: str, class_df: pd.DataFrame) -> list:
    header_style = ParagraphStyle(
        "ClassH", fontSize=12, fontName="Helvetica-Bold",
        spaceBefore=16, spaceAfter=4, textColor=C_DARK_BLUE,
    )

    is_games  = class_name in GAMES_CLASSES
    score_lbl = "Pts" if is_games else "Faults"
    headers   = ["Pl.", "Handler", "Dog", "SCT", score_lbl, "Time", "Lvl Pts"]

    table_data:     list      = [headers]
    subheader_rows: set[int]  = set()
    highlight_rows: set[int]  = set()
    nq_rows:        set[int]  = set()

    for level in LEVEL_ORDER:
        level_df = class_df[class_df["Level"] == level]
        if level_df.empty:
            continue

        heights = sorted(level_df["Height"].unique(), key=height_sort_key)
        for height in heights:
            height_df = level_df[level_df["Height"] == height]
            sorted_df = sort_group(height_df, is_games)

            subheader_rows.add(len(table_data))
            table_data.append([f"  {level} — {height}", "", "", "", "", "", ""])

            for _, row in sorted_df.iterrows():
                faults_val = row["Faults"]
                nq         = is_nq(faults_val)
                handler    = str(row["Member Name"]).strip()

                score = (
                    fmt_int(row["Games Points"]) if (is_games and not nq) else
                    "—"                          if (is_games and nq)      else
                    fmt_faults(faults_val)
                )

                row_idx = len(table_data)
                table_data.append([
                    fmt_place(row["Place"]),
                    handler,
                    str(row["Dog Name"]).strip(),
                    str(int(float(row["SCT"]))),
                    score,
                    fmt_time(row["Time"]),
                    fmt_int(row["Level Points"]),
                ])

                if nq:
                    nq_rows.add(row_idx)
                if HIGHLIGHT_HANDLER and handler.lower() == HIGHLIGHT_HANDLER.strip().lower():
                    highlight_rows.add(row_idx)

    n_runs = len(table_data) - 1 - len(subheader_rows)
    label  = f"{class_name.upper()}  ({n_runs} run{'s' if n_runs != 1 else ''})"
    return [
        Paragraph(label, header_style),
        _make_table(table_data, _COL_WIDTHS,
                    highlight_rows=highlight_rows,
                    subheader_rows=subheader_rows,
                    nq_rows=nq_rows),
    ]


# ── Main PDF generator ────────────────────────────────────────────────────────
def generate_pdf(df: pd.DataFrame, output_path: Path) -> None:
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(letter),
        rightMargin=0.5 * inch, leftMargin=0.5 * inch,
        topMargin=0.55 * inch,  bottomMargin=0.55 * inch,
    )
    title_style = ParagraphStyle(
        "Title", fontSize=18, fontName="Helvetica-Bold",
        alignment=TA_CENTER, spaceAfter=4, textColor=C_DARK_BLUE,
    )
    date_style = ParagraphStyle(
        "Date", fontSize=11, fontName="Helvetica",
        alignment=TA_CENTER, spaceAfter=8, textColor=C_MED_BLUE,
    )

    story = [
        Paragraph(TRIAL_TITLE, title_style),
        Paragraph(SHOW_DATE, date_style),
        HRFlowable(width="100%", thickness=1.5, color=C_MED_BLUE, spaceAfter=10),
    ]

    present = set(df["Class"].unique())
    ordered = [c for c in CLASS_ORDER if c in present]
    ordered += sorted(c for c in present if c not in CLASS_ORDER)

    for class_name in ordered:
        class_df = df[df["Class"] == class_name]
        print(f"  {class_name}: {len(class_df)} rows")
        story.extend(_class_flowables(class_name, class_df))
        story.append(Spacer(1, 0.12 * inch))

    doc.build(story)
    print(f"\nPDF saved → {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    script_dir = Path(__file__).parent
    df = load_data(script_dir / SPREADSHEET_PATH)
    generate_pdf(df, script_dir / OUTPUT_PDF)
