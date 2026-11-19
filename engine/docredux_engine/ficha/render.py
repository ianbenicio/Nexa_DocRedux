"""DocRedux - renderizador (passo 6): modelo.json -> DOCX compacto.

Uso: python -m docredux_engine.ficha.render <saida_dir> [--linhas-fator 0.5]  (no pacote: `renderizar()`)
Regras aplicadas aqui: R1 (caixa de identificacao), R2/R7 (tamanho de imagem), R3 (texto ao lado),
R4 (alternativas lado a lado), R6 (espacamento), R9 (hierarquia), P1 (linhas de resposta reduzidas).
"""
import json, math, sys
from pathlib import Path
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_LINE_SPACING, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, Cm, RGBColor

CFG = {
    "fonte": "Carlito",  # Q13/DT-11: metrica identica a Calibri e existe no Linux (era "Calibri")
    "tam": 10.5, "cor": RGBColor(0xCF, 0x45, 0x62), "cinza": RGBColor(0x66, 0x66, 0x66),
    "margem_cm": 1.5, "linha_resposta_pt": 18, "fator_linhas": 0.5, "min_linhas": 1,
    "img_max_pct_pagina": 50, "img_alvo_pct": 25,  # R2
    "max_chars_fluxo": 90, "sep_opcoes": "     ",  # R4: opção maior que isso vai em coluna
    "h1_espaco_antes": 22,
    "quadro_fator": 0.6,
}
A4_W, A4_H = 595.3, 841.9


def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    sh = OxmlElement("w:shd"); sh.set(qn("w:val"), "clear"); sh.set(qn("w:color"), "auto"); sh.set(qn("w:fill"), hexcolor)
    tcPr.append(sh)


def borders(par, sides=("bottom", "between"), color="999999", sz=4):
    pPr = par._p.get_or_add_pPr()
    b = OxmlElement("w:pBdr")
    for s in sides:
        e = OxmlElement(f"w:{s}"); e.set(qn("w:val"), "single"); e.set(qn("w:sz"), str(sz))
        e.set(qn("w:space"), "1"); e.set(qn("w:color"), color); b.append(e)
    pPr.append(b)


def no_table_borders(tbl):
    tblPr = tbl._tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for s in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{s}"); e.set(qn("w:val"), "nil"); b.append(e)
    tblPr.append(b)


def row_height(row, pt, exact=False):
    trPr = row._tr.get_or_add_trPr()
    h = OxmlElement("w:trHeight"); h.set(qn("w:val"), str(int(pt * 20))); h.set(qn("w:hRule"), "exact" if exact else "atLeast")
    trPr.append(h)


def fmt(par, before=0, after=2, left_cm=0, hanging_cm=0, keep=False):
    pf = par.paragraph_format
    pf.space_before, pf.space_after = Pt(before), Pt(after)
    pf.line_spacing = 1.0
    if left_cm:
        pf.left_indent = Cm(left_cm)
    if hanging_cm:
        pf.first_line_indent = Cm(-hanging_cm)
    if keep:
        pf.keep_with_next = True
    return par


