"""Síntese extrativa (sem LLM) a partir do modelo.json do DocRedux.

Protótipo do D16: seleciona frases do original, sem reescrever, até atingir a
fração de palavras pedida. Python puro (sem dependências).

Uso:
    python sintese_extrativa.py MODELO.json --capitulo 1 --fracao 0.2 --saida cap01_20.md
    python sintese_extrativa.py MODELO.json --capitulo 1 --fracao 0.2 0.35 0.5 --dir saida/

Estratégia:
1. Recorta o capítulo (do h1 N até o próximo h1).
2. Unidades de texto: frases de 'p' e 'quote', cada 'bullet' inteiro.
   Títulos e figuras (tabelas e gráficos com legenda) ficam sempre.
   Seções NOTAS e REFERÊNCIAS saem da síntese e são contadas no relatório.
3. Pontuação = TextRank (grafo de similaridade TF-IDF) + bônus por papel:
   frase-tema (1ª do parágrafo), dado numérico, palavras do título da seção,
   seção PROBLEMA ou CAUSAS (núcleo do diagnóstico).
4. Orçamento por seção (h2), proporcional ao tamanho, para manter cobertura.
5. Seleção gulosa com penalidade de redundância (MMR).
6. Saída em ordem original; '[…]' marca trechos omitidos; '§N' aponta o
   parágrafo de origem no modelo.
"""

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

STOP = set("""
a à às ao aos as o os um uma uns umas de da das do dos em na nas no nos por pela pelas pelo pelos
para com sem sob sobre entre até após ante contra desde e ou mas nem que se como quando onde porque
pois porém contudo entretanto todavia também já ainda mais menos muito muitos muita muitas pouco
mesmo mesma mesmos mesmas outro outra outros outras este esta estes estas esse essa esses essas
aquele aquela aqueles aquelas isto isso aquilo seu sua seus suas lhe lhes ele ela eles elas nós
você vocês eu me te nos vos ser é são foi foram era eram será serão sido sendo estar está estão
esteve estava estavam ter tem têm tinha tinham teve tiveram haver há houve havia qual quais cujo
cuja cujos cujas assim então não sim apenas entre cada todo toda todos todas tal tais segundo
conforme sendo vez vezes ano anos bem caso sob dessa desse desses dessas deste desta destes destas
nessa nesse nesses nessas neste nesta nestes nestas pela pelo
""".split())

ABREV = ["n.º", "nº", "art.", "arts.", "p.", "pp.", "v.", "inc.", "Dr.", "Sr.", "Sra.", "et al.",
         "p.p.", "aprox.", "ex.", "fig.", "tab.", "cap.", "séc.", "Ibid.", "op. cit."]


NUCLEO = ("PROBLEMA", "CAUSAS CRÍTICAS")
ANAFORA = re.compile(r"^(Tais|Esse|Essa|Esses|Essas|Isso|Este|Esta|Estes|Estas|Nesse|Nessa|Desse|Dessa|"
                     r"Apesar d|Assim|Dessa forma|Desse modo|Além disso|Por isso|Contudo|Entretanto|Todavia)")


def fonte_txt(f):
    """Fontes vêm como lista de listas de runs; achata para texto."""
    if not f:
        return ""
    if isinstance(f, str):
        return f
    partes = []
    for item in f:
        if isinstance(item, list):
            partes.append("".join(r[0] if isinstance(r, list) else str(r) for r in item))
        else:
            partes.append(str(item))
    return " ".join(x.strip() for x in partes if x.strip())


def texto_de(e):
    """Texto do elemento, sem marcadores de nota (runs sobrescritos, flag 4)."""
    if "runs" in e:
        return "".join(t for t, f in e["runs"] if not (f & 4)).strip()
    return (e.get("text") or "").strip()


def dividir_frases(txt):
    protegido = txt
    for i, a in enumerate(ABREV):
        protegido = protegido.replace(a, a.replace(".", f"\u0000{i}\u0000"))
    # números decimais/milhares: 6.9 / 1.234 já não têm espaço depois do ponto
    # Fatiar pela posicao preserva aspas/parenteses, que re.split consumia.
    partes, ini = [], 0
    for m in re.finditer(r"(?<=[.!?…])[\"”’)]*\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"(])", protegido):
        partes.append(protegido[ini:m.end()].rstrip())
        ini = m.end()
    partes.append(protegido[ini:])
    out = []
    for p in partes:
        for i, a in enumerate(ABREV):
            p = p.replace(a.replace(".", f"\u0000{i}\u0000"), a)
        p = p.strip()
        if not p:
            continue
        if out and out[-1].count("(") > out[-1].count(")"):
            out[-1] += " " + p  # ponto dentro de citação: "(Brasil. Inep, 2022)"
        else:
            out.append(p)
    return out


