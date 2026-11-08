"""
invoice_pdf.py

Builds the invoice PDF (Letter size) from db.invoice_detail(): work, then payments & barter, then the balance.
"""

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import db

NAVY = colors.HexColor('#1E3A8A')
GREY = colors.HexColor('#64748B')
LINE = colors.HexColor('#CBD5E1')
SHADE = colors.HexColor('#F1F5F9')

_ss = getSampleStyleSheet()
H1 = ParagraphStyle('h1', parent=_ss['Title'], alignment=0, textColor=NAVY, fontSize=22, leading=26, spaceAfter=8)
SMALL = ParagraphStyle('small', parent=_ss['Normal'], fontSize=9, leading=12, textColor=GREY)
BODY = ParagraphStyle('body', parent=_ss['Normal'], fontSize=10, leading=13)
CELL = ParagraphStyle('cell', parent=BODY, fontSize=9.5, leading=12)
NOTE = ParagraphStyle('note', parent=CELL, fontSize=8.5, leading=10.5, textColor=GREY)
RIGHT = ParagraphStyle('right', parent=BODY, alignment=TA_RIGHT)
H2 = ParagraphStyle('h2', parent=_ss['Heading2'], fontSize=12, textColor=NAVY, spaceBefore=14, spaceAfter=6)


def _esc(s) -> str:
    return (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _lines(text) -> str:
    return '<br/>'.join(_esc(line) for line in str(text or '').splitlines() if line.strip())


def _desc(r) -> Paragraph:
    note = r['comments']
    if r['kind'] == 'class' and r['status'] in ('subbed_out', 'cancelled'):
        note = f"{db.CLASS_STATUSES[r['status']]}" + (f": {r['comments']}" if r['comments'] else '')
    text = _esc(r['description']) + (f"<br/><font size=8.5 color='#64748B'>{_esc(note)}</font>" if note else '')
    return Paragraph(text, CELL)


def _qty(r) -> str:
    if r['kind'] == 'trial':
        return f"{r['quantity']:g} runs"
    if r['kind'] in ('class', 'sub') and r['amount_cents']:
        return f"{r['hours']:g} h"
    return ''


def _rate(r) -> str:
    if r['kind'] in ('class', 'sub') and r['amount_cents']:
        return f"{db.money(r['rate_cents'])}/h"
    if r['kind'] == 'trial':
        return f"{db.money(r['rate_cents'])}/run"
    return ''


def _table(data, widths, total_row=False):
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 9), ('TEXTCOLOR', (0, 0), (-1, 0), GREY),
        ('FONT', (0, 1), (-1, -1), 'Helvetica', 9.5),
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, NAVY), ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (-3, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    if total_row:
        style += [('FONT', (0, -1), (-1, -1), 'Helvetica-Bold', 9.5), ('BACKGROUND', (0, -1), (-1, -1), SHADE),
                  ('LINEBELOW', (0, -1), (-1, -1), 0, colors.white)]
    t.setStyle(TableStyle(style))
    return t


def build_pdf(detail: dict, settings: dict) -> bytes:
    inv = detail['invoice']
    rows = detail['rows']
    work = [r for r in rows if r['kind'] not in db.DEBIT_KINDS and r['status'] != 'no_class']
    debits = [r for r in rows if r['kind'] in db.DEBIT_KINDS]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"Invoice {inv['number']}", author=settings['instructor_name'])
    full = doc.width
    story = []

    head_left = [Paragraph('INVOICE', H1),
                 Paragraph(f"<b>{_esc(settings['instructor_name'])}</b>", BODY),
                 Paragraph(_lines(settings['instructor_contact']), SMALL)]
    head_right = [Paragraph(f"<b>Invoice #{inv['number']}</b>", RIGHT),
                  Paragraph(f"Date: {inv['invoice_date']:%B %-d, %Y}", RIGHT),
                  Paragraph(f"Work through: {inv['period_end']:%B %-d, %Y}", RIGHT)]
    t = Table([[head_left, head_right]], colWidths=[full * 0.6, full * 0.4])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                           ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story += [t, Spacer(1, 14), Paragraph('<b>Bill to</b>', SMALL),
              Paragraph(f"<b>{_esc(settings['client_name'])}</b>", BODY),
              Paragraph(_lines(settings['client_address']), SMALL)]

    widths = [0.95 * inch, None, 0.75 * inch, 0.8 * inch, 0.9 * inch]
    widths[1] = full - sum(w for w in widths if w)

    story.append(Paragraph('Work', H2))
    if work:
        data = [['Date', 'Description', 'Qty', 'Rate', 'Amount']]
        for r in work:
            data.append([f"{r['entry_date']:%-m/%-d/%Y}", _desc(r), _qty(r), _rate(r), db.money(r['amount_cents'])])
        data.append(['', 'Total work', '', '', db.money(detail['credits'])])
        story.append(_table(data, widths, total_row=True))
    else:
        story.append(Paragraph('No work this period.', SMALL))

    if debits:
        story.append(Paragraph('Payments &amp; barter', H2))
        data = [['Date', 'Description', '', 'Type', 'Amount']]
        for r in debits:
            data.append([f"{r['entry_date']:%-m/%-d/%Y}" if r['entry_date'] else '', _desc(r), '',
                         db.KINDS[r['kind']], db.money(r['amount_cents'])])
        data.append(['', 'Total payments & barter', '', '', db.money(detail['debits'])])
        story.append(_table(data, widths, total_row=True))

    story.append(Spacer(1, 16))
    summary = [['Previous balance', db.money(detail['previous'])],
               ['Work this period', db.money(detail['credits'])],
               ['Payments & barter this period', db.money(detail['debits'])],
               ['Balance owed', db.money(detail['balance'])]]
    t = Table(summary, colWidths=[2.6 * inch, 1.2 * inch], hAlign='RIGHT')
    t.setStyle(TableStyle([
        ('FONT', (0, 0), (-1, -2), 'Helvetica', 10), ('TEXTCOLOR', (0, 0), (0, -2), GREY),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('LINEABOVE', (0, -1), (-1, -1), 1, NAVY),
        ('FONT', (0, -1), (-1, -1), 'Helvetica-Bold', 12), ('TEXTCOLOR', (0, -1), (-1, -1), NAVY),
        ('TOPPADDING', (0, -1), (-1, -1), 7),
    ]))
    story.append(t)

    if settings.get('payment_note', '').strip():
        story += [Spacer(1, 18), Paragraph(_lines(settings['payment_note']), SMALL)]

    doc.build(story)
    return buf.getvalue()