class R:
    def __init__(self, out):
        self.out = out
        self.doc = Document()
        st = self.doc.styles["Normal"]
        st.font.name = CFG["fonte"]; st.font.size = Pt(CFG["tam"])
        st.element.rPr.rFonts.set(qn("w:eastAsia"), CFG["fonte"])
        st.paragraph_format.space_after = Pt(2); st.paragraph_format.line_spacing = 1.0
        for name, size, color in (("Heading 1", 13, CFG["cor"]), ("Heading 2", 11, CFG["cor"])):
            h = self.doc.styles[name]
            h.font.name = CFG["fonte"]; h.font.size = Pt(size); h.font.bold = True; h.font.color.rgb = color
            h.element.rPr.rFonts.set(qn("w:asciiTheme"), "") if False else None
        s = self.doc.sections[0]
        s.page_width, s.page_height = Pt(A4_W), Pt(A4_H)
        m = Cm(CFG["margem_cm"])
        s.left_margin = s.right_margin = s.top_margin = s.bottom_margin = m
        self.text_w = A4_W - 2 * m.pt
        self.stats = {"linhas_saida": 0, "imagens": 0}
        self.keep_intro = False

    # ---------------- elementos
    def ident(self, el):
        t = self.doc.add_table(rows=1, cols=1); t.alignment = WD_TABLE_ALIGNMENT.CENTER
        c = t.rows[0].cells[0]; shade(c, "FBE3E8")
        p = c.paragraphs[0]; fmt(p, 2, 0)
        r = p.add_run(el["text"].replace("5ᵒ", "5º")); r.bold = True; r.font.size = Pt(11); r.font.color.rgb = CFG["cor"]
        p2 = c.add_paragraph(); fmt(p2, 0, 2)
        r2 = p2.add_run(f"Versão compacta gerada a partir de: {el['arquivo']}"); r2.font.size = Pt(8); r2.font.color.rgb = CFG["cinza"]

    def h1(self, el):
        p = self.doc.add_heading(level=1); fmt(p, CFG["h1_espaco_antes"], 4, keep=True)
        p.add_run(f"{el['tag'].upper()} · {el['text']}" if el["tag"] else el["text"])
        borders(p, ("bottom",), "CF4562", 8)

    def h2(self, el):
        p = self.doc.add_heading(level=2); fmt(p, 6, 2, keep=True); p.add_run(el["text"])

    def h3(self, el):  # subtitulo ("Momento 2 – ...")
        p = self.doc.add_paragraph(); fmt(p, 5, 2, keep=True)
        r = p.add_run(el["text"]); r.bold = True; r.italic = True; r.font.size = Pt(10.5); r.font.color.rgb = CFG["cor"]

    def numbered(self, num, text, left, hang, before, keep=True):
        p = self.doc.add_paragraph(); fmt(p, before, 1, left, hang, keep=keep)
        r = p.add_run(f"{num}.\t"); r.bold = True; r.font.color.rgb = CFG["cor"]
        p.add_run(text)
        p.paragraph_format.tab_stops.add_tab_stop(Cm(left))
        return p

    def q(self, el):
        self.numbered(el["num"], el["text"], 0.6, 0.6, 5)

    def sub(self, el):
        # alinea nao prende o proximo bloco: senao 'a.' -> 'b.' -> titulo -> 'a.' encadeia o documento inteiro
        self.numbered(el["num"], el["text"], 1.2, 0.6, 2, keep=False)

    def opts(self, group, left_cm):
        items = [("☐ " + (o["label"] + ") " if o["label"] else "") + o["text"]) for o in group]
        if max(len(i) for i in items) <= CFG["max_chars_fluxo"]:
            # R4 v2: fluxo horizontal. Espaço rígido dentro da opção (não quebra no meio);
            # espaço comum entre opções (Word quebra ali quando não cabe na linha).
            p = self.doc.add_paragraph(); fmt(p, 1, 2, left_cm)
            p.add_run(CFG["sep_opcoes"].join(i.replace(" ", " ") for i in items))
        else:
            for it in items:
                p = self.doc.add_paragraph(); fmt(p, 0, 1, left_cm + 0.5, 0.5); p.add_run(it)

    def lines(self, el, left_cm):
        n = max(CFG["min_linhas"], math.ceil(el["n"] * CFG["fator_linhas"]))
        self.stats["linhas_saida"] += n
        for _ in range(n):
            p = self.doc.add_paragraph(); fmt(p, 0, 0, left_cm)
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            p.paragraph_format.line_spacing = Pt(CFG["linha_resposta_pt"])
            borders(p)

    def text(self, el):
        t = el["t"]
        p = self.doc.add_paragraph()
        if t == "quote":
            fmt(p, 2, 2, 1.2); borders(p, ("left",), "CF4562", 12)
            p.add_run(el["text"]).font.size = Pt(10)
        elif t == "source":
            fmt(p, 0, 3, 0.6); r = p.add_run(el["text"]); r.font.size = Pt(7.5); r.font.color.rgb = CFG["cinza"]
        elif t == "bullet":
            fmt(p, 0, 1, 1.0, 0.4); r = p.add_run("•\t"); r.font.color.rgb = CFG["cor"]; p.add_run(el["text"])
            p.paragraph_format.tab_stops.add_tab_stop(Cm(1.0))
        else:
            fmt(p, 3, 2); p.add_run(el["text"])
        # R17: texto de abertura apos titulo, ou que anuncia o que vem (termina em ":"), nao fica orfao
        # texto que vem antes de outro titulo nao prende nada: senao a cadeia titulo -> texto -> titulo
        # encadeia o documento inteiro (bug achado no ex. 03)
        if (self.keep_intro or el["text"].rstrip().endswith(":")) and not getattr(self, "next_is_heading", False):
            p.paragraph_format.keep_with_next = True
        self.keep_intro = False  # so o 1o paragrafo apos o titulo; senao a cadeia prende o documento inteiro
        return p

    def img_size(self, el):
        w, h = el["w"], el["h"]
        if el["pct"] > CFG["img_max_pct_pagina"] and not el.get("manter_tamanho"):  # R2 (R7 marca manter_tamanho)
            k = math.sqrt(CFG["img_alvo_pct"] / el["pct"]); w, h = w * k, h * k
        if w > self.text_w:
            h, w = h * self.text_w / w, self.text_w
        return w, h

    def imgs(self, group):
        if len(group) == 1 and group[0]["side"]:  # R3: texto ao lado
            el = group[0]; w, h = self.img_size(el)
            t = self.doc.add_table(rows=1, cols=2); no_table_borders(t)
            c0, c1 = t.rows[0].cells
            c0.width, c1.width = Pt(w + 8), Pt(self.text_w - w - 8)
            fmt(c0.paragraphs[0], 0, 0).add_run().add_picture(str(self.out / "img" / el["file"]), width=Pt(w))
            first = True
            for s in el["side"]:
                p = c1.paragraphs[0] if first else c1.add_paragraph(); first = False
                fmt(p, 0, 2)
                r = p.add_run(s["text"])
                if s["t"] == "source":
                    r.font.size = Pt(7.5); r.font.color.rgb = CFG["cinza"]
            self.stats["imagens"] += 1
            return
        p = self.doc.add_paragraph(); fmt(p, 2, 2, 0.6)
        for k, el in enumerate(group):  # imagens consecutivas lado a lado
            w, h = self.img_size(el)
            if k:
                p.add_run("      ")
            p.add_run().add_picture(str(self.out / "img" / el["file"]), width=Pt(w))
            self.stats["imagens"] += 1
            for s in el["side"]:
                self.text(s)

    def table(self, el):
        rows = el["rows"]; nc = len(rows[0])
        t = self.doc.add_table(rows=len(rows), cols=nc); t.style = "Table Grid"
        widths = [0.42, 0.08, 0.08, 0.42] if nc == 4 else [1 / nc] * nc
        # 1a linha so e cabecalho se for texto; grade numerada (1, 2, 3...) nao tem cabecalho
        has_header = not rows[0][0].strip().isdigit()
        for i, r in enumerate(rows):
            row_height(t.rows[i], 20)
            for j, txt in enumerate(r):
                c = t.rows[i].cells[j]; c.width = Pt(self.text_w * widths[j])
                p = fmt(c.paragraphs[0], 1, 1); run = p.add_run(txt); run.font.size = Pt(9)
                if i == 0 and has_header:
                    shade(c, "CF4562"); run.bold = True; run.font.color.rgb = RGBColor(255, 255, 255)

    def box(self, el):
        t = self.doc.add_table(rows=1, cols=1); t.style = "Table Grid"
        row_height(t.rows[0], el["h"] * CFG["quadro_fator"], exact=True)

    # ---------------- fluxo
    def render(self, model):
        els = model["elementos"]; i = 0; ctx_left = 0.6
        while i < len(els):
            el = els[i]; t = el["t"]
            self.next_is_heading = i + 1 < len(els) and els[i + 1]["t"] in ("h1", "h2", "h3")
            # R17: titulo nunca fica sozinho no fim da pagina. Titulo + texto introdutorio
            # (p/citacao/fonte) ficam presos ao primeiro conteudo seguinte (questao, opcao, imagem...).
            if t in ("h1", "h2", "h3"):
                self.keep_intro = True
            elif t not in ("p", "quote", "source"):
                self.keep_intro = False
            if t == "id":
                self.ident(el)
            elif t == "h1":
                self.h1(el); ctx_left = 0.6
            elif t == "h2":
                self.h2(el)
            elif t == "h3":
                self.h3(el)
            elif t == "q":
                self.q(el); ctx_left = 0.6
            elif t == "sub":
                self.sub(el); ctx_left = 1.2
            elif t == "opt":
                g = []
                while i < len(els) and els[i]["t"] == "opt":
                    g.append(els[i]); i += 1
                self.opts(g, ctx_left); continue
            elif t == "img":
                g = []
                while i < len(els) and els[i]["t"] == "img" and not els[i]["side"] and (not g or not g[-1]["side"]):
                    g.append(els[i]); i += 1
                if not g:
                    g = [el]; i += 1
                self.imgs(g); continue
            elif t == "lines":
                self.lines(el, ctx_left)
            elif t == "table":
                self.table(el)
            elif t == "box":
                self.box(el)
            else:
                self.text(el)
            i += 1
        return self.doc