def tokens(txt):
    pal = re.findall(r"[a-záéíóúâêôãõçà]+", txt.lower())
    return [w[:6] for w in pal if w not in STOP and len(w) > 2]  # radical simples: 6 letras


def n_palavras(txt):
    return len(re.findall(r"\w+", txt))


def recortar_capitulo(E, n):
    idx_h1 = [i for i, e in enumerate(E) if e["t"] == "h1" and e.get("secao") == "capitulo"]
    ini = idx_h1[n - 1]
    fim = idx_h1[n] if n < len(idx_h1) else len(E)
    return ini, E[ini:fim]


def montar_unidades(cap, base_idx):
    """Lista de blocos (para a saída) e unidades de texto (para a seleção)."""
    blocos, unidades = [], []
    secao, descartado = None, Counter()
    for k, e in enumerate(cap):
        gi = base_idx + k
        t = e["t"]
        if t == "h2":  # seção = h2; h3/h4 são subtítulos dentro dela
            secao = texto_de(e).upper()
        if secao in ("NOTAS", "REFERÊNCIAS") and t not in ("h1",):
            descartado[secao] += 1
            continue
        if t in ("h1", "h2", "h3", "h4"):
            blocos.append({"tipo": t, "texto": texto_de(e), "gi": gi})
        elif t == "fig":
            blocos.append({"tipo": "fig", "texto": e.get("caption", ""), "fontes": e.get("fontes"), "gi": gi})
        elif t in ("p", "quote"):
            frases = dividir_frases(texto_de(e))
            ids = []
            for j, f in enumerate(frases):
                unidades.append({"txt": f, "secao": secao, "tipo": t, "pos": j, "gi": gi})
                ids.append(len(unidades) - 1)
            blocos.append({"tipo": t, "unid": ids, "gi": gi})
        elif t == "bullet":
            unidades.append({"txt": texto_de(e), "secao": secao, "tipo": "bullet", "pos": 0, "gi": gi})
            blocos.append({"tipo": "bullet", "unid": [len(unidades) - 1], "gi": gi})
    return blocos, unidades, descartado


def tfidf(unidades):
    docs = [Counter(tokens(u["txt"])) for u in unidades]
    df = Counter()
    for d in docs:
        df.update(d.keys())
    N = len(docs)
    vecs = []
    for d in docs:
        v = {w: (1 + math.log(c)) * math.log((N + 1) / (df[w] + 0.5)) for w, c in d.items()}
        norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norma for w, x in v.items()})
    return vecs


def cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def textrank(vecs, d=0.85, it=40):
    n = len(vecs)
    W = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            s = cos(vecs[i], vecs[j])
            if s > 0.05:
                W[i][j] = W[j][i] = s
    soma = [sum(r) or 1.0 for r in W]
    r = [1.0 / n] * n
    for _ in range(it):
        r = [(1 - d) / n + d * sum(W[j][i] / soma[j] * r[j] for j in range(n) if W[j][i]) for i in range(n)]
    m = max(r) or 1.0
    return [x / m for x in r]


def pontuar(unidades, vecs):
    tr = textrank(vecs)
    for u, s, v in zip(unidades, tr, vecs):
        bonus = 0.0
        if u["pos"] == 0 and u["tipo"] == "p":
            bonus += 0.25  # frase-tema
        if re.search(r"\d+(,\d+)?\s?%|\d{1,3}(\.\d{3})+|\d+,\d+|\bp\.p\.", u["txt"]):
            bonus += 0.20  # dado quantitativo
        if u["secao"] and any(t in v for t in tokens(u["secao"])):
            bonus += 0.10
        if ANAFORA.match(u["txt"]):
            bonus -= 0.20  # depende da frase anterior
        if u["tipo"] == "quote":
            bonus -= 0.25  # citação longa de lei/autor: secundária
        if n_palavras(u["txt"]) < 6:
            bonus -= 0.30
        u["score"] = s + bonus


