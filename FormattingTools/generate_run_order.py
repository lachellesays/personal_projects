"""
Usage:
    python3 generate_run_order.py

Edit the CONFIG section below, then run from the terminal.
"""

# ── CONFIG ───────────────────────────────────────────────────────────────────
SPREADSHEET_PATH  = "grid_print_run_order.xls"
OUTPUT_PDF        = "run_order.pdf"
HIGHLIGHT_HANDLER = None          # e.g. "Lachelle Henderson", or None
TRIAL_TITLE       = "UKI Agility Trial — Run Order"

SC_LEVELS      = frozenset({'champ', 'select', 'senior', 'regular', 'palladium'})
BN_LEVELS      = frozenset({'novice', 'beginner'})
MIN_BLOCK_SIZE = 5
# ─────────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path

# ── Imports ──────────────────────────────────────────────────────────────────
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
        Paragraph, Spacer, PageBreak, HRFlowable,
    )
except ImportError:
    sys.exit("Missing: pip install reportlab")


# ── Load spreadsheet ─────────────────────────────────────────────────────────
def load_spreadsheet(path):
    path = Path(path)
    if path.suffix.lower() == ".xls":
        from xlrd import compdoc
        _orig = compdoc.CompDoc.__init__
        def _lenient(self, *a, **kw):
            kw["ignore_workbook_corruption"] = True
            _orig(self, *a, **kw)
        compdoc.CompDoc.__init__ = _lenient
        try:
            df = pd.read_excel(path, engine="xlrd")
        finally:
            compdoc.CompDoc.__init__ = _orig
    else:
        df = pd.read_excel(path, engine="openpyxl")

    df.columns = [str(c).strip() for c in df.columns]
    for col in ["Date", "Event"]:
        if col in df.columns:
            df[col] = df[col].replace("", pd.NA).ffill()
    df.dropna(how="all", inplace=True)
    df.reset_index(drop=True, inplace=True)
    print(f"Loaded {len(df)} rows  |  columns: {df.columns.tolist()}")
    return df


# ── Colors ───────────────────────────────────────────────────────────────────
C_DARK_BLUE = colors.HexColor("#1E3A8A")
C_MED_BLUE  = colors.HexColor("#3B82F6")
C_HIGHLIGHT = colors.HexColor("#FEF9C3")
C_ALT_ROW   = colors.HexColor("#EFF6FF")
C_SUBHEADER = colors.HexColor("#BFDBFE")
C_GRID      = colors.HexColor("#CBD5E1")

GROUP_LABELS = {"SC": "Senior / Champ", "BN": "Beginner / Novice", "M": "Masters"}


# ── Block / heat helpers ──────────────────────────────────────────────────────
def _level_group(level_str):
    l = str(level_str).strip().lower()
    if l in SC_LEVELS:  return "SC"
    if l in BN_LEVELS:  return "BN"
    if "masters" in l:  return "M"
    return "OTHER"


def _get_blocks(class_df):
    groups = class_df["Level"].apply(_level_group).tolist()
    if not groups:
        return []
    blocks, start, cur = [], 0, groups[0]
    for i in range(1, len(groups)):
        if groups[i] != cur:
            blocks.append((cur, start, i))
            start, cur = i, groups[i]
    blocks.append((cur, start, len(groups)))
    return blocks


def _split_into_heats(blocks):
    """Split blocks into heats; new heat when a large group repeats."""
    if not any(b[2] - b[1] >= MIN_BLOCK_SIZE for b in blocks):
        return [blocks]
    heats, current, seen = [], [], set()
    for grp, s, e in blocks:
        is_large = (e - s) >= MIN_BLOCK_SIZE
        if is_large and grp in seen:
            heats.append(current)
            current, seen = [(grp, s, e)], {grp}
        else:
            current.append((grp, s, e))
            if is_large:
                seen.add(grp)
    if current:
        heats.append(current)
    return heats


