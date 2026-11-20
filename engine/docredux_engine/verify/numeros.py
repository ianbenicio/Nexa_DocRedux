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
