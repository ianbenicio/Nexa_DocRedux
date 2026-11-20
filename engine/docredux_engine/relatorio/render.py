"""DocRedux - renderizador do tipo `relatorio` (R32-R48): modelo.json -> DOCX.

Chamado por ficha/render.renderizar() quando meta.tipo == "relatorio". Usa a mesma CFG (A4, margem 1,5 cm, Carlito 10,5).
Elementos: id, front, h1-h4, p, quote, bullet, ref, fig(caption, body img|table, fontes), fonte, siglas, toc, notas.
"""
import json, re, sys
from pathlib import Path
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Cm, RGBColor

from .extract import join_text
from ..ficha import render as RD
from ..ficha.render import CFG, A4_W, A4_H, shade, borders, fmt, no_table_borders
from .. import soffice
from ..pdftexto import DICT

COR = RGBColor(0x1F, 0x4E, 0x79)  # azul sobrio do relatorio
COR_HEX = "1F4E79"
CINZA = RGBColor(0x55, 0x55, 0x55)
B, I, SUP = 1, 2, 4


def add_runs(p, runs, size=None, color=None, bold=None, italic=None):
    for txt, fl in runs:
        r = p.add_run(txt)
        if size:
            r.font.size = Pt(size)
        if color is not None:
            r.font.color.rgb = color
        if (fl & B) or bold:
            r.bold = True
        if (fl & I) or italic:
            r.italic = True
        if fl & SUP:
            r.font.superscript = True
    return p


SUP_UNI = "⁰¹²³⁴⁵⁶⁷⁸⁹"
SUP_NORMAL = str.maketrans(SUP_UNI, "0123456789")
RE_SUP_UNI = re.compile(f"([{SUP_UNI}]+)")


def add_text(p, text):
    """Texto de titulo/legenda: digitos sobrescritos Unicode (marcador de nota, story 2.7) viram run sobrescrito
    com digito normal. Devolve os runs criados, para o chamador aplicar fonte/cor."""
    runs = []
    for k, parte in enumerate(RE_SUP_UNI.split(text)):
        if not parte:
            continue
        r = p.add_run(parte.translate(SUP_NORMAL))
        if k % 2:
            r.font.superscript = True
        runs.append(r)
    return runs


def page_field(par, size=8):
    for kind, txt in (("begin", None), (None, "PAGE"), ("end", None)):
        r = par.add_run(); r.font.size = Pt(size); r.font.color.rgb = CINZA
        if kind:
            f = OxmlElement("w:fldChar"); f.set(qn("w:fldCharType"), kind); r._r.append(f)
        else:
            it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = txt; r._r.append(it)


def cell_margins(tbl, pt=1.5):
    tblPr = tbl._tbl.tblPr
    m = OxmlElement("w:tblCellMar")
    for s in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{s}"); e.set(qn("w:w"), str(int(pt * 20))); e.set(qn("w:type"), "dxa"); m.append(e)
    tblPr.append(m)


def fixed_layout(tbl):
    e = OxmlElement("w:tblLayout"); e.set(qn("w:type"), "fixed"); tbl._tbl.tblPr.append(e)


def table_borders(tbl, color="9A9A9A", sz=4):
    b = OxmlElement("w:tblBorders")
    for s in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{s}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz)); e.set(qn("w:color"), color); b.append(e)
    tbl._tbl.tblPr.append(b)


def cant_split(row):
    trPr = row._tr.get_or_add_trPr(); e = OxmlElement("w:cantSplit"); trPr.append(e)