# ── Table builder ─────────────────────────────────────────────────────────────
def _make_table(table_data, col_widths, highlight_rows=(), subheader_rows=(), extra_cmds=()):
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
    ]
    for i in range(1, len(table_data)):
        if i not in subheader_rows and i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), C_ALT_ROW))
    for i in subheader_rows:
        cmds += [
            ("SPAN",          (0, i), (-1, i)),
            ("BACKGROUND",    (0, i), (-1, i), C_SUBHEADER),
            ("TEXTCOLOR",     (0, i), (-1, i), C_DARK_BLUE),
            ("FONTNAME",      (0, i), (-1, i), "Helvetica-BoldOblique"),
            ("FONTSIZE",      (0, i), (-1, i), 9),
            ("ALIGN",         (0, i), (-1, i), "CENTER"),
            ("TOPPADDING",    (0, i), (-1, i), 3),
            ("BOTTOMPADDING", (0, i), (-1, i), 3),
        ]
    for i in highlight_rows:
        if i not in subheader_rows:
            cmds += [
                ("BACKGROUND", (0, i), (-1, i), C_HIGHLIGHT),
                ("FONTNAME",   (0, i), (-1, i), "Helvetica-Bold"),
                ("TEXTCOLOR",  (0, i), (-1, i), C_DARK_BLUE),
            ]
    cmds += list(extra_cmds)
    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(cmds))
    return tbl


# ── Single heat ───────────────────────────────────────────────────────────────
def _single_heat_flowables(class_df, heat_label, heat_blocks, highlight_handler, col_widths):
    header_style = ParagraphStyle(
        "ClassH", fontSize=11, fontName="Helvetica-Bold",
        spaceBefore=14, spaceAfter=3, textColor=C_DARK_BLUE
    )
    n = sum(b[2] - b[1] for b in heat_blocks)
    label = f"{heat_label.strip().upper()}  ({n} run{'s' if n != 1 else ''})"

    large_in_heat = [b for b in heat_blocks if b[2] - b[1] >= MIN_BLOCK_SIZE]
    show_sh = len(large_in_heat) >= 2

    table_data = [["#", "Handler", "Dog", "Breed", "Height"]]
    hi_rows, sh_rows = set(), set()
    run_num = 1

    for grp, blk_s, blk_e in heat_blocks:
        is_large = (blk_e - blk_s) >= MIN_BLOCK_SIZE
        if show_sh and is_large:
            sh_rows.add(len(table_data))
            table_data.append([f"— {GROUP_LABELS.get(grp, grp)} —", "", "", "", ""])

        for i in range(blk_s, blk_e):
            row     = class_df.iloc[i]
            handler = str(row.get("Handler Name", "")).strip()
            dog     = str(row.get("Dog",          "")).strip()
            breed   = str(row.get("Breed",        "")).strip()
            height  = str(row.get("Height",       "")).strip()
            if height == "nan":
                height = ""
            table_data.append([run_num, handler, dog, breed, height])
            if highlight_handler and handler.lower() == highlight_handler.strip().lower():
                hi_rows.add(len(table_data) - 1)
            run_num += 1

    return [
        Paragraph(label, header_style),
        _make_table(table_data, col_widths, highlight_rows=hi_rows, subheader_rows=sh_rows),
    ]


# ── Event flowables (handles multi-heat split) ────────────────────────────────
def _class_flowables(class_df, event, highlight_handler):
    col_widths = [0.45*inch, 2.4*inch, 1.55*inch, 2.85*inch, 0.65*inch]
    class_df   = class_df.reset_index(drop=True)
    blocks     = _get_blocks(class_df)
    heats      = _split_into_heats(blocks)

    print(f"  {event}: {len(blocks)} blocks → {len(heats)} heat(s)")

    flowables = []
    for heat_idx, heat_blocks in enumerate(heats):
        suffix     = f" {heat_idx + 1}" if heat_idx > 0 else ""
        heat_label = f"{event.strip()}{suffix}"
        flowables.extend(
            _single_heat_flowables(class_df, heat_label, heat_blocks, highlight_handler, col_widths)
        )
        if heat_idx < len(heats) - 1:
            flowables.append(Spacer(1, 0.1 * inch))
    return flowables


