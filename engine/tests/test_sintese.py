"""Síntese extrativa do documento inteiro (story 2.4).

Modelo sintético para as regras; ex. 05 (DOCREDUX_FIXTURES) para fração, inclusão, núcleo, figuras e tempo.
"""
import json
import re
import time

import pytest

from conftest import fixture_pdf
from docredux_engine import api
from docredux_engine.api import Opcoes
from docredux_engine.sintese import documento as D

SUP, I = 4, 2


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _texto(e):
    return "".join(r[0] for r in e["runs"] if not r[1] & SUP)


def _trechos(novo):
    """Trechos de texto da síntese (sem aviso, sem marcador de página, cortados em […])."""
    for e in novo["elementos"]:
        if e.get("sintese"):
            txt = re.sub(r"\s*\[p\. [\d–]+\]$", "", "".join(r[0] for r in e["runs"]))
            for parte in txt.split(D.RETICENCIAS):
                if _norm(parte):
                    yield _norm(parte)


def _texto_original(model):
    return _norm(" ".join(_texto(e) for e in model["elementos"] if "runs" in e))


def _p(txt, pg, nota=False):
    runs = [[txt, 0]] + ([["7", SUP]] if nota else [])
    return {"t": "p", "runs": runs, "pg": pg}


def _frases(n, tema):
    return " ".join(f"A {tema} número {i} cresceu {i},5% entre 2015 e 2022 nas redes municipais de ensino."
                    for i in range(n))


@pytest.fixture
def modelo():
    E = [
        {"t": "id", "text": "Relatório de teste", "arquivo": "teste.pdf"},
        {"t": "h1", "text": "1) CAPÍTULO", "secao": "capitulo"},
        {"t": "h2", "text": "ANÁLISE DESCRITIVA", "secao": None},
        _p(_frases(8, "matrícula"), 10, nota=True),
        _p(_frases(8, "frequência escolar"), 11),
        {"t": "fig", "caption": "Gráfico 1 – Matrículas", "body": None, "fontes": []},
        _p(_frases(8, "taxa de conclusão"), 12),
        {"t": "h2", "text": "PROBLEMA", "secao": None},
        _p("O problema central é a baixa conclusão na idade certa.", 13),
        {"t": "h2", "text": "CAUSAS CRÍTICAS", "secao": None},
        {"t": "bullet", "runs": [["Oferta insuficiente de vagas na educação infantil.", 0]], "pg": 13},
        {"t": "h2", "text": "NOTAS", "sintetico": True},
        {"t": "notas", "itens": [{"n": 7, "runs": [["Nota de rodapé.", 0]]}]},
        {"t": "h2", "text": "REFERÊNCIAS", "secao": None},
        {"t": "ref", "runs": [["BRASIL. Lei 13.005, 2014.", 0]], "pg": 14},
        {"t": "ref", "runs": [["INEP. Censo Escolar, 2022.", 0]], "pg": 14},
    ]
    return {"meta": {"tipo": "relatorio", "origem": "teste.pdf"}, "elementos": E}


# ---------------------------------------------------------------- regras (modelo sintético)
def test_so_relatorio(modelo):
    modelo["meta"]["tipo"] = "ficha"
    with pytest.raises(ValueError, match="relat"):
        D.sintetizar(modelo, 0.2)


def test_regras_da_sintese(modelo):
    novo, rel = D.sintetizar(modelo, 0.35)
    els = novo["elementos"]
    tipos = [e["t"] for e in els]
    # notas e referências saem e entram em `condensado`
    assert "notas" not in tipos and "ref" not in tipos
    assert not any(e["t"] == "h2" and e["text"] in ("NOTAS", "REFERÊNCIAS") for e in els)
    assert any("1 nota de rodapé" in c for c in rel["condensado"])
    assert any("2 referências bibliográficas" in c for c in rel["condensado"])
    # títulos, figura e núcleo ficam
    assert [e["text"] for e in els if e["t"] in ("h1", "h2")] == ["1) CAPÍTULO", "ANÁLISE DESCRITIVA", "PROBLEMA", "CAUSAS CRÍTICAS"]
    assert sum(e["t"] == "fig" for e in els) == 1
    saida = " ".join(_trechos(novo))
    assert "O problema central é a baixa conclusão na idade certa." in saida
    assert "Oferta insuficiente de vagas na educação infantil." in saida
    # aviso logo depois da identificação; página de origem em itálico; sem número de nota
    assert els[1].get("sintese_aviso") and "[…]" in els[1]["runs"][0][0]
    sint = [e for e in els if e.get("sintese")]
    assert all(e["runs"][-1][1] == I and re.fullmatch(r" \[p\. \d+\]", e["runs"][-1][0]) for e in sint)
    assert not any(r[1] & SUP for e in sint for r in e["runs"])
    assert any(D.RETICENCIAS in "".join(r[0] for r in e["runs"]) for e in sint)
    assert novo["meta"]["saida_sufixo"] == "SINTESE35"
    assert 0 < rel["fracao_real"] <= 0.35 + 0.01 + 1e-9


