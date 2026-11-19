"""Síntese extrativa do documento inteiro, ligada ao render (story 2.4, FR-6, FR-13).

Generaliza `extrativa.py` (protótipo por capítulo) para o relatório todo e devolve um *modelo* com os
mesmos tipos de elemento, que `relatorio.render` transforma em DOCX/PDF. Nada é reescrito: a saída só
tem frases do original, recortadas com a formatação (runs) de origem.

- Unidades: frases de `p` e `quote`; cada `bullet` inteiro. NOTAS e REFERÊNCIAS saem (vão para
  `condensado`). Títulos, figuras/tabelas, fontes, siglas, sumário e pré-textuais ficam sempre.
- Pontuação: TextRank num grafo TF-IDF **por capítulo** (IDF do documento todo) + os bônus do protótipo.
  O grafo por capítulo mantém o custo em O(Σ capítulo²) em vez de O(documento²).
- Núcleo do diagnóstico (PROBLEMA, CAUSAS CRÍTICAS) entra inteiro, em todos os capítulos.
- Orçamento por seção (capítulo, h2) proporcional ao tamanho; seleção gulosa com MMR (redundância
  medida dentro do capítulo); depois um passe de ajuste leva a fração para perto do alvo.
- Fração = palavras das frases escolhidas ÷ palavras do texto corrido elegível (sem NOTAS e REFERÊNCIAS).
- Saída: `[…]` marca trecho omitido dentro do parágrafo; `[p. N]` aponta a página do original.
"""
import math
import re
from collections import Counter, defaultdict

from .extrativa import ANAFORA, NUCLEO, cos, dividir_frases, n_palavras, tokens

B, I, SUP = 1, 2, 4
FORA = ("NOTAS", "REFERÊNCIAS")      # seções que saem da síntese (contadas em `condensado`)
TEXTO = ("p", "quote", "bullet")
RETICENCIAS = "[…]"
LAMBDA = 0.7                         # MMR: peso da pontuação contra a redundância


# ---------------------------------------------------------------- unidades
def _runs_sem_nota(runs):
    """Runs sem os números de nota sobrescritos (as notas saem da síntese)."""
    return [(t, f) for t, f in runs if not (f & SUP) and t]


def _localizar(texto, frase, ini):
    """(a, b) da frase em `texto` a partir de `ini`, tolerando espaços diferentes; None se não achar."""
    padrao = r"\s+".join(re.escape(p) for p in frase.split())
    m = re.compile(padrao).search(texto, ini)
    return (m.start(), m.end()) if m else None


def _montar(E):
    """Unidades de texto (com posição no elemento) e o que fica fora da síntese."""
    unidades, fora = [], Counter()
    cap, secao = 0, None
    for gi, e in enumerate(E):
        t = e["t"]
        if t == "h1":
            cap, secao = cap + 1, None
        elif t == "h2":
            secao = (e.get("text") or "").strip().upper()
        if secao in FORA:
            if t == "notas":
                fora["notas"] += len(e.get("itens", []))
            elif t == "ref":
                fora["referencias"] += 1
            elif t in TEXTO:
                fora["paragrafos_fora"] += 1
            continue
        if t not in TEXTO:
            continue
        texto = "".join(x for x, _ in _runs_sem_nota(e["runs"]))
        frases = [texto.strip()] if t == "bullet" else dividir_frases(texto.strip())
        cursor = 0
        for pos, f in enumerate(frases):
            if not f:
                continue
            span = _localizar(texto, f, cursor)
            if span is None:  # não deveria ocorrer; sem posição, a frase não entra (nunca inventa texto)
                continue
            cursor = span[1]
            unidades.append({"txt": f, "gi": gi, "span": span, "cap": cap, "secao": secao,
                             "tipo": t, "pos": pos, "w": n_palavras(f)})
    return unidades, fora


# ---------------------------------------------------------------- pontuação
def _tfidf(unidades):
    docs = [Counter(tokens(u["txt"])) for u in unidades]
    df = Counter()
    for d in docs:
        df.update(d.keys())
    n = len(docs)
    vecs = []
    for d in docs:
        v = {w: (1 + math.log(c)) * math.log((n + 1) / (df[w] + 0.5)) for w, c in d.items()}
        norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({w: x / norma for w, x in v.items()})
    return vecs


def _textrank(ids, vecs, d=0.85, it=40):
    """TextRank esparso sobre as unidades `ids` (um capítulo). Devolve {id: rank normalizado}."""
    n = len(ids)
    if n == 0:
        return {}
    viz = [[] for _ in range(n)]
    for a in range(n):
        va = vecs[ids[a]]
        for b in range(a + 1, n):
            s = cos(va, vecs[ids[b]])
            if s > 0.05:
                viz[a].append((b, s)); viz[b].append((a, s))
    soma = [sum(s for _, s in v) or 1.0 for v in viz]
    r = [1.0 / n] * n
    for _ in range(it):
        r = [(1 - d) / n + d * sum(s / soma[b] * r[b] for b, s in viz[a]) for a in range(n)]
    m = max(r) or 1.0
    return {ids[a]: r[a] / m for a in range(n)}


def _pontuar(unidades, vecs):
    por_cap = defaultdict(list)
    for i, u in enumerate(unidades):
        por_cap[u["cap"]].append(i)
    tr = {}
    for ids in por_cap.values():
        tr.update(_textrank(ids, vecs))
    for i, u in enumerate(unidades):
        bonus = 0.0
        if u["pos"] == 0 and u["tipo"] == "p":
            bonus += 0.25  # frase-tema
        if re.search(r"\d+(,\d+)?\s?%|\d{1,3}(\.\d{3})+|\d+,\d+|\bp\.p\.", u["txt"]):
            bonus += 0.20  # dado quantitativo
        if u["secao"] and any(t in vecs[i] for t in tokens(u["secao"])):
            bonus += 0.10
        if ANAFORA.match(u["txt"]):
            bonus -= 0.20  # depende da frase anterior
        if u["tipo"] == "quote":
            bonus -= 0.25  # citação longa de lei/autor: secundária
        if u["w"] < 6:
            bonus -= 0.30
        u["score"] = tr.get(i, 0.0) + bonus
    return por_cap


# ---------------------------------------------------------------- seleção
def _selecionar(unidades, vecs, por_cap, fracao):
    total = sum(u["w"] for u in unidades)
    alvo = fracao * total
    escolhidas = set()
    red = [0.0] * len(unidades)       # maior similaridade com algo já escolhido no mesmo capítulo
    estado = {"usado": 0}

    def incluir(i):
        escolhidas.add(i)
        estado["usado"] += unidades[i]["w"]
        vi = vecs[i]
        for j in por_cap[unidades[i]["cap"]]:
            if j not in escolhidas:
                red[j] = max(red[j], cos(vi, vecs[j]))

    def mmr(i):
        return LAMBDA * unidades[i]["score"] - (1 - LAMBDA) * red[i]

    for i, u in enumerate(unidades):  # núcleo do diagnóstico entra inteiro
        if u["secao"] in NUCLEO:
            incluir(i)
    resto_total = total - estado["usado"]
    resto_alvo = max(0.0, alvo - estado["usado"])

    por_secao = defaultdict(list)
    for i, u in enumerate(unidades):
        if u["secao"] not in NUCLEO:
            por_secao[(u["cap"], u["secao"])].append(i)
    for ids in por_secao.values():
        orc = resto_alvo * sum(unidades[i]["w"] for i in ids) / max(1, resto_total)
        usado_sec, cand = 0, list(ids)
        while cand:
            melhor = max(cand, key=mmr)
            cand.remove(melhor)
            w = unidades[melhor]["w"]
            if estado["usado"] + w > alvo:
                continue
            if usado_sec + w <= orc or (usado_sec == 0 and w <= orc * 1.5):
                incluir(melhor)
                usado_sec += w

    # ajuste: o orçamento por seção tende a ficar abaixo do alvo; completa pelas melhores restantes
    teto = alvo + 0.004 * total
    for i in sorted((i for i in range(len(unidades)) if i not in escolhidas), key=mmr, reverse=True):
        if estado["usado"] >= alvo - 0.002 * total:
            break
        if estado["usado"] + unidades[i]["w"] <= teto:
            incluir(i)
    return escolhidas, total, estado["usado"]


# ---------------------------------------------------------------- modelo de saída
def _fatiar(runs, a, b):
    """Runs (texto, flag) do intervalo [a, b) do texto concatenado."""
    out, pos = [], 0
    for t, f in runs:
        ini, fim = pos, pos + len(t)
        pos = fim
        if fim <= a or ini >= b:
            continue
        pedaco = t[max(a, ini) - ini:min(b, fim) - ini]
        if pedaco:
            out.append([pedaco, f])
    return out


def _marcador_pagina(e):
    pg, fim = e.get("pg"), e.get("pg_fim")
    if pg is None:
        return None
    return f"[p. {pg}]" if not fim or fim == pg else f"[p. {pg}–{fim}]"


def _elemento(e, us, escolhidas):
    """Elemento de texto só com as frases escolhidas, `[…]` nos cortes e a página de origem."""
    runs = _runs_sem_nota(e["runs"])
    out, omitiu = [], False
    for i, u in us:
        if i not in escolhidas:
            omitiu = True
            continue
        if out:
            out.append([" ", 0])
        if omitiu:
            out += [[RETICENCIAS, 0], [" ", 0]]
            omitiu = False
        out += _fatiar(runs, *u["span"])
    if omitiu:
        out += [[" ", 0], [RETICENCIAS, 0]]
    marcador = _marcador_pagina(e)
    if marcador:
        out.append([" " + marcador, I])
    novo = {"t": e["t"], "runs": out, "sintese": True}
    for k in ("pg", "pg_fim"):
        if k in e:
            novo[k] = e[k]
    return novo


def _plural(n, s, p):
    return f"{n:,}".replace(",", ".") + " " + (s if n == 1 else p)


def sintetizar(model, fracao):
    """(modelo sintetizado, relatório da síntese). Só relatórios (Q4).

    relatório: palavras_original (texto elegível), palavras_sintese, fracao_real (0–1), condensado
    (o que saiu, com contagem), descricao, frases, paragrafos_omitidos, aviso.
    """
    if model.get("meta", {}).get("tipo") != "relatorio":
        raise ValueError("síntese só está disponível para relatórios (Q4)")
    E = model["elementos"]
    unidades, fora = _montar(E)
    if not unidades:
        raise ValueError("documento sem texto corrido para sintetizar")
    vecs = _tfidf(unidades)
    por_cap = _pontuar(unidades, vecs)
    escolhidas, total, usado = _selecionar(unidades, vecs, por_cap, fracao)

    por_el = defaultdict(list)
    for i, u in enumerate(unidades):
        por_el[u["gi"]].append((i, u))
    pct = round(fracao * 100)
    fracao_real = usado / total
    percentual = (f"{fracao_real * 100:.1f}".rstrip("0").rstrip(".")
                  if abs(fracao_real - fracao) > 0.01 else str(pct))
    ressalva = (f" Alvo de {pct}% inviável com o núcleo obrigatório preservado."
                if usado > fracao * total + 0.01 * total else "")
    aviso = (f"Síntese extrativa: cerca de {percentual}% do texto corrido, só com frases do original, sem reescrita. "
             f"{RETICENCIAS} marca trecho omitido; [p. N] aponta a página do documento original. "
             f"Títulos, tabelas, gráficos e as seções PROBLEMA e CAUSAS CRÍTICAS foram mantidos; "
             f"notas e referências foram retiradas.{ressalva}")
    novos, omitidos, cap, secao = [], 0, 0, None
    for gi, e in enumerate(E):
        t = e["t"]
        if t == "h1":
            cap, secao = cap + 1, None
        elif t == "h2":
            secao = (e.get("text") or "").strip().upper()
        if secao in FORA and t != "pagina":  # página-imagem (story 2.5) após REFERÊNCIAS abre o capítulo seguinte
            continue
        if t in TEXTO:
            us = por_el.get(gi, [])
            if not any(i in escolhidas for i, _ in us):
                omitidos += 1
                continue
            novos.append(_elemento(e, us, escolhidas))
        else:
            novos.append(e)
        if t == "id":
            novos.append({"t": "p", "runs": [[aviso, I]], "sintese_aviso": True})
    if not any(e.get("sintese_aviso") for e in novos):
        novos.insert(0, {"t": "p", "runs": [[aviso, I]], "sintese_aviso": True})

    fracao_real = usado / total
    n_fora = len(unidades) - len(escolhidas)
    condensado = [f"{_plural(n_fora, 'frase', 'frases')} do texto corrido "
                  f"({_plural(total - usado, 'palavra', 'palavras')}) omitidas; "
                  f"{_plural(omitidos, 'parágrafo inteiro', 'parágrafos inteiros')}"]
    if fora["notas"]:
        condensado.append(_plural(fora["notas"], "nota de rodapé", "notas de rodapé"))
    if fora["referencias"]:
        condensado.append(_plural(fora["referencias"], "referência bibliográfica", "referências bibliográficas"))
    descricao = (f"síntese extrativa de {fracao_real * 100:.1f}% do texto corrido (alvo {pct}%): "
                 f"{len(escolhidas)} de {len(unidades)} frases mantidas, sem reescrita; "
                 f"núcleo, títulos, tabelas e gráficos mantidos").replace(".", ",", 1)
    meta = dict(model.get("meta", {}), saida_sufixo=f"SINTESE{pct}", sintese={"alvo": fracao})
    rel = {"palavras_original": total, "palavras_sintese": usado, "fracao_real": round(fracao_real, 4),
           "condensado": condensado, "descricao": descricao, "frases": [len(escolhidas), len(unidades)],
           "paragrafos_omitidos": omitidos, "aviso": aviso}
    return {"meta": meta, "elementos": novos}, rel
