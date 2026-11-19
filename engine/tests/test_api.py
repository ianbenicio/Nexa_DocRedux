"""Contrato público (story 2.3): triar, processar, folha_contato, validar_perfil.

PDFs sintéticos são gerados em tempo de teste (nada versionado). Os ex. 01–05 vêm de DOCREDUX_FIXTURES.
"""
import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from conftest import EXEMPLOS, fixture_pdf
from docredux_engine import api
from docredux_engine.api import MotorErro, Opcoes, PerfilInvalido, Relatorio, triar

ENGINE = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- PDFs sintéticos
def _pdf_texto(path, paginas=3, fonte="helv"):
    d = pymupdf.open()
    for i in range(paginas):
        p = d.new_page()
        p.insert_text((72, 72), f"Página {i + 1} de um documento qualquer com texto suficiente para contar.",
                      fontname=fonte, fontsize=11)
    d.save(path)
    return path


@pytest.fixture
def pdf_desconhecido(tmp_path):
    return _pdf_texto(tmp_path / "desconhecido.pdf")


@pytest.fixture
def pdf_escaneado(tmp_path):
    d = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 280), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    for _ in range(2):
        d.new_page().insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
    p = tmp_path / "escaneado.pdf"
    d.save(p)
    return p


@pytest.fixture
def pdf_protegido(tmp_path):
    d = pymupdf.open(_pdf_texto(tmp_path / "base.pdf"))
    p = tmp_path / "protegido.pdf"
    d.save(p, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="segredo", owner_pw="dono")
    return p


@pytest.fixture
def pdf_corrompido(tmp_path):
    p = tmp_path / "corrompido.pdf"
    p.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog >> lixo sem xref nem paginas\n")
    return p


# ---------------------------------------------------------------- triar: recusas
def test_triar_escaneado(pdf_escaneado, perfis):
    t = triar(pdf_escaneado, perfis)
    assert t.recusa == "escaneado" and t.paginas == 2 and t.palavras == 0


def test_triar_protegido(pdf_protegido, perfis):
    assert triar(pdf_protegido, perfis).recusa == "protegido"


def test_triar_corrompido(pdf_corrompido, perfis):
    assert triar(pdf_corrompido, perfis).recusa == "corrompido"


def test_triar_template_desconhecido(pdf_desconhecido, perfis):
    t = triar(pdf_desconhecido, perfis)
    assert t.recusa is None
    assert t.perfil_id is None and t.tipo == "desconhecido" and t.sintese_disponivel is False
    assert t.paginas == 3 and t.palavras > 0 and t.tamanho_bytes == pdf_desconhecido.stat().st_size


def test_triar_excede_paginas_e_tamanho(pdf_desconhecido, perfis):
    assert triar(pdf_desconhecido, perfis, max_paginas=2).recusa == "excede_paginas"
    assert triar(pdf_desconhecido, perfis, max_bytes=100).recusa == "excede_tamanho"


def test_triagem_serializa(pdf_desconhecido, perfis):
    d = json.loads(json.dumps(triar(pdf_desconhecido, perfis).to_dict()))
    assert set(d) == {"paginas", "palavras", "tamanho_bytes", "tipo", "perfil_id", "recusa", "sintese_disponivel"}


# ---------------------------------------------------------------- triar: ex. 01–05
@pytest.mark.parametrize("n", sorted(EXEMPLOS))
def test_triar_exemplos(n, perfis):
    pdf = fixture_pdf(n)
    _, tipo, perfil = EXEMPLOS[n]
    t = triar(pdf, perfis)
    assert (t.recusa, t.tipo, t.perfil_id) == (None, tipo, perfil)
    assert t.sintese_disponivel is (tipo == "relatorio")  # Q4
    assert t.palavras > 1000


# ---------------------------------------------------------------- validar_perfil
def test_validar_perfis_semente(perfis):
    for p in perfis:
        assert api.validar_perfil(p) == [], p["nome"]


def test_validar_perfil_com_erros(perfil_por_nome):
    p = dict(perfil_por_nome["mec-sase-relatorio"])
    del p["h2"]
    p["legenda_regex"] = "(["
    erros = api.validar_perfil(p)
    assert any("'h2'" in e for e in erros) and any("legenda_regex" in e for e in erros)
    assert api.validar_perfil({"nome": "x", "tipo": "livro"})


def test_schema_e_json_schema_valido():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ENGINE / "docredux_engine" / "perfil.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    for f in (ENGINE / "perfis").glob("*.json"):
        jsonschema.validate(json.loads(f.read_text(encoding="utf-8")), schema)


# ---------------------------------------------------------------- processar: regras do contrato
def test_sintese_so_para_relatorio(tmp_path, perfil_por_nome, pdf_desconhecido):
    with pytest.raises(MotorErro, match="relat"):
        api.processar(pdf_desconhecido, Opcoes(modo="sintese", fracao=0.2), perfil_por_nome["mathema-saeb"], tmp_path)


