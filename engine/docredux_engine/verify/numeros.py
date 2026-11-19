"""Integridade numérica (M09, story 2.7): números e operadores do original devem chegar intactos à saída.

A cobertura por palavras descarta dígitos isolados e quebra "6,9" em "6" e "9", então trocar um valor
("a resposta é 2" -> "é 9") ou um sinal ("3 + 4" -> "3 − 4") passaria despercebido. Esta checagem roda sobre
os mesmos textos da cobertura (mesmas exclusões: páginas removidas, cabeçalho/rodapé, figuras, sumário,
marcadores sobrescritos) e compara multiconjuntos de tokens numéricos. Não altera a contagem de palavras
usada no preço (DT-21).
"""
import collections
import re

# número com sinal opcional (só no início de token: "2014-2024" não vira "-2024"), separadores de milhar/decimal,
# fração "3/4", porcentagem e ordinal. Espaços internos ("3 / 4", "25 %") são normalizados.
# Não começa colado a letra ("pt2023") nem no meio de "6,9"; depois de ponto de abreviação começa
# ("jun.2009" e "jun. 2009" dão o mesmo token: espaço perdido na junção de linhas não conta como número trocado).
NUM = re.compile(r"(?<!\w)(?<!\d[.,])(?:[-−+](?=\d))?\d+(?:[.,]\d+)*(?:\s?/\s?\d+)?(?:\s?%|[ºª°])?")
# operador entre dois números: "3 + 4", "2×5", "1 : 10" (divisão escolar); só conta com dígito dos dois lados
OP = re.compile(r"(?<=\d)\s*([+×÷:=<>≤≥−*])\s*(?=[-−+]?\d)")


def tokens(texto):
    """Tokens numéricos de um texto, normalizados: números e operadores entre números ("op:+")."""
    texto = texto.replace("ᵒ", "º")
    out = [re.sub(r"\s+", "", m.group(0)).replace("−", "-") for m in NUM.finditer(texto)]
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
