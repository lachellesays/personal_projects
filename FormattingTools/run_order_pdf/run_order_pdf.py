"""
run_order_pdf.py — turn UKI Manager's full-weekend run order export (grid_print_run_order.xls) into a
clean, printable PDF with conflicts highlighted.

Usage (from this folder):
    uv run --no-project --python 3.12 --with python-calamine --with pandas --with reportlab \
        python run_order_pdf.py ~/Downloads/grid_print_run_order.xls --saturday 2026-11-07 \
        --out ~/Downloads/"LUDS November 2026 Run Order.pdf"

Format (agreed with Lachelle, Nov 2026):
- Cover page: title, trial name + dates, total runs and classes; per day a table of Order / Class / Runs with a
  "Total for the day" row and the day's run count in the heading. NO conflict info on the cover.
- One class per page (long classes continue with repeated headings). Heading shows class, date, dogs, conflicts.
- Classes split by level get Senior/Champ and Beginner/Novice bars; open classes (levels mixed, e.g. Gamblers,
  Open Speedstakes) get no level bars and "· all levels together" by the name.
- No divider lines between heights/Regular-Select. No bold anywhere in rows. Select shown as a small purple SELECT tag.
- Conflict = fewer than 10 dogs since the same handler's last run in that class: row light red, Gap number in red
  (number only). The handler's earlier run in that conflict is pale yellow. Key in the footer (not on page 1).
- UKI's export names Agility 1 and 2 both "Agility" (same for Jumping, Sunday Speedstakes); split.py splits them
  where dogs start repeating, at the last level/height restart.
"""
import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from split import GAP, add_gaps, group, load, split_classes

ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
ap.add_argument('xls', help="UKI Manager's grid_print_run_order.xls")
ap.add_argument('--saturday', required=True, help='Saturday date, e.g. 2026-11-07 (Sunday is the day after)')
ap.add_argument('--trial', default='Level Up Dog Sports · UKI Agility Trial')
ap.add_argument('--out', required=True, help='PDF to write')
args = ap.parse_args()
SRC, OUT, TRIAL = os.path.expanduser(args.xls), os.path.expanduser(args.out), args.trial
_sat = date.fromisoformat(args.saturday)
_sun = _sat + timedelta(days=1)
DATES = {'Saturday': f'Saturday, {_sat:%B} {_sat.day}, {_sat.year}', 'Sunday': f'Sunday, {_sun:%B} {_sun.day}, {_sun.year}'}
SPAN = (f'{_sat:%B} {_sat.day}–{_sun.day}, {_sat.year}' if _sat.month == _sun.month
        else f'{_sat:%B} {_sat.day} – {_sun:%B} {_sun.day}, {_sun.year}')

NAVY = colors.HexColor('#1E3A8A'); INK = colors.HexColor('#0F172A'); MUTED = colors.HexColor('#64748B')
LINE = colors.HexColor('#E2E8F0'); BAND = colors.HexColor('#F1F5F9')
RED = colors.HexColor('#B91C1C'); RED_BG = colors.HexColor('#FEE2E2'); AMBER_BG = colors.HexColor('#FFF7D6')
GREEN = colors.HexColor('#166534'); GREEN_BG = colors.HexColor('#DCFCE7')

H1 = ParagraphStyle('h1', fontName='Helvetica-Bold', fontSize=26, leading=30, textColor=INK)
SUB = ParagraphStyle('sub', fontName='Helvetica', fontSize=12, leading=16, textColor=MUTED)
H2 = ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=NAVY, spaceBefore=6)
BODY = ParagraphStyle('body', fontName='Helvetica', fontSize=9.5, leading=13, textColor=INK)
CELL = ParagraphStyle('cell', fontName='Helvetica', fontSize=8.8, leading=10.5, textColor=INK, alignment=TA_LEFT)
CELL_B = ParagraphStyle('cellb', parent=CELL, fontName='Helvetica-Bold')
CELL_M = ParagraphStyle('cellm', parent=CELL, textColor=MUTED, fontSize=8)

classes = [add_gaps(c) for c in split_classes(load(SRC))]
days = []
for c in classes:
    if c['day'] not in days:
        days.append(c['day'])


def page_frame(canvas, doc, legend=True):
    canvas.saveState()
    w, h = letter
    canvas.setFont('Helvetica-Bold', 8.5); canvas.setFillColor(NAVY)
    canvas.drawString(0.5 * inch, h - 0.38 * inch, TRIAL + ' · Run Order')
    canvas.setFont('Helvetica', 8); canvas.setFillColor(MUTED)
    canvas.drawRightString(w - 0.5 * inch, h - 0.38 * inch, SPAN)
    canvas.setStrokeColor(LINE); canvas.line(0.5 * inch, h - 0.45 * inch, w - 0.5 * inch, h - 0.45 * inch)
    if not legend:
        canvas.setFont('Helvetica', 8); canvas.setFillColor(MUTED)
        canvas.drawRightString(w - 0.5 * inch, 0.34 * inch, f'Page {doc.page}')
        canvas.restoreState()
        return
    canvas.setFillColor(RED_BG); canvas.rect(0.5 * inch, 0.33 * inch, 8, 8, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.5 * inch + 12, 0.34 * inch, f'Conflict: fewer than {GAP} dogs since the same handler’s last run (number shown in Gap)')
    canvas.setFillColor(AMBER_BG); canvas.rect(0.5 * inch + 330, 0.33 * inch, 8, 8, fill=1, stroke=0)
    canvas.setFillColor(MUTED); canvas.drawString(0.5 * inch + 342, 0.34 * inch, 'The handler’s earlier run in that conflict')
    canvas.drawRightString(w - 0.5 * inch, 0.34 * inch, f'Page {doc.page}')
    canvas.restoreState()


