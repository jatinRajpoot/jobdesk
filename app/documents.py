from io import BytesIO
from xml.sax.saxutils import escape


def pdf_resume(text, title):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from pathlib import Path

    regular, bold = "Helvetica", "Helvetica-Bold"
    for font, strong in [(Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")), (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))]:
        if font.exists() and strong.exists():
            if "ResumeSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("ResumeSans", str(font)))
                pdfmetrics.registerFont(TTFont("ResumeSans-Bold", str(strong)))
            regular, bold = "ResumeSans", "ResumeSans-Bold"
            break

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(8.27 * inch, 11.69 * inch), rightMargin=48, leftMargin=48, topMargin=42, bottomMargin=42, title=title, author="")
    base = ParagraphStyle("Resume", fontName=regular, fontSize=10, leading=14, textColor=colors.HexColor("#20242e"), spaceAfter=5, alignment=TA_LEFT)
    heading = ParagraphStyle("Section", parent=base, fontName=bold, fontSize=10.5, spaceBefore=12, spaceAfter=7, keepWithNext=True)
    name = ParagraphStyle("Name", parent=base, fontName=bold, fontSize=20, leading=24, spaceAfter=8, keepWithNext=True)
    bullet = ParagraphStyle("Bullet", parent=base, leftIndent=12, firstLineIndent=-8)
    story = []
    first = True
    for line in text.splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        line = line.replace("**", "").lstrip("# ")
        style = name if first else heading if line.isupper() and len(line) < 90 else bullet if line.startswith(("- ", "• ")) else base
        story.append(Paragraph(escape(line), style))
        first = False
    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawRightString(doc.pagesize[0] - 48, 24, str(doc.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return buffer.getvalue()


def docx_resume(text):
    from docx import Document
    from docx.shared import Inches, Pt
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.6)
    section.left_margin = section.right_margin = Inches(.7)
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(5)
    first = True
    for line in text.splitlines():
        line = line.strip().replace("**", "").lstrip("# ")
        if not line:
            continue
        if first:
            doc.add_heading(line, 0)
        elif line.isupper() and len(line) < 90:
            doc.add_heading(line, 2)
        elif line.startswith(("- ", "• ")):
            doc.add_paragraph(line[2:], style="List Bullet")
        else:
            doc.add_paragraph(line)
        first = False
    output = BytesIO()
    doc.save(output)
    return output.getvalue()
