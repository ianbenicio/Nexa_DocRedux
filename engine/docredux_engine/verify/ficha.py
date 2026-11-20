"""DocRedux - verificacao (passo 7): cobertura de texto + estimativa de paginas + metricas.

Uso: python -m docredux_engine.verify.ficha <pdf_original> <saida_dir>  (no pacote: `verificar()`)
Cobertura: toda palavra do original (exceto rodape/cabecalho corrido/capa) deve existir no DOCX.
Paginas: estimativa por altura acumulada (Calibri 10,5; A4; margem 1,5 cm). Confirmar no Word.
"""
import collections, json, math, re, sys
from pathlib import Path
import pymupdf
from docx import Document
from docx.oxml.ns import qn

from ..ficha.render import CFG, A4_W, A4_H  # noqa: E402
from . import numeros
from .. import gerado
from ..pdftexto import DICT  # noqa: E402

WORD = re.compile(r"[\wÀ-ÿ%]+", re.UNICODE)


def words(text):
    return [w.lower() for w in WORD.findall(text.replace("ᵒ", "º")) if not w.isdigit() or len(w) > 1]


MARGEM_NUM_PAGINA = 0.08  # numero sozinho no topo/rodape (8% da altura) e numero de pagina, nao conteudo


def original_textos(pdf, removed, figs):
    """Linhas do original que a saida deve conter. Devolve (textos, textos_numeros, palavras_em_figuras).

    textos: base da cobertura de palavras (sem linhas so de digitos, como sempre).
    textos_numeros: base da checagem numerica (M09): inclui linhas so de digitos (valores de alternativas,
    celulas), exceto o numero de pagina na faixa de topo/rodape.
    figs: [(pagina, Rect)] das figuras vetoriais. Texto dentro delas esta na imagem (R7), nao no DOCX."""
    textos, textos_num, in_fig = [], [], 0
    with pymupdf.open(pdf) as doc:
        for pno, p in enumerate(doc):
            if str(pno + 1) in removed:
                continue
            fr = [r for pg, r in figs if pg == pno + 1]
            h = p.rect.height
            for b in p.get_text("dict", flags=DICT)["blocks"]:
                for ln in b.get("lines", []):
                    if abs(ln["dir"][1]) > 0.1:
                        continue
                    t = "".join(s["text"] for s in ln["spans"])
                    if re.search(r"FICHA DO ESTUDANTE|ANO\s+·\s+PERCURSO", t):
                        continue
                    lb = pymupdf.Rect(ln["bbox"])
                    if any(r.contains(pymupdf.Point((lb.x0 + lb.x1) / 2, (lb.y0 + lb.y1) / 2)) for r in fr):
                        in_fig += len(words(t)); continue
                    t = re.sub(r"\[\d\]", " ", t)  # glifo de checkbox -> vira ☐
                    if t.strip().isdigit():
                        if MARGEM_NUM_PAGINA * h < (lb.y0 + lb.y1) / 2 < (1 - MARGEM_NUM_PAGINA) * h:
                            textos_num.append(t)
                        continue
                    textos.append(t)
                    textos_num.append(t)
    return textos, textos_num, in_fig


def original_words(pdf, removed, figs):
    """figs: [(pagina, Rect)] das figuras vetoriais. Texto dentro delas esta na imagem (R7), nao no DOCX."""
    textos, _, in_fig = original_textos(pdf, removed, figs)
    c = collections.Counter()
    for t in textos:
        c.update(words(t))
    return c, in_fig


def docx_partes(path):
    """(textos, gerados): texto de cada paragrafo fisico do DOCX, uma vez, inclusive em celulas
    mescladas/aninhadas. O texto criado pelo motor (A5, `gerado.py`) vai para `gerados`."""
    d = Document(path); out, ger = [], []
    for p in d.element.body.iter(qn("w:p")):
        texto, g = [], []
        for r in p.iter(qn("w:r")):
            alvo = g if gerado.eh_gerado(r) else texto
            for node in r.iter():
                if node.tag == qn("w:t"):
                    alvo.append(node.text or "")
                elif node.tag in (qn("w:br"), qn("w:tab"), qn("w:cr")):
                    alvo.append(" ")
        t = "".join(texto)
        if gerado.LINHA_ID.match(t):  # linha de identificacao sem marca
            g, t = [t], ""
        out.append(t)
        if g:
            ger.append("".join(g))
    return out, ger


def docx_textos(path, incluir_gerado=False):
    textos, ger = docx_partes(path)
    return textos + ger if incluir_gerado else textos


def docx_words(path, incluir_gerado=False):
    c = collections.Counter()
    for t in docx_textos(path, incluir_gerado):
        c.update(words(t))
    return c


