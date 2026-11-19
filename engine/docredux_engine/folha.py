"""QA visual: folha de contato de um PDF (60 dpi por padrao) + PNG por pagina.
Uso: python folha.py <pdf> <dir_preview> [prefixo] [--paginas-png]
"""
import sys
from pathlib import Path
import pymupdf

pdf, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
pre = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "folha"
src = pymupdf.open(pdf); n = len(src)
cols = 8; cw, ch = 250, 351; rows = (n + cols - 1) // cols
sheet = pymupdf.open(); page = sheet.new_page(width=cols * cw, height=rows * ch)
for i in range(n):
    x, y = (i % cols) * cw, (i // cols) * ch
    page.show_pdf_page(pymupdf.Rect(x + 2, y + 2, x + cw - 2, y + ch - 2), src, i)
page.get_pixmap(dpi=60).save(out / f"{pre}.png")
if "--paginas-png" in sys.argv:
    for i, p in enumerate(src):
        p.get_pixmap(dpi=90).save(out / f"{pre}_p{i+1:02d}.png")
print(n, "paginas ->", out / f"{pre}.png")
