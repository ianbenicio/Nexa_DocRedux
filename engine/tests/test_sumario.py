"""Story 2.15 (R42): sumário do relatório com PAGEREF. Uma conversão do LibreOffice em vez de duas."""
import re

import pymupdf
import pytest
from docx import Document
from docx.oxml.ns import qn

from docredux_engine import gerado, soffice
from docredux_engine.relatorio import render as RR


def _modelo(itens, corpo):
    return {"meta": {"tipo": "relatorio"},
            "elementos": [{"t": "toc", "itens": [{"text": t} for t in itens]}] + corpo}


def _h1(t):
    return {"t": "h1", "text": t}


def _p(t):
    return {"t": "p", "runs": [[t, 0]]}


def _render(tmp_path, model):
    doc = RR.RR(tmp_path).render(model)
    caminho = tmp_path / "s.docx"
    doc.save(caminho)
    return caminho


def _marcadores(docx):
    """{nome do marcador: texto do parágrafo onde está}"""
    out = {}
    for p in Document(docx).paragraphs:
        for b in p._p.iter(qn("w:bookmarkStart")):
            out[b.get(qn("w:name"))] = p.text
    return out


def _instr(docx):
    return [i.text.strip() for i in Document(docx).element.body.iter(qn("w:instrText"))]


def test_item_com_h1_vira_campo_e_item_sem_h1_nao(tmp_path):
    docx = _render(tmp_path, _modelo(["Alfa", "Sem título", "Beta"], [_h1("Alfa"), _p("texto"), _h1("Beta")]))
    assert _instr(docx) == [r"PAGEREF _DocRedux_sum0 \h", r"PAGEREF _DocRedux_sum2 \h"]
    assert _marcadores(docx) == {"_DocRedux_sum0": "Alfa", "_DocRedux_sum2": "Beta"}


def test_texto_repetido_casa_na_ordem(tmp_path):
    docx = _render(tmp_path, _modelo(["Anexo", "Anexo"], [_h1("Anexo"), _p("um"), _h1("Anexo"), _p("dois")]))
    ids = [b.get(qn("w:id")) for b in Document(docx).element.body.iter(qn("w:bookmarkStart"))]
    nomes = [b.get(qn("w:name")) for b in Document(docx).element.body.iter(qn("w:bookmarkStart"))]
    assert nomes == ["_DocRedux_sum0", "_DocRedux_sum1"] and len(set(ids)) == 2


def test_sumario_inteiro_e_texto_gerado(tmp_path):
    docx = _render(tmp_path, _modelo(["Alfa"], [_h1("Alfa")]))
    p = Document(docx).paragraphs[0]
    assert p.text.startswith("Alfa") and all(gerado.eh_gerado(r) for r in p._p.iter(qn("w:r")))


def _pdf_com_h1(caminho, paginas):
    """PDF com o título em corpo 15 (tamanho do H1) na página indicada: {texto: página}."""
    with pymupdf.open() as d:
        for _ in range(max(paginas.values())):
            d.new_page()
        for t, pg in paginas.items():
            d[pg - 1].insert_text((72, 100), t, fontsize=15)
        d.save(caminho)
    return caminho


def test_preencher_grava_o_resultado_dos_campos(tmp_path):
    model = _modelo(["Alfa", "Sem título", "Beta"], [_h1("Alfa"), _p("texto"), _h1("Beta")])
    docx = _render(tmp_path, model)
    pdf = _pdf_com_h1(tmp_path / "s.pdf", {"Alfa": 2, "Beta": 5})
    assert RR.preencher_sumario(docx, pdf, model) == {"Alfa": 2, "Beta": 5}
    linhas = [p.text for p in Document(docx).paragraphs[:3]]
    assert linhas == ["Alfa\t2", "Sem título\t", "Beta\t5"]
    assert _instr(docx) == [r"PAGEREF _DocRedux_sum0 \h", r"PAGEREF _DocRedux_sum2 \h"]  # campo continua campo


def test_titulo_repetido_grava_a_pagina_de_cada_ocorrencia(tmp_path):
    """QA 2026-11-22: o 2o "Anexo" gravava a página do 1o no DOCX (o PDF, calculado pelo PAGEREF, saía certo)."""
    model = _modelo(["Anexo", "Anexo"], [_h1("Anexo"), _p("um"), _h1("Anexo"), _p("dois")])
    docx = _render(tmp_path, model)
    pdf = tmp_path / "s.pdf"
    with pymupdf.open() as d:
        for _ in range(5):
            d.new_page()
        d[1].insert_text((72, 100), "Anexo", fontsize=15)
        d[4].insert_text((72, 100), "Anexo", fontsize=15)
        d.save(pdf)
    assert RR.paginas_sumario(pdf, ["Anexo", "Anexo"]) == [2, 5]
    assert RR.preencher_sumario(docx, pdf, model) == {"Anexo": 2}  # relatorio.json: formato de antes
    assert [p.text for p in Document(docx).paragraphs[:2]] == ["Anexo\t2", "Anexo\t5"]


def test_titulos_na_mesma_pagina_e_titulo_ausente(tmp_path):
    pdf = tmp_path / "s.pdf"
    with pymupdf.open() as d:
        d.new_page()
        d[0].insert_text((72, 100), "Alfa", fontsize=15)
        d[0].insert_text((72, 200), "Beta", fontsize=15)
        d.save(pdf)
    assert RR.paginas_sumario(pdf, ["Alfa", "Sem título", "Beta", "Alfa"]) == [1, None, 1, None]


def test_sem_sumario_nao_mexe_no_docx(tmp_path):
    model = {"meta": {"tipo": "relatorio"}, "elementos": [_h1("Alfa")]}
    docx = _render(tmp_path, model)
    antes = docx.read_bytes()
    assert RR.preencher_sumario(docx, _pdf_com_h1(tmp_path / "s.pdf", {"Alfa": 1}), model) == {}
    assert docx.read_bytes() == antes


def test_libreoffice_calcula_a_pagina_na_conversao(tmp_path):
    """Ponta a ponta: uma conversão; o número impresso no sumário é a página do H1 e cada item é link."""
    try:
        soffice.localizar()
    except soffice.SofficeNaoEncontrado:
        pytest.skip("LibreOffice ausente")
    corpo = [_h1("Primeiro capítulo")] + [_p(f"Parágrafo {i} " + "texto corrido " * 30) for i in range(40)]
    corpo += [_h1("Segundo capítulo"), _p("fim")]
    model = _modelo(["Primeiro capítulo", "Segundo capítulo"], corpo)
    docx = _render(tmp_path, model)
    pdf = soffice.para_pdf(docx, tmp_path)
    paginas = RR.find_toc_pages(pdf, ["Primeiro capítulo", "Segundo capítulo"])
    assert paginas["Segundo capítulo"] > paginas["Primeiro capítulo"]
    with pymupdf.open(pdf) as d:
        texto = d[0].get_text()
        links = [l for l in d[0].get_links() if l["kind"] == pymupdf.LINK_GOTO]
    for t, pg in paginas.items():
        assert re.search(re.escape(t) + r"[\s.…]*" + str(pg) + r"\s", texto), texto[:300]
    assert "Erro" not in texto and "Error" not in texto  # campo sem marcador: "Erro: referência não encontrada"
    assert sorted(l["page"] + 1 for l in links) == sorted(paginas.values())
