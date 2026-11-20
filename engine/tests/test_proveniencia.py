"""Revisao 2026-11-20, A5: texto criado pelo motor (identificacao, sumario) nao entra na comparacao."""
import json

import pymupdf
from docx import Document

from docredux_engine import gerado
from docredux_engine.ficha import render as FR
from docredux_engine.verify import ficha, relatorio as VR


def _original(tmp_path, texto, nome="2.pdf"):
    pdf = tmp_path / nome
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 100), texto)
        d.save(pdf)
    return pdf


def _modelo(texto, origem="2.pdf"):
    return {"meta": {"origem": origem, "paginas": 1, "removidas": {}, "stats": {
        "linhas_resposta_orig": 0, "imagens": 0, "tabelas": 0, "quadros": 0}},
        "elementos": [{"t": "id", "text": "MATEMÁTICA · 2º ANO", "arquivo": origem},
                      {"t": "p", "text": texto}]}


def test_sonda_do_revisor_linha_de_identificacao_sem_marca(tmp_path):
    """Sonda A5 do revisor (DOCX montado a mao, sem a marca do render)."""
    pdf = _original(tmp_path, "A resposta correta e 2 lados.")
    doc = Document()
    doc.add_paragraph("A resposta correta e 9 lados.")
    doc.add_paragraph("Versao compacta gerada a partir de: 2.pdf")
    doc.save(tmp_path / "2_COMPACTO.docx")
    model = _modelo("A resposta correta e 9 lados.")
    model["elementos"] = model["elementos"][1:]
    rep = ficha.verificar(pdf, tmp_path, model)
    assert rep["cobertura_texto_pct"] == 100
    assert not rep["numeros"]["ok"] and rep["numeros"]["faltando"] == {"2": 1}


def test_identificacao_do_render_nao_mascara_numero_perdido(tmp_path):
    pdf = _original(tmp_path, "A resposta correta e 2 lados.")
    model = _modelo("A resposta correta e 9 lados.")
    (tmp_path / "img").mkdir()
    FR.renderizar(tmp_path, model)
    rep = ficha.verificar(pdf, tmp_path, model)
    assert not rep["numeros"]["ok"] and rep["numeros"]["faltando"] == {"2": 1}
    assert rep["texto_gerado_fora"] == ["MATEMÁTICA · 2º ANO", "Versão compacta gerada a partir de: 2.pdf"]


def test_controle_numero_preservado_continua_ok(tmp_path):
    pdf = _original(tmp_path, "A resposta correta e 2 lados.")
    model = _modelo("A resposta correta e 2 lados.")
    (tmp_path / "img").mkdir()
    FR.renderizar(tmp_path, model)
    assert ficha.verificar(pdf, tmp_path, model)["numeros"]["ok"]


def test_completude_do_pdf_continua_cobrando_o_texto_gerado(tmp_path):
    """O PDF precisa trazer tudo o que o DOCX tem, inclusive a identificacao."""
    model = _modelo("Texto do corpo.")
    (tmp_path / "img").mkdir()
    FR.renderizar(tmp_path, model)
    docx = next(tmp_path.glob("*.docx"))
    assert "compacta" not in ficha.docx_words(docx)
    assert ficha.docx_words(docx, incluir_gerado=True)["compacta"] == 1


def test_relatorio_separa_run_marcado_em_paragrafo_e_em_celula(tmp_path):
    doc = Document()
    p = doc.add_paragraph()
    r1 = p.add_run("3 INTRODUÇÃO"); r2 = p.add_run("\t17")
    gerado.marcar(doc, r1, r2)  # linha do sumario
    doc.add_paragraph("Em 2023 foram 17 escolas.")
    c = doc.add_table(rows=1, cols=1).rows[0].cells[0]
    gerado.marcar(doc, c.paragraphs[0].add_run("Versão compacta gerada a partir de: r.pdf"))
    doc.save(tmp_path / "r.docx")
    textos, ger = VR.docx_partes(tmp_path / "r.docx")
    assert [t for t in textos if t] == ["Em 2023 foram 17 escolas."]
    assert ger == ["3 INTRODUÇÃO\t17", "Versão compacta gerada a partir de: r.pdf"]
    assert len(VR.docx_textos(tmp_path / "r.docx", incluir_gerado=True)) == len(textos) + 2


def test_estilo_criado_uma_vez_e_oculto(tmp_path):
    doc = Document()
    gerado.marcar(doc, doc.add_paragraph().add_run("a"))
    gerado.marcar(doc, doc.add_paragraph().add_run("b"))
    st = [s for s in doc.styles if s.name == gerado.ESTILO]
    assert len(st) == 1 and st[0].style_id == gerado.ESTILO_ID and st[0].hidden