class RR:
    def __init__(self, out, toc_pages=None):
        self.out = out
        self.toc_pages = toc_pages or {}
        self.doc = Document()
        st = self.doc.styles["Normal"]
        st.font.name = CFG["fonte"]; st.font.size = Pt(CFG["tam"])
        st.element.rPr.rFonts.set(qn("w:eastAsia"), CFG["fonte"])
        st.paragraph_format.space_after = Pt(2); st.paragraph_format.line_spacing = 1.0
        for name, size in (("Heading 1", 15), ("Heading 2", 11.5), ("Heading 3", 11), ("Heading 4", 10.5)):
            h = self.doc.styles[name]
            h.font.name = CFG["fonte"]; h.font.size = Pt(size); h.font.bold = True; h.font.color.rgb = COR
            h.element.rPr.rFonts.set(qn("w:eastAsia"), CFG["fonte"])
            for a in ("w:asciiTheme", "w:hAnsiTheme"):
                rf = h.element.rPr.rFonts
                if rf.get(qn(a)) is not None:
                    del rf.attrib[qn(a)]
        s = self.doc.sections[0]
        s.page_width, s.page_height = Pt(A4_W), Pt(A4_H)
        m = Cm(CFG["margem_cm"])
        s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = m
        s.footer_distance = Cm(0.6)
        fp = s.footer.paragraphs[0]; fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        page_field(fp)
        self.text_w = A4_W - 2 * m.pt
        self.stats = {"imagens": 0, "tabelas": 0, "pares_lado_a_lado": 0}
        self.h1_pt = 15

    # ------------------------------------------------------------ blocos simples
    def ident(self, el):
        t = self.doc.add_table(rows=1, cols=1); t.alignment = WD_TABLE_ALIGNMENT.CENTER
        c = t.rows[0].cells[0]; shade(c, "DCE6F1")
        p = c.paragraphs[0]; fmt(p, 2, 0)
        r = p.add_run(el["text"]); r.bold = True; r.font.size = Pt(13); r.font.color.rgb = COR
        p2 = c.add_paragraph(); fmt(p2, 0, 2)
        r2 = p2.add_run(f"Versão compacta gerada a partir de: {el['arquivo']}"); r2.font.size = Pt(8); r2.font.color.rgb = CINZA

    def front(self, blocks):
        t = self.doc.add_table(rows=1, cols=1); t.style = "Table Grid"
        c = t.rows[0].cells[0]; shade(c, "F4F4F4")
        first = True
        for b in blocks:
            lines = b["lines"]
            if not lines[0].isupper():  # ficha catalografica: reflui as linhas quebradas pela diagramacao
                m = [lines[0]]
                for ln in lines[1:]:
                    m[-1:] = [join_text(m[-1], ln)] if len(m[-1]) >= 45 else [m[-1], ln]
                lines = m
            for k, line in enumerate(lines):
                p = c.paragraphs[0] if first else c.add_paragraph(); first = False
                fmt(p, 3 if (k == 0 and p is not c.paragraphs[0]) else 0, 0)
                r = p.add_run(line); r.font.size = Pt(8.5)
                if k == 0 and len(b["lines"]) > 1 and line.isupper():
                    r.bold = True

    def h(self, lvl, el):
        p = self.doc.add_heading(level=lvl)
        if lvl == 1:
            fmt(p, 16, 4, keep=True); borders(p, ("bottom",), COR_HEX, 8)
        elif lvl == 2:
            fmt(p, 8, 2, keep=True)
            if el.get("sintetico"):
                for r in p.runs: pass
        elif lvl == 3:
            fmt(p, 6, 2, keep=True)
        else:
            fmt(p, 4, 1, keep=True)
        for r in add_text(p, el["text"]):
            if lvl == 4:
                r.italic = True
            if lvl == 2 and el.get("sintetico"):
                r.font.size = Pt(10)
        return p

    def para(self, el):
        p = self.doc.add_paragraph(); fmt(p, 0, 3)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        if not el.get("continua"):
            p.paragraph_format.first_line_indent = Cm(0.5)
        add_runs(p, el["runs"])
        return p

    def quote(self, el):
        p = self.doc.add_paragraph(); fmt(p, 1, 3, 1.0)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        borders(p, ("left",), COR_HEX, 12)
        add_runs(p, el["runs"], size=9.5)

    def bullet(self, el):
        p = self.doc.add_paragraph(); fmt(p, 0, 1.5, 1.0, 0.45)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        r = p.add_run("•\t"); r.font.color.rgb = COR
        p.paragraph_format.tab_stops.add_tab_stop(Cm(1.0))
        add_runs(p, el["runs"])

    def ref(self, el):
        p = self.doc.add_paragraph(); fmt(p, 0, 2, 0.6, 0.6)
        add_runs(p, el["runs"], size=9)

    def fonte(self, runs, keep=False):
        p = self.doc.add_paragraph(); fmt(p, 0, 3)
        add_runs(p, runs, size=8.5, color=CINZA)
        p.paragraph_format.keep_together = True
        if keep:
            p.paragraph_format.keep_with_next = True
        return p

    def notas(self, el):
        for n in el["itens"]:
            p = self.doc.add_paragraph(); fmt(p, 0, 1.5, 0.6, 0.6)
            r = p.add_run(f"{n['n']}.\t"); r.font.size = Pt(8.5); r.bold = True
            p.paragraph_format.tab_stops.add_tab_stop(Cm(0.6))
            add_runs(p, n["runs"], size=8.5)

    def siglas(self, el):
        it = el["itens"]; half = (len(it) + 1) // 2
        t = self.doc.add_table(rows=half, cols=4); no_table_borders(t); fixed_layout(t); cell_margins(t, 2)
        w = [0.09, 0.41, 0.09, 0.41]
        for k, d in enumerate(it):
            row, col = (k, 0) if k < half else (k - half, 2)
            for j, txt, bold in ((col, d["sigla"], True), (col + 1, d["sig"], False)):
                c = t.rows[row].cells[j]; c.width = Pt(self.text_w * w[j])
                p = c.paragraphs[0]; fmt(p, 0, 0); r = p.add_run(txt); r.font.size = Pt(8.5); r.bold = bold
        for row in t.rows:
            for j, c in enumerate(row.cells):
                c.width = Pt(self.text_w * w[j])

    def toc(self, el):
        for e in el["itens"]:
            p = self.doc.add_paragraph(); fmt(p, 0, 1.5)
            p.paragraph_format.tab_stops.add_tab_stop(Pt(self.text_w), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
            r = p.add_run(e["text"]); r.font.size = Pt(10)
            r2 = p.add_run("\t" + str(self.toc_pages.get(e["text"], "")))
            r2.font.size = Pt(10)

    # ------------------------------------------------------------ figuras e tabelas
    def table(self, body, keep=True):
        sc = min(1.0, self.text_w / max(1, sum(body["colw"])))
        colw = [w * sc for w in body["colw"]]
        nr, nc = body["nrows"], body["ncols"]
        size = max(body["size"], 6.5)
        t = self.doc.add_table(rows=nr, cols=nc); t.alignment = WD_TABLE_ALIGNMENT.CENTER
        fixed_layout(t); table_borders(t); cell_margins(t, 1.5)
        for i, row in enumerate(t.rows):
            cant_split(row)
            for j, c in enumerate(row.cells):
                c.width = Pt(colw[j])
        for cd in body["cells"]:
            r0, c0, rs, cs = cd["r"], cd["c"], cd["rs"], cd["cs"]
            if r0 + rs > nr or c0 + cs > nc:
                continue
            c = t.cell(r0, c0)
            if rs > 1 or cs > 1:
                c = c.merge(t.cell(r0 + rs - 1, c0 + cs - 1))
            if cd["fill"]:
                shade(c, cd["fill"])
            p = c.paragraphs[0]; fmt(p, 0, 0)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT if c0 == 0 and cs == 1 else WD_ALIGN_PARAGRAPH.CENTER
            if keep and r0 < nr - 1:
                p.paragraph_format.keep_with_next = True
            r = p.add_run(cd["text"]); r.font.size = Pt(size)
            if cd.get("neg"):
                r.bold = True
            col = cd.get("cor", "231F20")
            r.font.color.rgb = RGBColor.from_string(col.upper())
        self.stats["tabelas"] += 1
        return t

    def picture(self, par, body, width=None):
        w = width or min(body["w"], self.text_w)
        par.add_run().add_picture(str(self.out / "img" / body["file"]), width=Pt(w))
        self.stats["imagens"] += 1

    def fig(self, el):
        """R37: legenda + figura/tabela + fontes presas; caption keep_with_next."""
        b = el["body"]
        if el["caption"]:
            p = self.doc.add_paragraph(); fmt(p, 6, 2, keep=True)
            for r in add_text(p, el["caption"]):
                r.bold = True; r.font.size = Pt(9.5); r.font.color.rgb = COR
        if b and b["t"] == "img":
            p = self.doc.add_paragraph(); fmt(p, 0, 2, keep=bool(el["fontes"]))
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self.picture(p, b)
        elif b and b["t"] == "table":
            self.table(b, keep=True)
        for k, f in enumerate(el["fontes"]):
            self.fonte(f, keep=False)
        if not el["fontes"]:
            sp = self.doc.add_paragraph(); fmt(sp, 0, 2)

    def fig_pair(self, a, b):
        """R46: dois graficos que cabem lado a lado, na mesma linha, cada um com legenda e fonte."""
        t = self.doc.add_table(rows=1, cols=2); no_table_borders(t); fixed_layout(t); cell_margins(t, 2)
        cant_split(t.rows[0])
        half = self.text_w / 2
        for c, el in zip(t.rows[0].cells, (a, b)):
            c.width = Pt(half)
            first = True
            if el["caption"]:
                p = c.paragraphs[0]; first = False; fmt(p, 0, 2, keep=True)
                for r in add_text(p, el["caption"]):
                    r.bold = True; r.font.size = Pt(9); r.font.color.rgb = COR
            p = c.paragraphs[0] if first else c.add_paragraph(); fmt(p, 0, 2)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            self.picture(p, el["body"], width=min(el["body"]["w"], half - 6))
            for f in el["fontes"]:
                q = c.add_paragraph(); fmt(q, 0, 2); add_runs(q, f, size=8, color=CINZA)
        self.stats["pares_lado_a_lado"] += 1
        sp = self.doc.add_paragraph(); fmt(sp, 0, 2)

    # ------------------------------------------------------------ fluxo
    def render(self, model):
        els = model["elementos"]; i = 0
        RD.cabecalho_docx(self.doc, model["meta"], CINZA)
        while i < len(els):
            el = els[i]; t = el["t"]
            if t == "id":
                self.ident(el)
            elif t == "pagina":
                RD.pagina_inteira(self.doc, self.out, el, self.text_w); self.stats["imagens"] += 1
            elif t == "front":
                g = []
                while i < len(els) and els[i]["t"] == "front":
                    g.append(els[i]); i += 1
                self.front(g); continue
            elif t in ("h1", "h2", "h3", "h4"):
                self.h(int(t[1]), el)
            elif t == "p":
                self.para(el)
            elif t == "quote":
                self.quote(el)
            elif t == "bullet":
                self.bullet(el)
            elif t == "ref":
                self.ref(el)
            elif t == "fonte":
                self.fonte(el["runs"])
            elif t == "notas":
                self.notas(el)
            elif t == "siglas":
                self.siglas(el)
            elif t == "toc":
                self.toc(el)
            elif t == "fig":
                nxt = els[i + 1] if i + 1 < len(els) else None
                half = self.text_w / 2 - 6
                if nxt and nxt["t"] == "fig" and el["body"] and nxt["body"] and el["body"]["t"] == "img" == nxt["body"]["t"] \
                        and el["body"]["w"] <= half and nxt["body"]["w"] <= half:
                    self.fig_pair(el, nxt); i += 2; continue
                self.fig(el)
            i += 1
        return self.doc


def soffice_pdf(docx, outdir):
    # executavel por DOCREDUX_SOFFICE e perfil do LibreOffice reservado do pool por chamada (DT-5, story 2.8)
    return soffice.para_pdf(docx, outdir, timeout=900)


def find_toc_pages(pdf, h1s, h1_pt=15):
    import pymupdf
    res, start = {}, 0
    # fechado no fim: a 2a passada do LibreOffice regrava este mesmo PDF (no Windows, arquivo aberto nao se apaga)
    with pymupdf.open(pdf) as d:
        # ignora paginas ate o fim do proprio sumario: so conta spans do tamanho do H1
        for t in h1s:
            for pno in range(start, d.page_count):
                hit = False
                for b in d[pno].get_text("dict", flags=DICT)["blocks"]:
                    for ln in b.get("lines", []):
                        s = ln["spans"][0] if ln["spans"] else None
                        if s and abs(s["size"] - h1_pt) < 0.3:
                            tx = "".join(x["text"] for x in ln["spans"]).strip()
                            if tx and t.startswith(tx[:25]):
                                hit = True
                if hit:
                    res[t] = pno + 1; start = pno; break
    return res


def run(out, model, name):
    h1s = [e["text"] for e in model["elementos"] if e["t"] == "toc" for e in e["itens"]]
    pages = {}
    r = RR(out); doc = r.render(model)
    path = out / name
    doc.save(path)
    if h1s:  # R42: sumario regenerado com a paginacao nova (2 passadas)
        try:
            pdf = soffice_pdf(path, out)
            pages = find_toc_pages(pdf, h1s, r.h1_pt)
        except Exception as e:  # sem LibreOffice: sumario sem numeros
            print("aviso: sumario sem paginas:", e, file=sys.stderr)
        r = RR(out, pages); doc = r.render(model); doc.save(path)
    return r.stats, pages