def selecionar(unidades, vecs, fracao, lam=0.7):
    total = sum(n_palavras(u["txt"]) for u in unidades)
    alvo = fracao * total
    por_secao = defaultdict(list)
    for i, u in enumerate(unidades):
        por_secao[u["secao"]].append(i)
    # núcleo do diagnóstico (PROBLEMA, CAUSAS CRÍTICAS) entra sempre inteiro
    escolhidas = {i for i, u in enumerate(unidades) if u["secao"] in NUCLEO}
    nucleo = sum(n_palavras(unidades[i]["txt"]) for i in escolhidas)
    resto_total = total - nucleo
    resto_alvo = max(0.0, alvo - nucleo)
    for sec, ids in por_secao.items():
        if sec in NUCLEO:
            continue
        tam = sum(n_palavras(unidades[i]["txt"]) for i in ids)
        orc = resto_alvo * tam / max(1, resto_total)
        usado, cand = 0, list(ids)
        while cand:
            def mmr(i):
                red = max((cos(vecs[i], vecs[j]) for j in escolhidas), default=0.0)
                return lam * unidades[i]["score"] - (1 - lam) * red
            melhor = max(cand, key=mmr)
            cand.remove(melhor)
            w = n_palavras(unidades[melhor]["txt"])
            global_usado = sum(n_palavras(unidades[j]["txt"]) for j in escolhidas)
            if global_usado + w > alvo:
                continue
            if usado + w <= orc or (usado == 0 and w <= orc * 1.5):
                escolhidas.add(melhor)
                usado += w
    return escolhidas, total


def renderizar(blocos, unidades, escolhidas, titulo_frac):
    linhas = [f"> Síntese extrativa ({titulo_frac}) — todas as frases são do original; "
              f"[…] = trecho omitido; §N = parágrafo de origem no modelo.\n"]
    for b in blocos:
        t = b["tipo"]
        if t == "h1":
            linhas.append(f"# {b['texto']}\n")
        elif t in ("h2", "h3", "h4"):
            nivel = {"h2": "##", "h3": "###", "h4": "####"}[t]
            linhas.append(f"{nivel} {b['texto']}\n")
        elif t == "fig":
            ft = fonte_txt(b.get("fontes"))
            fonte = f" — {ft}" if ft else ""
            linhas.append(f"*[{b['texto']}]*{fonte}\n")
        else:
            ids = b["unid"]
            sel = [i for i in ids if i in escolhidas]
            if not sel:
                continue
            partes, ant = [], None
            for i in ids:
                if i in escolhidas:
                    if ant is not None and i != ant + 1:
                        partes.append("[…]")
                    partes.append(unidades[i]["txt"])
                    ant = i
            if ids[0] not in escolhidas:
                partes.insert(0, "[…]")
            if ids[-1] not in escolhidas:
                partes.append("[…]")
            pref = "- " if t == "bullet" else ("> " if t == "quote" else "")
            linhas.append(f"{pref}{' '.join(partes)} §{b['gi']}\n")
    return "\n".join(linhas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modelo")
    ap.add_argument("--capitulo", type=int, required=True)
    ap.add_argument("--fracao", type=float, nargs="+", required=True)
    ap.add_argument("--dir", default=".")
    a = ap.parse_args()
    try:
        m = json.loads(Path(a.modelo).read_text(encoding="utf-8"))
    except Exception as err:
        sys.exit(f"Falha ao ler {a.modelo}: {err}")
    E = m["elementos"]
    base, cap = recortar_capitulo(E, a.capitulo)
    blocos, unidades, descartado = montar_unidades(cap, base)
    vecs = tfidf(unidades)
    pontuar(unidades, vecs)
    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    relatorio = {"capitulo": a.capitulo, "titulo": blocos[0]["texto"], "unidades": len(unidades),
                 "fora_da_sintese": dict(descartado), "versoes": []}
    num_re = re.compile(r"\d+(?:[.,]\d+)*\s?%?")
    nums_orig = set(n for u in unidades for n in num_re.findall(u["txt"]) if len(n) > 1)
    for f in a.fracao:
        esc, total = selecionar(unidades, vecs, f)
        pal = sum(n_palavras(unidades[i]["txt"]) for i in esc)
        nums = set(n for i in esc for n in num_re.findall(unidades[i]["txt"]) if len(n) > 1)
        nome = out / f"cap{a.capitulo:02d}_{int(f * 100)}pct.md"
        nome.write_text(renderizar(blocos, unidades, esc, f"alvo {int(f*100)}% das palavras"), encoding="utf-8")
        relatorio["versoes"].append({
            "alvo_pct": round(f * 100), "palavras_original": total, "palavras_sintese": pal,
            "real_pct": round(100 * pal / total, 1), "frases": f"{len(esc)}/{len(unidades)}",
            "dados_numericos_mantidos_pct": round(100 * len(nums) / max(1, len(nums_orig)), 1),
            "arquivo": nome.name})
    (out / f"cap{a.capitulo:02d}_relatorio.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
