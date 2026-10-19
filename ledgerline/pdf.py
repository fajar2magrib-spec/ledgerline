"""Invoice PDF engine (ReportLab).

Structure
* ``PdfTheme``          colours and sizes in one place.
* ``NumberedCanvas``    two-pass canvas that knows the page count, so the footer can say
                        "Page X of Y" on every page.
* ``InvoicePdfBuilder`` turns an ``InvoiceDetail`` into flowables: branded header, bill-to
                        and meta blocks, a line-item table whose rows grow with the text
                        and whose header repeats on each page, totals, payments, terms.

Multi-page overflow is handled by ReportLab: the item table splits by row, the totals block
is kept together, and pages after the first get a compact running header.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Optional
from xml.sax.saxutils import escape

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:  # the app keeps working; PDF buttons are disabled
    REPORTLAB_AVAILABLE = False

from .formatting import fdate, text_money
from .i18n import is_rtl, translator
from .pdftext import register_fonts, shape
from .services.invoices import InvoiceDetail
from .services.settings import HEX_COLOR, AppSettings, get_logo

NO_PDF_FONT = frozenset({"hi"})   # Devanagari etc.: the bundled font cannot draw it, so that PDF is printed in English
STATUS_HEX = {"Paid": "#0F9D74", "Partially Paid": "#3B82F6", "Pending": "#E0A100",
              "Overdue": "#E5484D", "Cancelled": "#8B95A5"}


@dataclass(frozen=True)
class PdfTheme:
    brand: str = "#2563EB"
    ink: str = "#111827"
    muted: str = "#6B7280"
    line: str = "#E5E7EB"
    soft: str = "#F3F4F6"
    tint: str = "#EFF6FF"
    margin_mm: float = 18.0


if REPORTLAB_AVAILABLE:

    class NumberedCanvas(canvas.Canvas):
        """Defers saving each page until the total page count is known."""

        footer_left = ""
        font = "Helvetica"
        page_tpl = "Page {n} of {total}"
        footer_color = "#6B7280"
        margin = 18 * mm

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._saved_page_states: list[dict] = []

        def showPage(self) -> None:  # noqa: N802 (ReportLab API)
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_footer(total)
                super().showPage()
            super().save()

        def _draw_footer(self, total: int) -> None:
            width = A4[0]
            self.setStrokeColor(colors.HexColor("#E5E7EB"))
            self.line(self.margin, 14 * mm, width - self.margin, 14 * mm)
            self.setFont(self.font, 8)
            self.setFillColor(colors.HexColor(self.footer_color))
            self.drawString(self.margin, 9.5 * mm, self.footer_left)
            self.setFillColor(colors.HexColor(self.footer_color))
            self.drawRightString(width - self.margin, 9.5 * mm, self.page_tpl.format(n=self._pageNumber, total=total))


class InvoicePdfBuilder:
    def __init__(self, detail: InvoiceDetail, settings: AppSettings, today: Optional[date] = None,
                 theme: Optional[PdfTheme] = None, show_expenses: bool = False, logo: Optional[bytes] = None,
                 extras: Optional[list] = None, lang: str = "en") -> None:
        if not REPORTLAB_AVAILABLE:
            raise RuntimeError("PDF export needs reportlab: pip install reportlab")
        self.d, self.cfg = detail, settings
        self.today = today or date.today()
        self.t = theme or PdfTheme()
        self.show_expenses = show_expenses  # internal cost: off by default, never client-facing
        self.logo = logo
        self.extras = list(extras or [])   # [(label, text)] extra invoice fields marked 'print on PDF'
        self.status = detail.display_status(self.today)
        self.lang = lang if lang not in NO_PDF_FONT else "en"       # scripts the bundled font cannot draw stay English
        self.tr = translator(self.lang)
        self.rtl = is_rtl(self.lang)
        self.f, self.fb = register_fonts() if self.lang != "en" else ("Helvetica", "Helvetica-Bold")
        self.width = A4[0] - 2 * self.t.margin_mm * mm
        self._styles()

    # ----------------------------------------------------------------- helpers
    def _m(self, value) -> str:
        return text_money(value, self.cfg.currency)

    def _c(self, hex_: str):
        return colors.HexColor(hex_)

    def _styles(self) -> None:
        def st(name: str, **kw) -> ParagraphStyle:
            base = dict(fontName=self.f, fontSize=9.5, leading=13, textColor=self._c(self.t.ink))
            base.update(kw)
            if self.rtl and "alignment" not in kw:
                base["alignment"] = TA_RIGHT      # right-to-left languages read from the right
            return ParagraphStyle(name, **base)

        self.s = {
            "body": st("body"), "small": st("small", fontSize=8.5, leading=12, textColor=self._c(self.t.muted)),
            "bold": st("bold", fontName=self.fb), "right": st("right", alignment=TA_RIGHT),
            "right_bold": st("rb", fontName=self.fb, alignment=TA_RIGHT),
            "th": st("th", fontName=self.fb, fontSize=8.5, textColor=self._c(self.t.muted)),
            "th_r": st("thr", fontName=self.fb, fontSize=8.5, textColor=self._c(self.t.muted), alignment=TA_RIGHT),
            "title": st("title", fontName=self.fb, fontSize=21, leading=25, textColor=colors.white),
            "sub": st("sub", fontSize=9, textColor=self._c("#DBEAFE")),
            "num": st("num", fontName=self.fb, fontSize=13, leading=17, textColor=colors.white, alignment=TA_RIGHT),
            "num_sub": st("numsub", fontSize=9, textColor=self._c("#DBEAFE"), alignment=TA_RIGHT),
            "section": st("section", fontName=self.fb, fontSize=11, leading=15, spaceAfter=4),
            "thanks": st("thanks", fontName=self.fb, fontSize=13, leading=18, alignment=TA_CENTER,
                         textColor=self._c(self.t.brand)),
            "due": st("due", fontName=self.fb, fontSize=12, alignment=TA_RIGHT),
        }

    def _p(self, text: object, style: str = "body") -> Paragraph:
        return Paragraph(escape(self.x(str(text))).replace("\n", "<br/>"), self.s[style])

    def x(self, text: str) -> str:
        """Right-to-left languages: join the letters and put the text in visual order."""
        return shape(text) if self.rtl else text

    def d_(self, value, fmt: str = "%b %d, %Y") -> str:
        return fdate(value, fmt, self.tr)

    # ------------------------------------------------------------------ blocks
    def _logo(self):
        """The company logo (max 55 x 16 mm, aspect ratio kept) or ``None``. A bad image never breaks the PDF."""
        if not self.logo:
            return None
        try:
            w, h = ImageReader(BytesIO(self.logo)).getSize()
            scale = min(55 * mm / w, 16 * mm / h)
            image = Image(BytesIO(self.logo), width=w * scale, height=h * scale)
            image.hAlign = "LEFT"
            return image
        except Exception:  # noqa: BLE001  corrupt or unsupported image: print the invoice without it
            return None

    def _header(self) -> Table:
        table = Table([[
            [Paragraph(escape(self.x(self.tr("{company} Invoice", company=self.cfg.company_name))), self.s["title"]),
             Paragraph(escape(self.x(self.tr("Invoice and payment receipt"))), self.s["sub"])],
            [Paragraph(escape(self.d.number), self.s["num"]),
             Paragraph(escape(self.x(self.tr("Issued {date}", date=self.d_(self.d.issue_date)))), self.s["num_sub"])],
        ]], colWidths=[self.width * 0.62, self.width * 0.38])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), self._c(self.t.brand)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 14), ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ]))
        return table

    def _parties(self) -> Table:
        d, c = self.d, self.cfg
        seller = [self._p(c.company_name, "bold")]
        for line in (c.company_address, c.company_email, f"Tax ID: {c.company_tax_id}" if c.company_tax_id else ""):
            if line:
                seller.append(self._p(line, "small"))
        buyer = [self._p(self.tr("Billed to"), "small"), self._p(d.client_name, "bold")]
        for line in (d.client_address, d.client_email, f"Tax ID: {d.client_tax_id}" if d.client_tax_id else ""):
            if line:
                buyer.append(self._p(line, "small"))
        color = STATUS_HEX.get(self.status, "#8B95A5")
        meta = Table([
            [self._p(self.tr("Invoice date"), "small"), self._p(self.d_(d.issue_date), "bold")],
            [self._p(self.tr("Due date"), "small"), self._p(self.d_(d.due_date), "bold")],
            [self._p(self.tr("Category"), "small"), self._p(self.tr(d.category), "bold")],
            [self._p(self.tr("Status"), "small"), Paragraph(f'<font color="{color}"><b>{escape(self.x(self.tr(self.status)).upper())}</b></font>', self.s["body"])],
        ], colWidths=[24 * mm, 36 * mm])
        meta.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        table = Table([[seller, buyer, meta]], colWidths=[self.width * 0.32, self.width * 0.34, self.width * 0.34])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        return table

    def _items(self) -> Table:
        tax_label = self.cfg.tax_label
        head = [self._p("#", "th"), self._p(self.tr("Description"), "th"), self._p(self.tr("Qty"), "th_r"), self._p(self.tr("Unit price"), "th_r"),
                self._p(self.tr("Discount"), "th_r"), self._p(f"{tax_label} %", "th_r"), self._p(self.tr("Amount"), "th_r")]
        rows = [head]
        for it in self.d.items:
            rows.append([
                self._p(it.position, "small"), self._p(it.description),
                self._p(f"{it.quantity.normalize():f}", "right"), self._p(self._m(it.unit_price), "right"),
                self._p(self._m(it.line_discount) if it.line_discount else "-", "right"),
                self._p(f"{it.tax_rate.normalize():f}%" if it.tax_rate else "-", "right"),
                self._p(self._m(it.line_subtotal - it.line_discount), "right"),
            ])
        w = self.width
        fixed = [9 * mm, 12 * mm, 24 * mm, 21 * mm, 14 * mm, 26 * mm]  # #, qty, unit price, discount, tax %, amount
        table = Table(rows, colWidths=[fixed[0], w - sum(fixed), *fixed[1:]], repeatRows=1)  # header repeats per page
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self._c(self.t.soft)), ("BOX", (0, 0), (-1, -1), 0.5, self._c(self.t.line)),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, self._c(self.t.line)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return table

    def _totals(self) -> Table:
        d = self.d
        rows = [[self.tr("Subtotal"), self._m(d.subtotal)], [self.tr("Discounts"), (f"-{self._m(d.discount_total)}" if d.discount_total else self._m(d.discount_total))],
                [self.cfg.tax_label, self._m(d.tax_total)]]
        if self.show_expenses:
            rows.append([self.tr("Expenses (internal)"), self._m(d.expenses)])
        rows += [[self.tr("Total"), self._m(d.total)], [self.tr("Paid to date"), f"-{self._m(d.amount_paid)}"],
                 [self.tr("Balance due"), self._m(d.balance)]]
        data = [[self._p(k, "right"), self._p(v, "right")] for k, v in rows]
        total_row, balance_row = len(rows) - 3, len(rows) - 1
        for r in (total_row, balance_row):
            data[r] = [self._p(rows[r][0], "right_bold"), self._p(rows[r][1], "right_bold")]
        table = Table(data, colWidths=[40 * mm, 34 * mm], hAlign="RIGHT")
        table.setStyle(TableStyle([
            ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, self._c(self.t.ink)),
            ("BACKGROUND", (0, balance_row), (-1, balance_row), self._c(self.t.tint)),
            ("LINEABOVE", (0, balance_row), (-1, balance_row), 1, self._c(self.t.brand)),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        return table

    def _payments(self) -> list:
        if not self.d.payments:
            return []
        rows = [[self._p(self.tr("Date"), "th"), self._p(self.tr("Method"), "th"), self._p(self.tr("Reference"), "th"), self._p(self.tr("Amount"), "th_r")]]
        for p in self.d.payments:
            rows.append([self._p(self.d_(p.paid_on)), self._p(self.tr(p.method)), self._p(p.reference or "-"),
                         self._p(self._m(p.amount), "right")])
        table = Table(rows, colWidths=[32 * mm, 40 * mm, self.width - 32 * mm - 40 * mm - 32 * mm, 32 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), self._c(self.t.soft)), ("BOX", (0, 0), (-1, -1), 0.5, self._c(self.t.line)),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, self._c(self.t.line)),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return [KeepTogether([self._p(self.tr("Payments received"), "section"), table])]

    def _terms(self) -> list:
        note = {"Paid": "This invoice has been paid in full. No further action is needed.",
                "Partially Paid": "A partial payment has been received. Please settle the remaining balance by the due date.",
                "Pending": "Payment is due by the date shown above.",
                "Overdue": "This invoice is past its due date. Please arrange payment as soon as possible.",
                "Cancelled": "This invoice has been cancelled and no payment is required."}.get(self.status, "")
        terms = (self.tr("Payment is due by {due} (terms: {days} days). Please quote invoice number {number} with your payment.",
                         due=self.d_(self.d.due_date), days=self.cfg.payment_terms_days, number=self.d.number)
                 + (" " + self.tr(note) if note else ""))
        blocks = [self._p(self.tr("Payment terms"), "section"), self._p(terms)]
        for label, text in self.extras:
            blocks += [Spacer(1, 4), self._p(f"{label}: {text}")]
        if self.d.notes:
            blocks += [Spacer(1, 8), self._p(self.tr("Notes"), "section"), self._p(self.d.notes)]
        blocks += [Spacer(1, 22), Paragraph(escape(self.x(self.tr("Thank you for your business!"))), self.s["thanks"])]
        return [KeepTogether(blocks)]

    # ------------------------------------------------------------------- build
    def build(self) -> bytes:
        buffer = BytesIO()
        theme = self.t
        footer_text = self.x(self.tr("Generated by {company} on {date}", company=self.cfg.company_name, date=self.d_(self.today)))
        page_text, body_font, bold_font = self.tr("Page {n} of {total}"), self.f, self.fb

        class _Canvas(NumberedCanvas):
            footer_left = footer_text
            page_tpl = page_text
            font = body_font
            margin = theme.margin_mm * mm

        def running_header(canv, doc) -> None:  # pages 2+
            if doc.page > 1:
                canv.saveState()
                canv.setFont(bold_font, 9)
                canv.setFillColor(self._c(theme.muted))
                canv.drawString(theme.margin_mm * mm, A4[1] - 12 * mm,
                                self.x(self.tr("{company}  |  Invoice {number} (continued)", company=self.cfg.company_name, number=self.d.number)))
                canv.restoreState()

        doc = SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=theme.margin_mm * mm, rightMargin=theme.margin_mm * mm,
            topMargin=18 * mm, bottomMargin=20 * mm, title=f"Invoice {self.d.number}", author=self.cfg.company_name,
        )
        logo = self._logo()
        story: list = [*([logo, Spacer(1, 8)] if logo else []), self._header(), Spacer(1, 14), self._parties(), Spacer(1, 16), self._items(), Spacer(1, 12),
                       KeepTogether([self._totals()]), Spacer(1, 16), *self._payments(), Spacer(1, 14), *self._terms()]
        doc.build(story, onFirstPage=running_header, onLaterPages=running_header, canvasmaker=_Canvas)
        return buffer.getvalue()


def _mix_with_white(hex_: str, ratio: float) -> str:
    """Light tint of a brand colour (``ratio`` 0 = the colour, 1 = white)."""
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(round(c + (255 - c) * ratio) for c in (r, g, b))


def build_invoice_pdf(detail: InvoiceDetail, settings: AppSettings, today: Optional[date] = None,
                      show_expenses: bool = False, extras: Optional[list] = None, lang: str = "en") -> bytes:
    try:
        logo = get_logo()
    except Exception:  # noqa: BLE001  the invoice still prints without a logo
        logo = None
    brand = settings.brand_color if HEX_COLOR.match(settings.brand_color or "") else PdfTheme.brand
    theme = PdfTheme(brand=brand, tint=_mix_with_white(brand, 0.92))
    return InvoicePdfBuilder(detail, settings, today, theme=theme, show_expenses=show_expenses, logo=logo, extras=extras, lang=lang).build()
