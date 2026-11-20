"""Revisão 2026-11-20, A1 (R51): grade de contas para resolver não se funde (R16) e mantém o espaço de resposta."""
import json

import pymupdf
from docx import Document

from docredux_engine.ficha import extract as FE
from docredux_engine.ficha import render as FR


def _linha(txt, x, y):
    return {"bb": pymupdf.Rect(x, y, x + 40, y + 13), "txt": txt, "raw": txt}


def _rows(raw):
    """Como page_items: trechos na mesma linha de base viram uma row."""
    rows = []
    for r in sorted(raw, key=lambda r: (r["bb"].y0, r["bb"].x0)):
        if rows and abs(rows[-1]["bb"].y0 - r["bb"].y0) < 3:
            rows[-1] = dict(rows[-1], txt=rows[-1]["txt"] + " " + r["txt"], bb=rows[-1]["bb"] | r["bb"])
        else:
            rows.append(dict(r))
    return rows


def test_grade_mantem_colunas_e_celula_vazia():
    raw = [_linha("1 : 10 =", 109, 264), _linha("25 : 10 =", 234, 264), _linha("1 248 : 10 =", 363, 264),
           _linha("5 : 10 =", 109, 297), _linha("157 : 10 =", 234, 297),
           _linha("25 : 100 =", 234, 330)]  # 1a coluna vazia nesta linha
    resto, grades = FE.contas_grade(_rows(raw), raw)
    assert resto == []
    assert grades == [{"y0": 264, "rows": [["1 : 10 =", "25 : 10 =", "1 248 : 10 ="],
                                           ["5 : 10 =", "157 : 10 =", ""],
                                           ["", "25 : 100 =", ""]]}]


def test_texto_comum_e_conta_com_resposta_nao_viram_grade():
    raw = [_linha("Anotem os resultados ao lado das operações:", 109, 200),
           _linha("3 + 4 = 7", 109, 230),  # já resolvida: não é espaço de resposta
           _linha("Pinte 3 de 4 partes =", 109, 260)]
    rows = _rows(raw)
    resto, grades = FE.contas_grade(rows, raw)
    assert grades == [] and resto == rows


def test_grades_separadas_por_texto():
    raw = [_linha("2 × 3 =", 109, 100), _linha("2 × 4 =", 234, 100),
           _linha("Agora divida:", 109, 130),
           _linha("8 : 2 =", 109, 160)]
    resto, grades = FE.contas_grade(_rows(raw), raw)
    assert [r["txt"] for r in resto] == ["Agora divida:"]
    assert [g["rows"] for g in grades] == [[["2 × 3 =", "2 × 4 ="]], [["8 : 2 ="]]]


def test_render_da_grade_sem_bordas_uma_conta_por_celula(tmp_path):
    model = {"meta": {"origem": "x.pdf", "paginas": 1, "removidas": {}, "stats": {}},
             "elementos": [{"t": "p", "text": "Anotem os resultados:"},
                           {"t": "contas", "rows": [["1 : 10 =", "25 : 10 ="], ["5 : 10 =", ""]]},
                           {"t": "p", "text": "O que vocês observam?"}]}
    (tmp_path / "img").mkdir()
    (tmp_path / "modelo.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
    FR.renderizar(tmp_path, model)
    doc = Document(next(tmp_path.glob("*.docx")))
    t = doc.tables[-1]
    assert [[c.text for c in r.cells] for r in t.rows] == [["1 : 10 =", "25 : 10 ="], ["5 : 10 =", ""]]
    paras = [p.text for p in doc.paragraphs if p.text]
    assert "Anotem os resultados:" in paras and "O que vocês observam?" in paras