def test_fracao_invalida(tmp_path, perfil_por_nome, pdf_desconhecido):
    with pytest.raises(MotorErro):
        api.processar(pdf_desconhecido, Opcoes(modo="sintese", fracao=0.3), perfil_por_nome["mec-sase-relatorio"], tmp_path)
    with pytest.raises(MotorErro):
        api.processar(pdf_desconhecido, Opcoes(modo="sintese"), perfil_por_nome["mec-sase-relatorio"], tmp_path)


def test_perfil_invalido(tmp_path, pdf_desconhecido):
    with pytest.raises(PerfilInvalido):
        api.processar(pdf_desconhecido, Opcoes(), {"nome": "x", "tipo": "ficha"}, tmp_path)


CAMPOS_RELATORIO = {"versao_motor", "perfil_id", "paginas_antes", "paginas_depois", "palavras_original",
                    "palavras_saida", "cobertura", "fracao_real", "condensado", "gate", "motivo_gate", "estrutura",
                    "modificacoes", "regras_disparadas", "tempos_s", "avisos"}


def test_relatorio_tem_campos_do_spec():
    assert {f.name for f in dataclasses.fields(Relatorio)} == CAMPOS_RELATORIO


def test_processar_ficha_ex03(tmp_path, perfil_por_nome):
    pdf = fixture_pdf("03")
    res = api.processar(pdf, Opcoes(docx=True), perfil_por_nome["mathema-saeb"], tmp_path)
    r = res.relatorio
    assert res.pdf.exists() and res.docx and res.docx.exists()
    assert (r.paginas_antes, r.paginas_depois) == (16, 5)
    assert r.cobertura == 1.0 and r.gate == "ok" and r.motivo_gate is None and r.fracao_real is None
    assert r.palavras_original > 0 and r.palavras_saida >= r.palavras_original * 0.99
    assert {"extracao", "render_docx", "pdf", "verify"} <= set(r.tempos_s)
    assert r.estrutura.get("questoes", 0) > 0
    assert any(m.tipo == "removido" for m in r.modificacoes)
    assert any(m.tipo == "reorganizado" for m in r.modificacoes)
    assert {"R10", "R32", "R33"} <= set(r.regras_disparadas)
    d = json.loads(r.to_json())  # serializa em JSON com todos os campos
    assert set(d) == CAMPOS_RELATORIO
    assert json.loads((tmp_path / "relatorio.json").read_text(encoding="utf-8")) == d
    # sem docx pedido, o Resultado não entrega o DOCX
    res2 = api.processar(pdf, Opcoes(), perfil_por_nome["mathema-saeb"], tmp_path / "b")
    assert res2.docx is None and res2.relatorio.paginas_depois == 5


def test_gate_bloqueia_cobertura_baixa(tmp_path, perfil_por_nome, monkeypatch):
    pdf = fixture_pdf("03")
    monkeypatch.setattr(api, "GATE_COBERTURA", 1.01)  # força o gate: 100% < 101%
    r = api.processar(pdf, Opcoes(), perfil_por_nome["mathema-saeb"], tmp_path).relatorio
    assert r.gate == "bloqueado" and r.motivo_gate.startswith("cobertura 100,00% <")


# ---------------------------------------------------------------- folha de contato
def test_folha_contato(tmp_path, pdf_desconhecido):
    arqs = api.folha_contato(pdf_desconhecido, tmp_path, paginas=[1, 3])
    assert len(arqs) == 1 and arqs[0].exists() and arqs[0].stat().st_size > 0


# ---------------------------------------------------------------- CLI (como o worker chama, DT-5)
def _cli(*args):
    r = subprocess.run([sys.executable, "-m", "docredux_engine", *map(str, args)], cwd=ENGINE,
                       capture_output=True, text=True, encoding="utf-8", timeout=600)
    linhas = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(linhas) == 1, r.stdout  # um único objeto JSON no stdout
    return r.returncode, json.loads(linhas[0])


def test_cli_triar_e_erro(pdf_desconhecido, pdf_protegido):
    cod, d = _cli("triar", pdf_desconhecido)
    assert cod == 0 and d["tipo"] == "desconhecido" and d["perfil_id"] is None
    cod, d = _cli("triar", pdf_protegido)
    assert cod == 0 and d["recusa"] == "protegido"
    cod, d = _cli("processar", pdf_protegido, pdf_protegido.parent / "out")
    assert cod == 1 and d["erro"] == "PdfRecusado" and d["motivo"] == "protegido"


def test_cli_processar_ex03(tmp_path):
    pdf = fixture_pdf("03")
    cod, d = _cli("processar", pdf, tmp_path, "--docx")
    assert cod == 0 and d["relatorio"]["paginas_depois"] == 5 and d["docx"]
