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

WORD = re.compile(r"[\wÀ-ÿ%]+", re.UNICODE)


def words(text):
    return [w.lower() for w in WORD.findall(text.replace("ᵒ", "º")) if not w.isdigit() or len(w) > 1]


def original_words(pdf, removed, figs):
    """figs: [(pagina, Rect)] das figuras vetoriais. Texto dentro delas esta na imagem (R7), nao no DOCX."""
    doc = pymupdf.open(pdf); c = collections.Counter(); in_fig = 0
    for pno, p in enumerate(doc):
        if str(pno + 1) in removed:
            continue
        fr = [r for pg, r in figs if pg == pno + 1]
        for b in p.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                if abs(ln["dir"][1]) > 0.1:
                    continue
                t = "".join(s["text"] for s in ln["spans"])
                if re.search(r"FICHA DO ESTUDANTE|ANO\s+·\s+PERCURSO", t) or t.strip().isdigit():
                    continue
                lb = pymupdf.Rect(ln["bbox"])
                if any(r.contains(pymupdf.Point((lb.x0 + lb.x1) / 2, (lb.y0 + lb.y1) / 2)) for r in fr):
                    in_fig += len(words(t)); continue
                t = re.sub(r"\[\d\]", " ", t)  # glifo de checkbox -> vira ☐
                c.update(words(t))
    return c, in_fig


def docx_words(path):
    d = Document(path); c = collections.Counter()
    for p in d.paragraphs:
        c.update(words(p.text))
    for t in d.tables:  # celulas fisicas (tc), sem duplicar mescladas
        for tc in t._tbl.iter(qn("w:tc")):
            c.update(words("".join(x.text or "" for x in tc.iter(qn("w:t")))))
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
            side = sum(tl(s["text"], w) * lh for s in el["side"])
            h += max(ih, side) + 6
        elif t == "table":
            h += sum(max(20, max(tl(c, text_w * 0.58) for c in r) * 11) for r in el["rows"])
        elif t == "box":
            h += el["h"] * CFG["quadro_fator"] + 4
        elif t == "source":
            h += tl(el["text"], 17, 7.5) * 9.5 + 3
        elif t == "pagina":  # manter_capa_elementos: imagem de pagina inteira
            h += usable_h
        else:
            h += 4 + tl(el["text"], 34 if t in ("quote", "bullet") else 0) * lh
    return h / usable_h


def verificar(pdf, out, model=None, perfil=None):
    """Cobertura + metricas -> out/verificacao.json. Devolve o relatorio (dict)."""
    pdf, out = Path(pdf), Path(out)
    if model is None:
        model = json.loads((out / "modelo.json").read_text(encoding="utf-8"))
    if model["meta"].get("tipo") == "relatorio":  # R32/R47: familia de relatorio
        from . import relatorio as verify_relatorio
        return verify_relatorio.run(pdf, out, model, perfil)
    docx = next(out.glob("*_COMPACTO.docx"))
    figs = [(el["pagina"], pymupdf.Rect(el["bbox"])) for el in model["elementos"] if el.get("figura")]
    # paginas mantidas como imagem (manter_capa_elementos): texto na imagem, como nas figuras (R7)
    fora = {**model["meta"]["removidas"], **{str(k): v for k, v in model["meta"].get("paginas_imagem", {}).items()}}
    o, in_fig = original_words(pdf, fora, figs)
    n = docx_words(docx)
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
