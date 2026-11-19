"""QA visual: folha de contato de um PDF (60 dpi por padrao) + PNG por pagina.
Uso: python -m docredux_engine.folha <pdf> <dir_preview> [prefixo] [--paginas-png]
"""
import sys
from pathlib import Path
import pymupdf


def folha(pdf, out, pre="folha", dpi=60, paginas=None, paginas_png=False, dpi_png=90):
    """Folha de contato (8 colunas) de `paginas` (1-based; None = todas). Devolve [png da folha, png por pagina...]."""
    pdf, out = Path(pdf), Path(out); out.mkdir(parents=True, exist_ok=True)
    src = pymupdf.open(pdf)
    sel = [p - 1 for p in paginas] if paginas else list(range(len(src)))
    n = len(sel)
    cols = 8; cw, ch = 250, 351; rows = max(1, (n + cols - 1) // cols)
    sheet = pymupdf.open(); page = sheet.new_page(width=cols * cw, height=rows * ch)
    for k, i in enumerate(sel):
        x, y = (k % cols) * cw, (k // cols) * ch
        page.show_pdf_page(pymupdf.Rect(x + 2, y + 2, x + cw - 2, y + ch - 2), src, i)
    saida = [out / f"{pre}.png"]
    page.get_pixmap(dpi=dpi).save(saida[0])
    if paginas_png:
        for i in sel:
            p = out / f"{pre}_p{i+1:02d}.png"
            src[i].get_pixmap(dpi=dpi_png).save(p)
            saida.append(p)
    return saida


def main():
    pdf, out = Path(sys.argv[1]), Path(sys.argv[2])
    pre = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "folha"
    res = folha(pdf, out, pre, paginas_png="--paginas-png" in sys.argv)
    print(pymupdf.open(pdf).page_count, "paginas ->", res[0])


if __name__ == "__main__":
    main()
