"""Mutacao (M12, story 2.17): defeitos injetados na saida real; mede a deteccao de cada checagem de verify/ordem.py.

Uso: python mutacao.py <saida da regressao> [json]   (DOCREDUX_FIXTURES aponta os exemplos)
  palavras: troca duas palavras diferentes (3+ letras) no mesmo paragrafo
  rotulos:  troca o numero de dois paragrafos que comecam com "Aula(s) N"
  imagens:  troca duas imagens de paginas diferentes de lugar
  tabelas:  troca o texto de duas celulas diferentes da mesma tabela
"""
import copy, json, os, random, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine"))
import pymupdf
from docx import Document
from docx.oxml.ns import qn
from docredux_engine.verify import ficha as VF, relatorio as VR, ordem
from docredux_engine.ficha import extract

PALAVRAS = ordem._Palavras  # classe real; o cache abaixo vale so para um exemplo
base = Path(sys.argv[1]); fx = Path(os.environ["DOCREDUX_FIXTURES"]); N = 100
res = {}
for d in sorted(base.iterdir()):
    if not d.is_dir():
        continue
    model = json.loads((d / "modelo.json").read_text(encoding="utf-8")); meta = model["meta"]
    pdf = sorted((fx / d.name / "original").glob("*.pdf"))[0]; docx = next(d.glob("*.docx"))
    fora = {**meta["removidas"], **{str(k): v for k, v in meta.get("paginas_imagem", {}).items()}}
    if meta.get("tipo") == "relatorio":
        perfil = extract.load_perfil(Path(extract.PERFIL_DIR) / f"{meta['perfil']}.json")
        orig, _ = VR.orig_lines(pdf, perfil, fora, meta["recorte"], meta.get("sumario_linhas", []))
        out, _ = VR.docx_partes(docx)
    else:
        figs = [(el["pagina"], pymupdf.Rect(el["bbox"])) for el in model["elementos"] if el.get("figura")]
        orig, _, _ = VF.original_textos(pdf, fora, figs); out, _ = VF.docx_partes(docx)
    doc = pymupdf.open(pdf); dx = Document(docx)
    pal = PALAVRAS(doc, fora); ordem._Palavras = lambda *a, _p=pal: _p  # cache entre mutacoes deste exemplo
    r = {}
    rnd = random.Random(7)
    # palavras
    cand = [i for i, t in enumerate(out) if len(re.findall(r"[^\W\d_]{3,}", t)) >= 4]
    det = tent = 0
    while tent < N and cand:
        i = rnd.choice(cand); ms = list(re.finditer(r"[^\W\d_]{3,}", out[i]))
        a, b = sorted(rnd.sample(ms, 2), key=lambda m: m.start())
        if a.group(0).lower() == b.group(0).lower():
            continue
        t = out[i]; o2 = list(out)
        o2[i] = t[:a.start()] + b.group(0) + t[a.end():b.start()] + a.group(0) + t[b.end():]
        tent += 1; det += ordem.palavras(orig, o2)["total"] > 0
    r["palavras"] = [det, tent]
    # rotulos
    idx = [(i, m) for i, t in enumerate(out) for m in [re.match(r"(?i)\s*aulas?\s+(\d+(?:\s+[ea]\s+\d+)?)", t)] if m]
    det = tent = 0
    for _ in range(N * 3):
        if tent >= N or len(idx) < 2:
            break
        (i, a), (j, b) = rnd.sample(idx, 2)
        if a.group(1) == b.group(1):
            continue
        o2 = list(out)
        o2[i] = out[i][:a.start(1)] + b.group(1) + out[i][a.end(1):]
        o2[j] = out[j][:b.start(1)] + a.group(1) + out[j][b.end(1):]
        tent += 1; det += ordem.rotulos(doc, fora, o2)["total"] > 0
    r["rotulos"] = [det, tent]
    # imagens
    loc = ordem._locais_imagens(model)
    pics = [e for e in dx.element.body.iter(ordem.PIC) if e.get("name") in loc]
    det = tent = 0
    for _ in range(N * 3):
        if tent >= N or len(pics) < 2:
            break
        a, b = rnd.sample(pics, 2)
        if loc[a.get("name")][0] == loc[b.get("name")][0]:
            continue
        na, nb = a.get("name"), b.get("name")
        a.set("name", nb); b.set("name", na)
        tent += 1; det += ordem.imagens(doc, model, dx)["total"] > 0
        a.set("name", na); b.set("name", nb)
    r["imagens"] = [det, tent]
    # tabelas
    tabs = [t for t in dx.tables if not any(True for _ in t._tbl.iter(ordem.PIC))]
    cels = []
    for ti, t in enumerate(tabs):
        vistos = []
        for row in t.rows:
            for x in row.cells:
                if x.text.strip() and not any(x._tc is v._tc for v in vistos):
                    vistos.append(x)
        cels += [(ti, c) for c in vistos]
    det = tent = 0
    for _ in range(N * 3):
        if tent >= N or len(cels) < 2:
            break
        ti, a = rnd.choice(cels)
        outras = [c for t2, c in cels if t2 == ti and c is not a and c.text.strip() != a.text.strip()]
        if not outras:
            continue
        b = rnd.choice(outras)
        ta, tb = copy.deepcopy(a._tc), copy.deepcopy(b._tc)
        pa, pb = a._tc.getparent(), b._tc.getparent()
        ia, ib = pa.index(a._tc), pb.index(b._tc)
        # troca so o conteudo (paragrafos), mantendo propriedades da celula
        pa_ps = [p for p in a._tc if p.tag == qn("w:p")]; pb_ps = [p for p in b._tc if p.tag == qn("w:p")]
        for p in pa_ps: a._tc.remove(p)
        for p in pb_ps: b._tc.remove(p)
        for p in pb_ps: a._tc.append(p)
        for p in pa_ps: b._tc.append(p)
        tent += 1; det += ordem.tabelas(doc, fora, dx)["total"] > 0
        for p in pb_ps: a._tc.remove(p)
        for p in pa_ps: b._tc.remove(p)
        for p in pa_ps: a._tc.append(p)
        for p in pb_ps: b._tc.append(p)
    r["tabelas"] = [det, tent]
    res[d.name[:2]] = {k: {"detectadas": v[0], "trocas": v[1], "pct": round(100 * v[0] / v[1], 1) if v[1] else None}
                       for k, v in r.items()}
    print(d.name[:2], res[d.name[:2]], flush=True)
    doc.close()
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
