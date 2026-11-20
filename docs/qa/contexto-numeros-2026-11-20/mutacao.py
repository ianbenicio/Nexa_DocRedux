"""Mutacao: troca dois numeros diferentes entre paragrafos da saida real; mede deteccao do contexto (M12)."""
import json, os, random, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine"))  # uso: python mutacao.py <saida da regressao>
import pymupdf
from docredux_engine.verify import ficha as VF, relatorio as VR, numeros
from docredux_engine.ficha import extract
base = Path(sys.argv[1]); fx = Path(os.environ["DOCREDUX_FIXTURES"])
NUMW = re.compile(r"(?<![\w.,/-])\d+(?:,\d+)?(?![\w.,/%º-])")  # numero simples, isolado
res = {}
for d in sorted(base.iterdir()):
    if not d.is_dir(): continue
    model = json.loads((d/"modelo.json").read_text(encoding="utf-8")); meta = model["meta"]
    pdf = sorted((fx/d.name/"original").glob("*.pdf"))[0]; docx = next(d.glob("*.docx"))
    fora = {**meta["removidas"], **{str(k): v for k, v in meta.get("paginas_imagem", {}).items()}}
    if meta.get("tipo") == "relatorio":
        perfil = extract.load_perfil(Path(extract.PERFIL_DIR)/f"{meta['perfil']}.json")
        orig, _ = VR.orig_lines(pdf, perfil, fora, meta["recorte"], meta.get("sumario_linhas", []))
        out, _ = VR.docx_partes(docx)
    else:
        figs = [(el["pagina"], pymupdf.Rect(el["bbox"])) for el in model["elementos"] if el.get("figura")]
        _, orig, _ = VF.original_textos(pdf, fora, figs); out, _ = VF.docx_partes(docx)
    so = numeros.sequencia(orig)
    occ = [(pi, m.start(), m.end(), m.group(0)) for pi, t in enumerate(out) for m in NUMW.finditer(t)]
    rnd = random.Random(7); N = 300 if len(occ) < 2000 else 150; det = glob = tent = 0
    for _ in range(N * 5):
        if tent >= N: break
        a, b = rnd.sample(occ, 2)
        if a[0] == b[0] or a[3] == b[3]: continue
        o2 = list(out)
        for (pi, s, e, v), novo in ((a, b[3]), (b, a[3])):
            o2[pi] = o2[pi][:s] + novo + o2[pi][e:] if pi != a[0] or (pi, s) == (a[0], a[1]) else o2[pi]
        tent += 1
        glob += not numeros.comparar(numeros.contar(orig), numeros.contar(o2))["ok"]
        hit = numeros.contexto(so, numeros.sequencia(o2))["total"] > 0; det += hit
        if not hit and tent <= 25: print("   MISS", repr(out[a[0]][max(0,a[1]-25):a[2]+25]), "<->", repr(out[b[0]][max(0,b[1]-25):b[2]+25]))
    res[d.name[:2]] = {"trocas": tent, "contagem_global": glob, "contexto": det, "pct": round(100 * det / max(1, tent), 1)}
    print(d.name[:2], res[d.name[:2]], flush=True)
