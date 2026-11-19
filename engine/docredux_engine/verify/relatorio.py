"""DocRedux - verificacao do tipo `relatorio` (R35, R47): cobertura de texto + checagens estruturais.

Chamado por verify/ficha.verificar() quando meta.tipo == "relatorio".
Cobertura: toda palavra do original (fora de paginas removidas, faixa de cabecalho/rodape, sumario estatico e
marcadores sobrescritos) deve existir no DOCX. O original e dehifenizado com a mesma regra da extracao (R35).
Estrutura: legendas, 'Fonte:', notas, H1 e referencias contados no PDF e no modelo/DOCX.
"""
import collections, json, math, re
from pathlib import Path
import pymupdf
from docx import Document
from docx.oxml.ns import qn

from ..ficha import extract
from ..relatorio import extract as ER
from . import numeros

WORD = re.compile(r"[\wÀ-ÿ%]+", re.UNICODE)


def words(text):
    return [w.lower() for w in WORD.findall(text.replace("ᵒ", "º")) if not w.isdigit() or len(w) > 1]


def orig_lines(pdf, perfil, removed, recorte, sumario):
    """Texto de cada linha do original, sem marcadores sobrescritos nem numero de nota."""
    doc = pymupdf.open(pdf)
    skip = collections.Counter(t for _, t in sumario)
    out, info = [], collections.Counter()
    thr = perfil["nota_rodape"]["size_numero_max"]
    legenda = re.compile(perfil["legenda_regex"]); fonte = re.compile(r"^Fonte\b")
    for pno in range(recorte[0] - 1, recorte[1]):
        if str(pno + 1) in removed:
            continue
        lines, _ = ER.page_lines(doc[pno], perfil)
        for l in lines:
            if skip[l["text"]] > 0:
                skip[l["text"]] -= 1; continue
            runs = [r for r in l["runs"] if not (r[1] & ER.SUP)]
            t = ER.runs_text(runs)
            nota = l["first_size"] <= thr and ER.RE_NUM_NOTA.match(l["first_text"]) and l["y"] > doc[pno].rect.height * 0.55
            if nota:
                info["notas"] += 1 if len(l["text"]) > len(l["first_text"]) + 2 else 0
                t = t[len(l["first_text"]):].lstrip() if t.startswith(l["first_text"]) else t
            else:
                if legenda.match(l["text"]) and ER.capish(l):
                    info["legendas"] += 1
                if fonte.match(l["text"]) and l["size"] <= 10.6:
                    info["fontes"] += 1
                if re.match(perfil["capitulo_regex"], l["text"]) and l["size"] >= perfil["h1"]["size_min"]:
                    info["h1_capitulos"] += 1
            out.append((t, l["x0"], pno))
    # dehifenizacao (R35), ciente de colunas: a continuacao e a proxima linha da mesma coluna (|dx|<8; a vizinha
    # imediata aceita |dx|<20 por causa do recuo de 1a linha)
    res, used = [], set()
    for i, (t, x0, pn) in enumerate(out):
        if i in used:
            continue
        j = i
        while t.endswith("-"):
            k = None
            for q in range(i + 1, min(len(out), i + 9)):
                if q in used or out[q][2] != pn:
                    continue
                dx = abs(out[q][1] - x0)
                if (q == i + 1 and dx < 20) or dx < 8:
                    k = q; break
            if k is None and i + 1 < len(out) and i + 1 not in used:
                k = i + 1  # sem coluna equivalente: vizinha imediata (recuos diferentes)
            if k is None:
                break
            a, sep, b = ER.join_str(t, out[k][0]); t = a + sep + b; used.add(k); i = k; x0 = out[k][1]
        res.append(t)
    return res, info


def orig_counter(lines):
    c = collections.Counter()
    for t in lines:
        c.update(words(t))
    return c


def docx_counter(path):
    c = collections.Counter()
    for t in docx_textos(path):
        c.update(words(t))
    return c