def summary():
    out = [Paragraph('Run Order', H1), Spacer(1, 4),
           Paragraph(f'{TRIAL} · {SPAN}', SUB), Spacer(1, 16),
           Paragraph(f'<b>{sum(len(c["rows"]) for c in classes)} runs</b> in <b>{len(classes)} classes</b>', BODY), Spacer(1, 14)]
    for d in days:
        day_classes = [c for c in classes if c['day'] == d]
        runs = sum(len(c['rows']) for c in day_classes)
        data = [['Order', 'Class', 'Runs']]
        data += [[str(n), c['name'], str(len(c['rows']))] for n, c in enumerate(day_classes, start=1)]
        data.append(['', 'Total for the day', str(runs)])
        n = len(data) - 1
        t = Table(data, colWidths=[0.6 * inch, 3.4 * inch, 0.9 * inch], hAlign='LEFT')
        t.setStyle(TableStyle([
            ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 8.5), ('TEXTCOLOR', (0, 0), (-1, 0), MUTED),
            ('LINEBELOW', (0, 0), (-1, 0), 0.8, LINE), ('FONT', (0, 1), (-1, -1), 'Helvetica', 10),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, BAND]), ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('FONT', (0, n), (-1, n), 'Helvetica-Bold', 10), ('LINEABOVE', (0, n), (-1, n), 0.8, colors.HexColor('#CBD5E1'))]))
        out += [Paragraph(f'{DATES.get(d, d)} <font size="11" color="#64748B">· {runs} runs</font>', H2), Spacer(1, 4), t, Spacer(1, 16)]
    return out


def is_open(c):
    """All levels run together (Gamblers, Open Speedstakes): the level groups are mixed instead of two blocks."""
    segments = 0
    for i, r in enumerate(c['rows']):
        if i == 0 or group(r['Level']) != group(c['rows'][i - 1]['Level']):
            segments += 1
    return segments > 2


def class_table(c):
    open_class = is_open(c)
    head = ['#', 'Height', 'Level', 'Dog', 'Breed', 'Handler', 'Gap']
    data, styles = [head], [
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 8), ('TEXTCOLOR', (0, 0), (-1, 0), MUTED),
        ('BACKGROUND', (0, 0), (-1, 0), BAND), ('LINEBELOW', (0, 0), (-1, 0), 0.8, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'), ('ALIGN', (6, 0), (6, -1), 'CENTER'),
        ('BOX', (0, 0), (-1, -1), 0.6, LINE)]
    prev_group, prev_block = None, None
    for r in c['rows']:
        g = group(r['Level'])
        if g != prev_group and g != 'Masters' and not open_class:
            data.append([Paragraph(f'<b>{g}</b>', ParagraphStyle('g', parent=CELL, textColor=NAVY)), '', '', '', '', '', ''])
            i = len(data) - 1
            styles += [('SPAN', (0, i), (-1, i)), ('BACKGROUND', (0, i), (-1, i), colors.HexColor('#EEF2FF'))]
            prev_block = None
        block = (r['Height'], r['Class Type'])
        sel = r['Class Type'] == 'Select'
        height = f'{int(r["Height"])}"' + (' <font size="7" color="#5B21B6">SELECT</font>' if sel else '')
        data.append([str(r['seq']), Paragraph(height, CELL), r['Level'],
                     Paragraph(r['Dog'], CELL), Paragraph(r['Breed'], CELL_M), Paragraph(r['Handler Name'], CELL),
                     '' if r['gap'] is None else str(r['gap'])])
        i = len(data) - 1
        if r['conflict']:
            styles += [('BACKGROUND', (0, i), (-1, i), RED_BG), ('TEXTCOLOR', (6, i), (6, i), RED),
                       ('FONT', (6, i), (6, i), 'Helvetica', 9)]
        elif r.get('conflict_partner'):
            styles.append(('BACKGROUND', (0, i), (-1, i), AMBER_BG))
        else:
            styles.append(('TEXTCOLOR', (6, i), (6, i), MUTED))
        prev_group, prev_block = g, block
    styles = styles[:9] + [('FONT', (0, 1), (0, -1), 'Helvetica', 9), ('FONT', (2, 1), (2, -1), 'Helvetica', 8.8),
                           ('FONT', (6, 1), (6, -1), 'Helvetica', 9)] + styles[9:]   # per-row styles win
    t = Table(data, colWidths=[0.38 * inch, 0.95 * inch, 0.8 * inch, 1.45 * inch, 1.5 * inch, 1.75 * inch, 0.55 * inch],
              repeatRows=1, hAlign='LEFT')
    t.setStyle(TableStyle(styles))
    return t


def class_header(c):
    badge = (f'<font color="#B91C1C"><b>{c["conflicts"]} conflict{"s" if c["conflicts"] != 1 else ""}</b></font>'
             if c['conflicts'] else '<font color="#166534"><b>No conflicts</b></font>')
    return [Paragraph(f'{c["name"]}' + (' <font size="10" color="#64748B">· all levels together</font>' if is_open(c) else ''), H2),
            Paragraph(f'{DATES.get(c["day"], c["day"])} · {len(c["rows"])} dogs · {badge}', SUB), Spacer(1, 6)]


story = summary()
for d in days:
    for n, c in enumerate([c for c in classes if c['day'] == d]):
        story.append(PageBreak())
        story += [KeepTogether(class_header(c)), class_table(c)]

doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.65 * inch,
                        bottomMargin=0.6 * inch, title=f'Run Order · {SPAN}', author='Level Up Dog Sports')
doc.build(story, onFirstPage=lambda c, d: page_frame(c, d, legend=False), onLaterPages=page_frame)
print('pages written to', OUT)
