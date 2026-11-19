"""Testes de caracterizacao e de comportamento desejado da auditoria do motor.

Nao integram a suite padrao. As falhas documentam problemas ainda nao corrigidos.
Executar da raiz do repositorio:
api/.venv/Scripts/python -m pytest docs/qa/motor_auditoria_repro.py -v
"""
from collections import Counter
from pathlib import Path
import os
import json
import subprocess
import sys

import pytest
import pymupdf
from docx import Document

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))

from docredux_engine import api
from docredux_engine.ficha import extract
from docredux_engine.sintese import documento, extrativa
from docredux_engine.verify import ficha, relatorio


def _pdf(path, texto="Um documento com texto suficiente para esta verificacao."):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 100), texto)
        pdf.save(path)
    return path


def _perfil(tipo):
    return next(p for p in api.carregar_perfis() if p["tipo"] == tipo)


@pytest.mark.parametrize("verificador", [ficha, relatorio])
def test_alteracao_de_valor_numerico_precisa_ser_detectada(verificador):
    original = Counter(verificador.words("A resposta correta e 2."))
    alterado = Counter(verificador.words("A resposta correta e 9."))
    assert original != alterado, "A troca de 2 por 9 e invisivel a cobertura"


def test_cobertura_nao_pode_ignorar_a_ordem_das_palavras():
    original = Counter(ficha.words("O aluno ajuda o professor."))
    alterado = Counter(ficha.words("O professor ajuda o aluno."))
    assert original != alterado, "Frases de sentidos diferentes tem cobertura identica"


def test_contagem_docx_separa_paragrafos_na_mesma_celula(tmp_path):
    doc = Document()
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "Primeira"
    cell.add_paragraph("Segunda")
    path = tmp_path / "tabela.docx"
    doc.save(path)
    assert ficha.docx_words(path) == Counter({"primeira": 1, "segunda": 1})


def test_contagem_docx_preserva_runs_da_mesma_palavra(tmp_path):
    doc = Document()
    p = doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    p.add_run("Prime").bold = True
    p.add_run("ira")
    path = tmp_path / "runs.docx"
    doc.save(path)
    assert ficha.docx_words(path) == Counter({"primeira": 1})


@pytest.mark.parametrize("manter", [False, True])
def test_pagina_com_diagrama_nao_e_branca(tmp_path, manter):
    path = tmp_path / "diagrama.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 100), "Observe o diagrama na pagina seguinte e resolva a atividade.")
        p = pdf.new_page()
        p.draw_rect(pymupdf.Rect(100, 100, 300, 300), fill=(0.2, 0.5, 0.8))
        p.draw_circle((200, 200), 60, fill=(1, 1, 1))
        pdf.save(path)
    out = tmp_path / "saida"
    meta = extract.extrair(path, out, _perfil("ficha"), manter_capa_elementos=manter)
    assert meta["removidas"].get(2) != "branca", "Pagina com figura foi descartada como branca"


def test_gate_nao_libera_estrutura_divergente(tmp_path, monkeypatch):
    docx = tmp_path / "teste_COMPACTO.docx"
    doc = Document()
    doc.add_paragraph("Conteudo preservado")
    doc.save(docx)
    meta = {"paginas": 1, "stats": {}, "removidas": {}}
    model = {"meta": meta, "elementos": [{"t": "p", "runs": [["Conteudo preservado", 0]]}]}
    res = {"meta": meta, "modelo": model, "verificacao": {
        "cobertura_texto_pct": 100, "palavras_original": 2, "estrutura_ok": False},
        "render": {}, "docx": docx, "pdf": tmp_path / "teste_COMPACTO.pdf",
        "paginas_pdf": 1, "tempos_s": {}}
    monkeypatch.setattr(api.pipeline, "rodar", lambda *a, **kw: res)
    result = api.processar(tmp_path / "entrada.pdf", api.Opcoes(), _perfil("relatorio"), tmp_path)
    assert result.relatorio.gate == "bloqueado", result.relatorio.avisos


def test_fracao_fora_do_contrato_nao_e_arredondada():
    with pytest.raises(api.MotorErro):
        api._checar(api.Opcoes(modo="sintese", fracao=0.204), _perfil("relatorio"))


def test_divisao_de_frases_preserva_as_aspas_de_fechamento():
    texto = 'O autor afirmou: "O ensino melhorou." A pesquisa confirmou esse resultado.'
    partes = extrativa.dividir_frases(texto)
    assert " ".join(partes) == texto