def test_inclusao(modelo):
    original = _texto_original(modelo)
    for f in (0.2, 0.35, 0.5):
        novo, _ = D.sintetizar(modelo, f)
        assert all(t in original for t in _trechos(novo))


def test_marcador_de_pagina_intervalo():
    assert D._marcador_pagina({"pg": 3}) == "[p. 3]"
    assert D._marcador_pagina({"pg": 3, "pg_fim": 4}) == "[p. 3–4]"
    assert D._marcador_pagina({}) is None


# ---------------------------------------------------------------- ex. 05, documento inteiro
@pytest.fixture(scope="module")
def modelo05(tmp_path_factory):
    from docredux_engine.ficha.extract import extrair
    out = tmp_path_factory.mktemp("ex05")
    extrair(fixture_pdf("05"), out)
    return json.loads((out / "modelo.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("fracao", [0.20, 0.35, 0.50])
def test_ex05(modelo05, fracao):
    t = time.process_time()
    novo, rel = D.sintetizar(modelo05, fracao)
    cpu = time.process_time() - t
    assert cpu <= 30, f"{cpu:.1f} s de CPU"                                    # AC 4
    assert abs(rel["fracao_real"] - fracao) <= 0.01, rel["fracao_real"]       # AC 1
    original = _texto_original(modelo05)
    trechos = list(_trechos(novo))
    fora = [t for t in trechos if t not in original]
    assert not fora, fora[:3]                                                 # AC 2
    saida = " ".join(trechos)
    unidades, _ = D._montar(modelo05["elementos"])
    nucleo = [u["txt"] for u in unidades if u["secao"] in ("PROBLEMA", "CAUSAS CRÍTICAS")]
    assert nucleo and all(_norm(f) in saida for f in nucleo)                  # AC 3: núcleo inteiro
    figs = lambda m: [(e["caption"], json.dumps(e["body"], sort_keys=True)) for e in m["elementos"] if e["t"] == "fig"]
    assert figs(novo) == figs(modelo05)                                       # AC 3: figuras e tabelas
    sint = [e for e in novo["elementos"] if e.get("sintese")]
    assert sint and all(re.fullmatch(r" \[p\. [\d–]+\]", e["runs"][-1][0]) for e in sint)  # AC 5


def test_processar_sintese_ex05(tmp_path, perfil_por_nome):
    pdf = fixture_pdf("05")
    res = api.processar(pdf, Opcoes(modo="sintese", fracao=0.35, docx=True), perfil_por_nome["mec-sase-relatorio"], tmp_path)
    r = res.relatorio
    assert res.pdf.exists() and res.docx.name.endswith("_SINTESE35.docx")
    assert r.cobertura is None and r.gate == "ok" and r.motivo_gate is None   # FR-13: sem gate de cobertura
    assert abs(r.fracao_real - 0.35) <= 0.01 and r.condensado
    # síntese não roda o verify: palavras_original = contagem da triagem (PDF inteiro), ver story 2.4
    assert r.palavras_original == api.triar(pdf, api.carregar_perfis()).palavras
    assert r.palavras_saida < 0.6 * r.palavras_original
    assert r.paginas_antes == 294 and r.paginas_depois < 142
    assert any(m.tipo == "sintetizado" for m in r.modificacoes)
    assert "sintese" in r.tempos_s and "verify" not in r.tempos_s
    assert (tmp_path / "modelo_sintese.json").exists()
    from docredux_engine.verify.ficha import docx_words  # noqa: F401 — garante que o DOCX abre
    import docx as python_docx
    texto = "\n".join(p.text for p in python_docx.Document(res.docx).paragraphs)
    assert D.RETICENCIAS in texto and re.search(r"\[p\. \d+", texto)