# ── Gap analysis ──────────────────────────────────────────────────────────────
def _gap_flowables(df, handler_name):
    title_style = ParagraphStyle(
        "GapT", fontSize=14, fontName="Helvetica-Bold",
        alignment=TA_CENTER, spaceAfter=6, textColor=C_DARK_BLUE
    )
    note_style = ParagraphStyle(
        "GapN", fontSize=9, fontName="Helvetica-Oblique",
        alignment=TA_CENTER, spaceAfter=14,
        textColor=colors.HexColor("#6B7280")
    )
    flowables = [
        PageBreak(),
        Paragraph(f"Run Gap Analysis — {handler_name}", title_style),
        Paragraph(
            '"Runs Between" = number of other handlers running between '
            "this handler's consecutive entries within the same class.",
            note_style
        ),
    ]
    df = df.copy()
    df["_class"] = (
        df["Date"].fillna("").astype(str).str.strip() + " — " +
        df["Event"].fillna("").astype(str).str.strip()
    )
    name_lower = handler_name.strip().lower()
    col_widths = [3.0*inch, 0.65*inch, 1.4*inch, 1.4*inch, 1.0*inch, 1.4*inch]
    table_data = [["Class", "Run #", "Dog", "Runs Between", "Next Run #", "Next Dog"]]
    for cls in df["_class"].unique():
        cls_df    = df[df["_class"] == cls].reset_index(drop=True)
        mask      = cls_df["Handler Name"].str.strip().str.lower() == name_lower
        h         = cls_df[mask]
        if h.empty:
            continue
        positions = h.index.tolist()
        for i, pos in enumerate(positions):
            dog = str(h.loc[pos, "Dog"]).strip()
            if i < len(positions) - 1:
                nxt      = positions[i + 1]
                gap      = nxt - pos - 1
                next_dog = str(h.loc[nxt, "Dog"]).strip()
                table_data.append([cls, pos+1, dog, gap, nxt+1, next_dog])
            else:
                table_data.append([cls, pos+1, dog, "(last entry)", "—", "—"])
    if len(table_data) == 1:
        flowables.append(Paragraph(f"No runs found for '{handler_name}'.", note_style))
        return flowables
    tbl = _make_table(table_data, col_widths, extra_cmds=[
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("ALIGN", (3, 1), (4, -1), "CENTER"),
    ])
    flowables.append(tbl)
    return flowables


# ── Day banner ────────────────────────────────────────────────────────────────
def _day_banner(date_val):
    data = [[f"  ◆   {str(date_val).upper()}   ◆  "]]
    tbl  = Table(data, colWidths=[9.9 * inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_DARK_BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, -1), colors.white),
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 13),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


# ── Main PDF generator ────────────────────────────────────────────────────────
def generate_pdf(df, output_path, highlight_handler=None, title=None):
    if title is None:
        title = TRIAL_TITLE
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=landscape(letter),
        rightMargin=0.5*inch, leftMargin=0.5*inch,
        topMargin=0.55*inch,  bottomMargin=0.55*inch,
    )
    title_style = ParagraphStyle(
        "Title", fontSize=18, fontName="Helvetica-Bold",
        alignment=TA_CENTER, spaceAfter=4, textColor=C_DARK_BLUE
    )
    story = [
        Paragraph(title, title_style),
        HRFlowable(width="100%", thickness=1.5, color=C_MED_BLUE, spaceAfter=8),
    ]
    for date_val in df["Date"].dropna().unique():
        date_df = df[df["Date"] == date_val]
        story.append(Spacer(1, 0.1 * inch))
        story.append(_day_banner(date_val))
        print(f"\n{date_val}:")
        for event, class_df in date_df.groupby("Event", sort=False):
            story.extend(_class_flowables(class_df, str(event), highlight_handler))
            story.append(Spacer(1, 0.1 * inch))
    if highlight_handler:
        story.extend(_gap_flowables(df, highlight_handler))
    doc.build(story)
    print(f"\nPDF saved → {output_path}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    script_dir = Path(__file__).parent
    df = load_spreadsheet(script_dir / SPREADSHEET_PATH)
    generate_pdf(
        df,
        script_dir / OUTPUT_PDF,
        highlight_handler=HIGHLIGHT_HANDLER,
        title=TRIAL_TITLE,
    )