def estimate_pages(model):
    usable_h = A4_H - 2 * CFG["margem_cm"] / 2.54 * 72
    text_w = A4_W - 2 * CFG["margem_cm"] / 2.54 * 72
    lh = CFG["tam"] * 1.22
    cpl = text_w / (CFG["tam"] * 0.47)

    def tl(text, indent_pt=0, size=CFG["tam"]):
        return max(1, math.ceil(len(text) / ((text_w - indent_pt) / (size * 0.47))))

    h = 0
    els = model["elementos"]
    for i, el in enumerate(els):
        t = el["t"]
        if t == "id":
            h += 40
        elif t == "h1":
            h += CFG["h1_espaco_antes"] + tl(el["text"], 0, 13) * 16 + 4
        elif t == "h2":
            h += 8 + 14
        elif t in ("q", "sub"):
            h += 4 + tl(el["text"], 17 if t == "q" else 34) * lh
        elif t == "opt":
            if i and els[i - 1]["t"] == "opt":
                continue  # grupo ja contado
            g = []
            for e in els[i:]:
                if e["t"] != "opt":
                    break
                g.append(len(e["text"]) + 6 + len(CFG["sep_opcoes"]))
            if max(g) <= CFG["max_chars_fluxo"] + 6 + len(CFG["sep_opcoes"]):
                h += 3 + tl("x" * sum(g), 34) * lh  # fluxo horizontal
            else:
                h += sum(tl("x" * n, 34) * lh + 1 for n in g)
        elif t == "lines":
            h += max(1, math.ceil(el["n"] * CFG["fator_linhas"])) * CFG["linha_resposta_pt"]
        elif t == "img":
            w, ih = el["w"], el["h"]
            if w > text_w:
                ih *= text_w / w
            ih *= el.get("encaixe", 1)  # R52
            side = sum(tl(s["text"], w) * lh for s in el["side"])
            h += max(ih, side) + 6
        elif t == "table":
            h += sum(max(20, max(tl(c, text_w * 0.58) for c in r) * 11) for r in el["rows"])
        elif t == "contas":  # R51
            h += len(el["rows"]) * CFG["conta_linha_pt"] + 4
        elif t == "box":
            h += el["h"] * CFG["quadro_fator"] + 4
        elif t == "source":
            h += tl(el["text"], 17, 7.5) * 9.5 + 3
        elif t == "pagina":  # manter_capa_elementos: imagem de pagina inteira
            h += usable_h
        else:
            h += 4 + tl(el["text"], 34 if t in ("quote", "bullet") else 0) * lh
    return h / usable_h


def verificar(pdf, out, model=None, perfil=None, *, docx_path=None, pdf_path=None):
    """Cobertura + metricas -> out/verificacao.json. Devolve o relatorio (dict)."""
    pdf, out = Path(pdf), Path(out)
    if model is None:
        model = json.loads((out / "modelo.json").read_text(encoding="utf-8"))
    if model["meta"].get("tipo") == "relatorio":  # R32/R47: familia de relatorio
        from . import relatorio as verify_relatorio
        return verify_relatorio.run(pdf, out, model, perfil, docx_path=docx_path, pdf_path=pdf_path)
    from ..ficha.render import nome_docx
    docx = Path(docx_path) if docx_path is not None else out / nome_docx(model)
    figs = [(el["pagina"], pymupdf.Rect(el["bbox"])) for el in model["elementos"] if el.get("figura")]
    # paginas mantidas como imagem (manter_capa_elementos): texto na imagem, como nas figuras (R7)
    fora = {**model["meta"]["removidas"], **{str(k): v for k, v in model["meta"].get("paginas_imagem", {}).items()}}
    textos, textos_num, in_fig = original_textos(pdf, fora, figs)
    o = collections.Counter()
    for t in textos:
        o.update(words(t))
    saida_textos, gerados = docx_partes(docx)
    n = collections.Counter()
    for t in saida_textos:
        n.update(words(t))
    missing = o - n
    cov = 1 - sum(missing.values()) / max(1, sum(o.values()))
    pages_est = estimate_pages(model)
    st = model["meta"]["stats"]
    rep = {
        "paginas_original": model["meta"]["paginas"],
        "paginas_estimadas_docx": round(pages_est, 1),
        "reducao_paginas_pct": round((1 - math.ceil(pages_est) / model["meta"]["paginas"]) * 100),
        "palavras_original": sum(o.values()),
        "palavras_em_figuras_(na_imagem)": in_fig,
        "figuras_vetoriais": len(figs),
        "cobertura_texto_pct": round(cov * 100, 2),
        "palavras_faltando": dict(missing.most_common(30)),
        "numeros": numeros.comparar(numeros.contar(textos_num), numeros.contar(saida_textos)),
        "texto_gerado_fora": gerados,  # A5: identificacao do motor, fora da comparacao
        "linhas_resposta": {"original": st["linhas_resposta_orig"],
                            "saida": sum(max(1, math.ceil(e["n"] * CFG["fator_linhas"])) for e in model["elementos"] if e["t"] == "lines")},
        "imagens": st["imagens"], "tabelas": st["tabelas"], "quadros": st["quadros"],
        "paginas_removidas": model["meta"]["removidas"],
        "tamanho_kb": {"original": round(pdf.stat().st_size / 1024), "docx": round(docx.stat().st_size / 1024)},
    }
    (out / "verificacao.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def main():
    print(json.dumps(verificar(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
