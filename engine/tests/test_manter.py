"""Opção manter_capa_elementos (story 2.5): desliga R1/R44 (capa, folha de rosto, fotos), R5 (decorativos)
e R12 (cabeçalho corrido) nos dois pipelines, sem derrubar a cobertura."""
import json

import pymupdf
import pytest

from conftest import fixture_pdf
from docredux_engine import api
from docredux_engine.api import Opcoes

CAB_01 = "FICHA DO ESTUDANTE · LÍNGUA PORTUGUESA · 5º ANO · PERCURSO 3"


def _rodar(n, perfil, out, manter):
    res = api.processar(fixture_pdf(n), Opcoes(manter_capa_elementos=manter), perfil, out)
    modelo = json.loads((out / "modelo.json").read_text(encoding="utf-8"))
    return res, modelo


def test_desligada_nao_muda_o_modelo(tmp_path, perfil_por_nome):
    _, m = _rodar("03", perfil_por_nome["mathema-saeb"], tmp_path, False)
    assert not {"manter_capa_elementos", "paginas_imagem", "cabecalho"} & set(m["meta"])
    assert not any(e["t"] == "pagina" for e in m["elementos"])


def test_ficha_ex01(tmp_path, perfil_por_nome):
    res, m = _rodar("01", perfil_por_nome["mathema-saeb"], tmp_path, True)
    r = res.relatorio
    assert m["meta"]["paginas_imagem"] == {"1": "capa", "52": "contracapa"}
    assert set(m["meta"]["removidas"].values()) == {"branca"}             # páginas em branco continuam saindo
    assert m["elementos"][0]["t"] == "pagina" and m["elementos"][0]["pagina"] == 1   # capa antes da identificação
    assert m["elementos"][-1]["t"] == "pagina" and m["elementos"][-1]["pagina"] == 52
    assert m["meta"]["stats"].get("desenhos_decorativos_mantidos", 0) > 0 and not m["meta"]["stats"].get("desenhos_decorativos")
    assert round(r.cobertura * 100, 2) >= 99.96 and r.gate == "ok"        # AC 2: cobertura não cai
    assert r.paginas_depois > 19
    with pymupdf.open(res.pdf) as d:
        assert d[0].get_images() and len(d[0].get_text().split()) < 30     # 1a página = capa (imagem)
        assert all(CAB_01 in d[i].get_text() for i in range(1, d.page_count))  # cabeçalho em todas
    descr = " ".join(x.descricao for x in r.modificacoes)
    assert "2 páginas mantidas como imagem" in descr and "cabeçalho corrido" in descr
    assert "R1" not in r.regras_disparadas


def test_relatorio_ex05(tmp_path, perfil_por_nome):
    res, m = _rodar("05", perfil_por_nome["mec-sase-relatorio"], tmp_path, True)
    r = res.relatorio
    img = m["meta"]["paginas_imagem"]
    assert img["1"] == "foto" and img["2"] == "foto_ou_arte"
    assert set(m["meta"]["removidas"].values()) == {"sem_conteudo"}       # só páginas sem conteúdo saem
    assert [e["t"] for e in m["elementos"][:3]] == ["pagina", "pagina", "id"]
    assert round(r.cobertura * 100, 2) >= 99.97 and r.gate == "ok"
    with pymupdf.open(res.pdf) as d:
        assert d[0].get_images()
        assert "Documento Diagnóstico da Educação Nacional" in d[5].get_text()
    assert r.paginas_depois > 142


def test_sintese_com_capa(tmp_path, perfil_por_nome):
    """As duas opções juntas: a síntese mantém as páginas-imagem como elementos não textuais."""
    from docredux_engine.ficha.extract import extrair
    from docredux_engine.sintese import documento as D
    extrair(fixture_pdf("05"), tmp_path, perfil_por_nome["mec-sase-relatorio"], manter_capa_elementos=True)
    m = json.loads((tmp_path / "modelo.json").read_text(encoding="utf-8"))
    novo, rel = D.sintetizar(m, 0.2)
    assert [e["t"] for e in novo["elementos"]].count("pagina") == len(m["meta"]["paginas_imagem"])
    assert abs(rel["fracao_real"] - 0.2) <= 0.01