def nome_docx(model):
    return Path(model["meta"]["origem"]).stem.replace("_ORIGINAL", "") + "_COMPACTO.docx"


def renderizar(out, model=None):
    """out/modelo.json (ou `model`) -> out/<origem>_COMPACTO.docx. Devolve as estatisticas do render.
    Relatorio: 2 passadas pelo LibreOffice para o sumario (R42)."""
    out = Path(out)
    if model is None:
        model = json.loads((out / "modelo.json").read_text(encoding="utf-8"))
    name = nome_docx(model)
    if model["meta"].get("tipo") == "relatorio":  # R32: familia de relatorio
        from ..relatorio import render as render_relatorio
        st, pg = render_relatorio.run(out, model, name)
        return {"arquivo": name, **st, "toc_paginas": pg}
    r = R(out)
    doc = r.render(model)
    doc.save(out / name)
    return {"arquivo": name, **r.stats, "cfg": {k: v for k, v in CFG.items() if not isinstance(v, RGBColor)}}


def main():
    out = Path(sys.argv[1])
    if "--linhas-fator" in sys.argv:
        CFG["fator_linhas"] = float(sys.argv[sys.argv.index("--linhas-fator") + 1])
    print(json.dumps(renderizar(out), ensure_ascii=False))


if __name__ == "__main__":
    main()