def test_verificador_nao_seleciona_docx_de_outra_execucao(tmp_path):
    texto = "Conteudo correto do documento original para verificacao completa."
    pdf = _pdf(tmp_path / "entrada.pdf", texto)
    stale = Document()
    stale.add_paragraph("Arquivo antigo sem relacao com o documento atual.")
    stale.save(tmp_path / "A_ANTIGO_COMPACTO.docx")
    atual = Document()
    atual.add_paragraph(texto)
    atual.save(tmp_path / "Z_ATUAL_COMPACTO.docx")
    meta = {"origem": "Z_ATUAL.pdf", "paginas": 1, "removidas": {}, "stats": {
        "linhas_resposta_orig": 0, "imagens": 0, "tabelas": 0, "quadros": 0}}
    model = {"meta": meta, "elementos": [{"t": "p", "text": texto}]}
    rep = ficha.verificar(pdf, tmp_path, model)
    assert rep["cobertura_texto_pct"] == 100, rep["palavras_faltando"]


def test_nucleo_maior_que_alvo_exige_aviso_especifico():
    model = {"meta": {"tipo": "relatorio", "origem": "teste.pdf"}, "elementos": [
        {"t": "h1", "text": "Diagnostico"},
        {"t": "h2", "text": "PROBLEMA"},
        {"t": "p", "runs": [["O problema central e a baixa conclusao dos estudantes na idade certa.", 0]], "pg": 1}]}
    _, rep = documento.sintetizar(model, 0.2)
    assert rep["fracao_real"] <= 0.21 or "100%" in rep["aviso"], rep


def test_folha_recusa_pagina_zero(tmp_path):
    pdf = _pdf(tmp_path / "entrada.pdf")
    with pytest.raises((ValueError, api.MotorErro)):
        api.folha_contato(pdf, tmp_path / "folhas", paginas=[0])


@pytest.mark.skipif(not os.environ.get("DOCREDUX_FIXTURES"), reason="Exemplo 05 externo necessario")
def test_sintese_usa_contagem_dt21_no_relatorio(tmp_path, monkeypatch):
    """Isola o contrato de metadados; extracao/render nao sao executados neste teste."""
    base = Path(os.environ["DOCREDUX_FIXTURES"]) / "05-diagnostico-educacao-nacional" / "original"
    pdf = next(base.glob("*.pdf"))
    docx = tmp_path / "teste_SINTESE35.docx"
    doc = Document()
    doc.add_paragraph("Conteudo preservado")
    doc.save(docx)
    meta = {"tipo": "relatorio", "paginas": 294, "origem": pdf.name, "stats": {}, "removidas": {}}
    model = {"meta": meta, "elementos": [
        {"t": "h1", "text": "CAPITULO"},
        {"t": "p", "runs": [["O ensino melhorou em todas as escolas municipais. Os estudantes conseguiram melhores resultados na pesquisa.", 0]], "pg": 1}]}

    def rodar(*args, **kwargs):
        novo = kwargs["transformar"](model)
        return {"meta": meta, "modelo": novo, "verificacao": None, "render": {},
                "docx": docx, "pdf": tmp_path / "teste_SINTESE35.pdf", "paginas_pdf": 1, "tempos_s": {}}

    monkeypatch.setattr(api.pipeline, "rodar", rodar)
    result = api.processar(pdf, api.Opcoes(modo="sintese", fracao=0.35), _perfil("relatorio"), tmp_path)
    # Mesmo valor do teste existente test_triar_conta_como_o_verify (DT-21).
    assert result.relatorio.palavras_original == 91037


def test_gate_verifica_conteudo_do_pdf_entregue(tmp_path, monkeypatch):
    """Injeta uma conversao defeituosa: DOCX correto, PDF sem conteudo."""
    pdf = _pdf(tmp_path / "entrada.pdf")
    out = tmp_path / "saida"

    def converter_em_branco(docx, outdir):
        alvo = Path(outdir) / (Path(docx).stem + ".pdf")
        with pymupdf.open() as d:
            d.new_page()
            d.save(alvo)
        return alvo

    monkeypatch.setattr(api.pipeline.soffice, "para_pdf", converter_em_branco)
    result = api.processar(pdf, api.Opcoes(), _perfil("ficha"), out)
    with pymupdf.open(result.pdf) as d:
        assert not any(p.get_text().strip() for p in d)
    assert result.relatorio.gate == "bloqueado", result.relatorio


def test_cli_json_compativel_com_leitor_utf8_em_ambiente_windows(tmp_path):
    """O worker decodifica stdout como UTF-8, mesmo em hosts com pagina de codigo legada."""
    perfil = tmp_path / "invalido.json"
    perfil.write_text(json.dumps({"nome": "x", "tipo": "livro"}), encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONPATH=str(ROOT / "engine"))
    r = subprocess.run([sys.executable, "-m", "docredux_engine", "validar-perfil", str(perfil)],
                       cwd=ROOT / "engine", capture_output=True, env=env, timeout=30)
    assert r.returncode == 0
    saida = json.loads(r.stdout.decode("utf-8"))
    assert saida["valido"] is False