def docx_textos(path):
    """Texto de cada paragrafo do DOCX como a cobertura o le (sem sobrescritos nem numero de nota)."""
    d = Document(path); out = []

    def runs_words(p):
        t = []
        first = True
        for r in p.runs:
            if r.font.superscript:
                continue
            if first and re.match(r"^\d+\.\t$", r.text) and r.bold:  # numero da nota (formato do Notas)
                first = False; continue
            first = False
            t.append(r.text)
        return "".join(t)
    for p in d.paragraphs:
        out.append(runs_words(p))
    for t in d.tables:
        for tc in t._tbl.iter(qn("w:tc")):
            for p in tc.iter(qn("w:p")):
                txt = []
                for r in p.iter(qn("w:r")):
                    va = r.find(qn("w:rPr") + "/" + qn("w:vertAlign"))
                    if va is not None and va.get(qn("w:val")) == "superscript":
                        continue
                    txt.append("".join(x.text or "" for x in r.iter(qn("w:t"))))
                out.append("".join(txt))
    return out


def run(pdf, out, model, perfil=None, *, docx_path=None, pdf_path=None):
    meta = model["meta"]
    if perfil is None:
        perfil = extract.load_perfil(Path(extract.PERFIL_DIR) / f"{meta['perfil']}.json")
    from ..ficha.render import nome_docx
    out = Path(out)
    docx = Path(docx_path) if docx_path is not None else out / nome_docx(model)
    # paginas mantidas como imagem (manter_capa_elementos): texto na imagem, fora da cobranca
    fora = {**meta["removidas"], **{str(k): v for k, v in meta.get("paginas_imagem", {}).items()}}
    lines, info = orig_lines(pdf, perfil, fora, meta["recorte"], meta.get("sumario_linhas", []))
    saida_textos = docx_textos(docx)
    o = orig_counter(lines); n = orig_counter(saida_textos)
    missing = o - n; extra = n - o
    cov = 1 - sum(missing.values()) / max(1, sum(o.values()))
    els = model["elementos"]
    figs = [e for e in els if e["t"] == "fig"]
    fontes_m = sum(1 for e in figs for f in e["fontes"] if ER.runs_text(f).startswith("Fonte")) + \
        sum(1 for e in els if e["t"] == "fonte" and ER.runs_text(e["runs"]).startswith("Fonte"))
    notas_m = sum(len(e["itens"]) for e in els if e["t"] == "notas")
    refs_m = sum(1 for e in els if e["t"] == "ref")
    caps_m = sum(1 for e in figs if e["caption"])
    h1_cap_m = sum(1 for e in els if e["t"] == "h1" and re.match(perfil["capitulo_regex"], e["text"]))
    h1_m = sum(1 for e in els if e["t"] == "h1")
    est = {
        "h1_capitulos": {"pdf": info["h1_capitulos"], "modelo": h1_cap_m},
        "h1_total_modelo": h1_m,
        "legendas": {"pdf": info["legendas"], "modelo": caps_m},
        "fontes": {"pdf": info["fontes"], "modelo": fontes_m},
        "notas": {"pdf": info["notas"], "modelo": notas_m},
        "referencias_modelo": refs_m,
        "figuras_sem_legenda": sum(1 for e in figs if not e["caption"]),
        "legendas_sem_visual": sum(1 for e in figs if e["caption"] and not e["body"]),
    }
    ok = all(est[k]["pdf"] == est[k]["modelo"] for k in ("h1_capitulos", "legendas", "fontes", "notas")) \
        and est["figuras_sem_legenda"] == 0 and est["legendas_sem_visual"] == 0
    pdf_out = Path(pdf_path) if pdf_path is not None else docx.with_suffix(".pdf")
    if not pdf_out.exists():
        pdf_out = None
    if pdf_out:
        with pymupdf.open(pdf_out) as saida:
            pages = saida.page_count
    else:
        pages = None
    rep = {
        "paginas_original": meta["paginas"], "paginas_docx_pdf": pages,
        "palavras_original": sum(o.values()), "cobertura_texto_pct": round(cov * 100, 2),
        "palavras_faltando": dict(missing.most_common(40)), "palavras_a_mais": dict(extra.most_common(15)),
        "numeros": numeros.comparar(numeros.contar(lines), numeros.contar(saida_textos)),
        "estrutura": est, "estrutura_ok": ok,
        "imagens": meta["stats"].get("imagens", 0), "tabelas": meta["stats"].get("tabelas", 0),
        "paginas_removidas": meta["removidas"],
        "tamanho_kb": {"original": round(Path(pdf).stat().st_size / 1024), "docx": round(docx.stat().st_size / 1024),
                       "pdf": round(pdf_out.stat().st_size / 1024) if pdf_out else None},
    }
    (out / "verificacao.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep
