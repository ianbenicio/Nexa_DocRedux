"""Integridade numérica (M09, story 2.7): números e operadores do original devem chegar intactos à saída.

A cobertura por palavras descarta dígitos isolados e quebra "6,9" em "6" e "9", então trocar um valor
("a resposta é 2" -> "é 9") ou um sinal ("3 + 4" -> "3 − 4") passaria despercebido. Esta checagem roda sobre
os mesmos textos da cobertura (mesmas exclusões: páginas removidas, cabeçalho/rodapé, figuras, sumário,
marcadores sobrescritos) e compara multiconjuntos de tokens numéricos. Não altera a contagem de palavras
usada no preço (DT-21).
"""
import collections
import re

# número sem sinal: separadores de milhar/decimal, fração "3/4", porcentagem e ordinal. Espaços internos
# ("3 / 4", "25 %") são normalizados. Não começa colado a letra ("pt2023") nem no meio de "6,9"; depois de ponto
# de abreviação começa ("jun.2009" e "jun. 2009" dão o mesmo token: espaço perdido na junção de linhas não conta
# como número trocado).
NUM = re.compile(r"(?<!\w)(?<!\d[.,])\d+(?:[.,]\d+)*(?:\s?/\s?\d+)?(?:\s?%|[ºª°])?")
# operador entre dois números: "3 + 4", "3 - 4", "2×5", "1 : 10" (divisão escolar); só conta com dígito dos dois
# lados. Inclui o hífen ASCII (revisão 2026-11-20, A3): "3 - 4" trocado por "3 + 4" ou "3 4" tem de aparecer.
# Intervalos ("2014-2024") e códigos ("70040-020") também dão "op:-", dos dois lados igual.
OP = re.compile(r"(?<=\d)\s*([-+×÷:=<>≤≥−*])\s*(?=[-−+]?\d)")
SINAIS = "-−+"


def _sinal(texto, i):
    """Sinal unário colado ao número que começa em texto[i] ("-3", "× -2", "(−3)", "2,0% -24,8%"), ou "".

    O sinal unário também vira token "op:-"/"op:+" (o número fica sem sinal). Assim a contagem não depende de
    espaço nem de quebra, que o render muda: "3 +4" == "3 + 4" (A4), e a célula "-24,8%" sozinha no original
    == "12,0 -24,8%" numa linha do DOCX (lá o OP já conta o sinal; aqui não conta de novo). Colado a letra é
    hífen ("x-3" == "x - 3", como antes). Só colado ao dígito: "- 3" é travessão de lista.
    """
    if i == 0 or texto[i - 1] not in SINAIS:
        return ""
    if i >= 2 and texto[i - 2].isalnum():  # "x-3": hífen; "3-4": o OP já contou
        return ""
    j = i - 2
    while j >= 0 and texto[j].isspace():
        j -= 1
    if j >= 0 and texto[j].isdigit():  # "3 -4": o OP já contou
        return ""
    return texto[i - 1]


def tokens(texto):
    """Tokens numéricos de um texto, normalizados: números sem sinal e sinais/operadores ("op:+", "op:-")."""
    texto = texto.replace("ᵒ", "º")
    out = []
    for m in NUM.finditer(texto):
        out.append(re.sub(r"\s+", "", m.group(0)))
        s = _sinal(texto, m.start())
        if s:
            out.append("op:" + s.replace("−", "-"))
    out += ["op:" + m.group(1).replace("−", "-").replace("*", "×") for m in OP.finditer(texto)]
    return out


def contar(textos):
    c = collections.Counter()
    for t in textos:
        c.update(tokens(t))
    return c


def comparar(original, saida, limite=20):
    """Compara contadores de tokens. ok=True quando nada do original falta na saída."""
    faltando = original - saida
    a_mais = saida - original
    return {
        "ok": not faltando,
        "tokens_original": sum(original.values()),
        "faltando": dict(faltando.most_common(limite)),
        "a_mais": dict(a_mais.most_common(limite)),
    }


# ----------------------------------------------------------------------------- contexto local (M12, story 2.13)
# A contagem global aceita um número certo vindo de outro lugar: "tem 4 lados ... tem 6 faces" trocado por
# "tem 6 lados ... tem 4 faces" tem os mesmos tokens. Aqui cada número do original precisa reaparecer entre os
# mesmos vizinhos (palavra ou número antes e depois). Na fronteira de bloco basta um vizinho: o render muda o que
# fica antes/depois de um parágrafo (selo "Aula 5" mudou de lugar, texto ao lado da imagem), não o miolo da frase.
# Fronteira = começo/fim de linha no original ou começo/fim de parágrafo na saída. No meio da linha, achar os mesmos
# dois vizinhos com outro número entre eles é troca, mesmo que o número certo apareça ao lado de um deles.
SEQ = re.compile(NUM.pattern + r"|[^\W\d_]+")
URL = re.compile(r"%[0-9A-Fa-f]{2}|://|www\.", re.IGNORECASE)
# pedaço de URL partido na linha anterior ("view/86203/81685", "saude-2024-brasil", "dengue_1920x.jpg"): letra e
# separador de caminho no mesmo pedaço sem espaço
CAMINHO = re.compile(r"^(?=.*[^\W\d_])(?=.*[/_=?&#-]).*$")
IGNORADO = "~"  # número dentro de URL/caminho: a quebra de linha muda os vizinhos; a contagem global cobre


def _url(pedaco):
    return bool(URL.search(pedaco) or CAMINHO.match(pedaco))


def sequencia(textos):
    """(tokens, fronteiras): palavras e números de todos os textos, na ordem e sem operadores, e o conjunto dos
    índices que começam um texto (linha do original, parágrafo da saída)."""
    out, ini = [], set()
    for t in textos:
        t = t.replace("ᵒ", "º")
        urls = [(m.start(), m.end()) for m in re.finditer(r"\S+", t) if _url(m.group(0))]
        primeiro = True
        for m in SEQ.finditer(t):
            w = m.group(0)
            if w[0].isdigit():
                w = re.sub(r"\s+", "", w)
                if any(a <= m.start() < b for a, b in urls):
                    w = IGNORADO + w
            else:
                w = w.lower()
            if primeiro:
                ini.add(len(out)); primeiro = False
            out.append(w)
    return out, ini


def _eh_num(w):
    return w is not None and (w[0].isdigit() or w[0] == IGNORADO)


def contexto(original, saida, limite=10):
    """Números do original que não reaparecem entre os mesmos vizinhos na saída.

    original/saida: resultados de `sequencia()`. Cada ocorrência de número na saída serve a uma ocorrência do
    original só, achada nesta ordem:
      1. mesmos vizinhos dos dois lados (passada própria, antes das outras);
      2. no meio de linha do original, a saída tem os mesmos dois vizinhos com OUTRO número entre eles: troca;
      3. um vizinho só, se o outro lado é fronteira (fim/começo de linha no original ou de parágrafo na saída).
    Número colado a outro número (tabela lida por linha no original e por coluna no DOCX, gabarito "1 2 3 ... 29",
    grade de contas) fica com a contagem global, que já o cobra.
    Devolve {"total": n, "trechos": [...]} com até `limite` trechos do original (3 tokens de cada lado)."""
    (so, ini_o), (sd, ini_d) = original, saida
    sd = [w.lstrip(IGNORADO) for w in sd]  # a marca so decide o que cobrar no original; na saida o numero e igual
    n = len(sd)
    idx = collections.defaultdict(list)  # chave -> posicoes de numero na saida
    for j in range(n):
        if not sd[j][0].isdigit():
            continue
        e = sd[j - 1] if j else None
        d = sd[j + 1] if j + 1 < n else None
        ini, fim = j in ini_d, j + 1 in ini_d or d is None
        idx[("trio", e, sd[j], d)].append(j)
        if e is not None:
            idx[("esq", e, sd[j])].append(j)
            if fim:
                idx[("esq_borda", e, sd[j])].append(j)
        if d is not None:
            idx[("dir", sd[j], d)].append(j)
            if ini:
                idx[("dir_borda", sd[j], d)].append(j)
        if not ini and not fim and not _eh_num(e) and not _eh_num(d):
            idx[("molde", e, d)].append(j)
    usados = set()

    def pegar(chave):
        for j in idx.get(chave, ()):
            if j not in usados:
                usados.add(j); return True
        return False

    def viz(i):
        return so[i - 1] if i else None, so[i + 1] if i + 1 < len(so) else None

    # 1a passada: mesmos vizinhos dos dois lados (antes de qualquer casamento parcial gastar a ocorrencia)
    resto = [i for i, w in enumerate(so) if w[0].isdigit() and not pegar(("trio", viz(i)[0], w, viz(i)[1]))]
    trechos, total = [], 0
    for i in resto:
        w = so[i]
        esq, dir_ = viz(i)
        borda_esq, borda_dir = i in ini_o or esq is None, i + 1 in ini_o or dir_ is None
        if _eh_num(esq) or _eh_num(dir_):  # colado a outro numero: so a contagem global (nao gasta ocorrencia)
            continue
        if not borda_esq and not borda_dir and pegar(("molde", esq, dir_)):
            pass  # troca: conta como fora de contexto
        elif esq is not None and (pegar(("esq_borda", esq, w)) or (borda_dir and pegar(("esq", esq, w)))):
            continue
        elif dir_ is not None and (pegar(("dir_borda", w, dir_)) or (borda_esq and pegar(("dir", w, dir_)))):
            continue
        total += 1
        if len(trechos) < limite:
            trechos.append(" ".join(x.lstrip(IGNORADO) for x in so[max(0, i - 3):i + 4]))
    return {"total": total, "trechos": trechos}


def verificar(textos_original, textos_saida, limite=20):
    """Contagem global (M09) + contexto local (M12). ok=False se falta número/operador ou se há número fora do
    lugar de origem."""
    r = comparar(contar(textos_original), contar(textos_saida), limite)
    ctx = contexto(sequencia(textos_original), sequencia(textos_saida))
    r["fora_de_contexto"] = ctx["total"]
    r["fora_de_contexto_trechos"] = ctx["trechos"]
    r["ok"] = r["ok"] and not ctx["total"]
    return r
