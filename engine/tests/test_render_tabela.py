"""Story 2.14: tabela do relatório com a grade calculada uma vez dá a mesma estrutura que t.cell() a cada célula."""
from docx import Document
from docx.oxml.ns import qn

from docredux_engine.relatorio import render as RR


def _cel(r, c, texto, rs=1, cs=1):
    return {"r": r, "c": c, "rs": rs, "cs": cs, "text": texto, "fill": None}


def _corpo(cells, nr=4, nc=4):
    return {"nrows": nr, "ncols": nc, "colw": [60] * nc, "size": 8, "cells": cells}


def _estrutura(tbl):
    """Por linha: (texto, gridSpan, vMerge) de cada <w:tc>."""
    out = []
    for tr in tbl.iter(qn("w:tr")):
        linha = []
        for tc in tr.iter(qn("w:tc")):
            pr = tc.tcPr
            span = pr.find(qn("w:gridSpan")) if pr is not None else None
            vm = pr.find(qn("w:vMerge")) if pr is not None else None
            linha.append(("".join(t.text or "" for t in tc.iter(qn("w:t"))),
                          span.get(qn("w:val")) if span is not None else None,
                          (vm.get(qn("w:val")) or "continue") if vm is not None else None))
        out.append(linha)
    return out


def _referencia(body):
    """Caminho antigo: t.cell() para cada célula do modelo."""
    t = Document().add_table(rows=body["nrows"], cols=body["ncols"])
    for cd in body["cells"]:
        r0, c0, rs, cs = cd["r"], cd["c"], cd["rs"], cd["cs"]
        if r0 + rs > body["nrows"] or c0 + cs > body["ncols"]:
            continue
        c = t.cell(r0, c0)
        if rs > 1 or cs > 1:
            c = c.merge(t.cell(r0 + rs - 1, c0 + cs - 1))
        c.paragraphs[0].add_run(cd["text"])
    return _estrutura(t._tbl)


def _nova(tmp_path, body):
    return _estrutura(RR.RR(tmp_path).table(body)._tbl)


def test_mesclas_horizontal_vertical_e_bloco(tmp_path):
    body = _corpo([_cel(0, 0, "titulo", cs=4),
                   _cel(1, 0, "linha", rs=2), _cel(1, 1, "a"), _cel(1, 2, "bloco", rs=2, cs=2),
                   _cel(2, 1, "b"),
                   _cel(3, 0, "c"), _cel(3, 1, "d"), _cel(3, 2, "e"), _cel(3, 3, "f")])
    assert _nova(tmp_path, body) == _referencia(body)


def test_celula_sobreposta_usa_a_grade_mesclada(tmp_path):
    """Modelo malformado: (1,1) cai dentro da mescla de (0,0)-(1,1). Volta a t.cell(), como antes."""
    body = _corpo([_cel(0, 0, "grande", rs=2, cs=2), _cel(1, 1, "dentro"), _cel(2, 2, "fora")])
    assert _nova(tmp_path, body) == _referencia(body)


def test_celula_fora_da_grade_e_ignorada(tmp_path):
    body = _corpo([_cel(0, 0, "ok"), _cel(3, 3, "estoura", cs=2)])
    assert _nova(tmp_path, body) == _referencia(body)
