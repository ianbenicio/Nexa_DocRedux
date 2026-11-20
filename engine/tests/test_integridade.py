"""Story 2.6: regressao dos erros objetivos, sem depender do LibreOffice."""
from collections import Counter
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from docx import Document
import pymupdf
import pytest

from docredux_engine import api, pipeline
from docredux_engine.ficha import extract
from docredux_engine.sintese import documento, extrativa
from docredux_engine.verify import ficha


def pdf_texto(path, texto="Um documento com texto suficiente para esta verificacao."):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 100), texto)
        pdf.save(path)
    return path


@pytest.mark.parametrize("manter", [False, True])
@pytest.mark.parametrize("visual", ["vetor", "raster", "linha"])
def test_preserva_conteudo_visual_sem_texto(tmp_path, perfil_por_nome, manter, visual):
    path = tmp_path / "figura.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 100), "Observe a figura na pagina seguinte e responda a atividade.")
        p = pdf.new_page()
        if visual == "vetor":
            p.draw_circle((200, 250), 60, fill=(0.2, 0.5, 0.8))
        elif visual == "linha":
            p.draw_line((100, 250), (300, 250), width=2)
        else:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
            pix.set_rect(pix.irect, (10, 100, 200))
            p.insert_image(pymupdf.Rect(100, 100, 300, 300), pixmap=pix)
        pdf.save(path)
    out = tmp_path / "saida"
    meta = extract.extrair(path, out, perfil_por_nome["mathema-saeb"], manter_capa_elementos=manter)
    model = json.loads((out / "modelo.json").read_text(encoding="utf-8"))
    assert 2 not in meta["removidas"]
    assert meta["paginas_imagem"][2] == "conteudo_visual"
    paginas = [e for e in model["elementos"] if e["t"] == "pagina" and e["pagina"] == 2]
    assert len(paginas) == 1 and (out / "img" / paginas[0]["file"]).exists()


@pytest.mark.parametrize("tipo", ["branca", "fundo", "fora", "sangria", "sangria_topo", "sangria_imagem"])
def test_nao_promove_fundo_ou_objeto_fora_da_pagina_a_conteudo(tipo):
    with pymupdf.open() as pdf:
        p = pdf.new_page()
        if tipo in ("fundo", "sangria", "sangria_topo", "sangria_imagem"):
            p.draw_rect(p.rect, fill=(1, 0.95, 0.66), color=None)
        if tipo == "fora":
            p.draw_rect(pymupdf.Rect(p.rect.x1, 100, p.rect.x1 + 100, 200), fill=(0, 0, 1))
        if tipo == "sangria":  # ex. 01/02, p. 2: filete de 0,28 pt na borda direita
            p.draw_rect(pymupdf.Rect(p.rect.x1 - 0.28, 0, p.rect.x1, 158), fill=(0.92, 0.3, 0.43), color=None)
        if tipo == "sangria_topo":
            p.draw_rect(pymupdf.Rect(0, 0, p.rect.x1, 2), fill=(0.92, 0.3, 0.43), color=None)
        if tipo == "sangria_imagem":
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 40), False)
            pix.set_rect(pix.irect, (230, 80, 110))
            p.insert_image(pymupdf.Rect(p.rect.x1 - 1, 0, p.rect.x1 + 5, 300), pixmap=pix)
        assert not extract.has_visual_content(p)
        assert extract.is_cover_or_blank(p, 0, 1) == "branca"


def _arte_pagina_inteira(page, cor=(30, 90, 160)):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 80), False)
    pix.set_rect(pix.irect, cor)
    page.insert_image(page.rect, pixmap=pix)


@pytest.mark.parametrize("manter", [False, True])
def test_capa_so_com_imagem_segue_r1(tmp_path, perfil_por_nome, manter):
    """Capa/contracapa sem texto, so com arte de pagina inteira, e R1 (nao M01); no meio do documento e conteudo."""
    path = tmp_path / "capa.pdf"
    with pymupdf.open() as pdf:
        _arte_pagina_inteira(pdf.new_page())                       # p. 1: capa so imagem
        pdf.new_page().insert_text((72, 100), "Leia o texto e responda as questoes da atividade.")
        _arte_pagina_inteira(pdf.new_page(), (200, 120, 40))       # p. 3: arte no meio, sem texto
        pdf.new_page().insert_text((72, 100), "Continue a atividade nesta pagina com atencao.")
        _arte_pagina_inteira(pdf.new_page(), (90, 30, 30))         # p. 5: contracapa so imagem
        pdf.save(path)
    out = tmp_path / "saida"
    meta = extract.extrair(path, out, perfil_por_nome["mathema-saeb"], manter_capa_elementos=manter)
    if manter:
        assert meta["paginas_imagem"] == {1: "capa", 3: "conteudo_visual", 5: "contracapa"}
        assert meta["removidas"] == {}
    else:
        assert meta["removidas"] == {1: "capa", 5: "contracapa"}
        assert meta["paginas_imagem"] == {3: "conteudo_visual"}


def test_capa_vetorial_sem_texto_segue_r1():
    with pymupdf.open() as pdf:
        p = pdf.new_page()
        for i in range(520):
            p.draw_rect(pymupdf.Rect(20 + i % 26 * 20, 20 + i // 26 * 20, 35 + i % 26 * 20, 35 + i // 26 * 20),
                        fill=(0.2, 0.4, 0.7), color=None)
        assert extract.is_cover_or_blank(p, 0, 3) == "capa"
        assert extract.is_cover_or_blank(p, 1, 3) is None  # mesma arte no meio: conteudo


@pytest.mark.parametrize("posicao", ["margem", "atravessa"])
def test_desenho_perto_da_borda_continua_conteudo(posicao):
    """A faixa de sangria e estreita: linha a 10 pt do corte, ou objeto que entra na pagina, e conteudo."""
    with pymupdf.open() as pdf:
        p = pdf.new_page()
        if posicao == "margem":
            p.draw_line((p.rect.x1 - 10, 100), (p.rect.x1 - 10, 400), width=1)
        else:
            p.draw_rect(pymupdf.Rect(p.rect.x1 - 40, 100, p.rect.x1 + 10, 200), fill=(0, 0, 1))
        assert extract.has_visual_content(p)
        assert extract.is_cover_or_blank(p, 0, 1) is None


@pytest.mark.parametrize("rotacao", [0, 90, 180, 270])
def test_conteudo_visual_em_pagina_rotacionada(rotacao):
    with pymupdf.open() as pdf:
        p = pdf.new_page(width=300, height=800)
        p.draw_circle((150, 700), 40, fill=(0.2, 0.5, 0.8))
        p.set_rotation(rotacao)
        assert extract.has_visual_content(p)
        assert extract.is_cover_or_blank(p, 0, 1) is None


def test_conta_paragrafos_quebras_runs_e_tabela_aninhada(tmp_path):
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    cell = table.cell(0, 0).merge(table.cell(0, 1))
    cell.text = "Primeira"
    cell.add_paragraph("Segunda")
    p = cell.add_paragraph()
    p.add_run("Ter").bold = True
    p.add_run("ceira")
    cell.add_paragraph("Quarta\nQuinta\tSexta")
    cell.add_table(rows=1, cols=1).cell(0, 0).text = "Interna"
    path = tmp_path / "celulas.docx"
    doc.save(path)
    assert ficha.docx_words(path) == Counter({w: 1 for w in (
        "primeira", "segunda", "terceira", "quarta", "quinta", "sexta", "interna")})


@pytest.mark.parametrize("texto", [
    'O autor afirmou: "O ensino melhorou." A pesquisa confirmou o resultado.',
    'O autor afirmou: “O ensino melhorou.” A pesquisa confirmou o resultado.',
    'O autor afirmou: (O ensino melhorou.) A pesquisa confirmou o resultado.',
    'O autor afirmou: "O ensino melhorou!" A pesquisa confirmou o resultado.',
    'O autor afirmou: “O ensino melhorou?” A pesquisa confirmou o resultado.',
    'O autor afirmou: ("O ensino melhorou.") A pesquisa confirmou o resultado.',
    'O Dr. Silva verificou o art. 5. A pesquisa confirmou o resultado.',
])
def test_segmentacao_preserva_pontuacao_e_texto(texto):
    assert " ".join(extrativa.dividir_frases(texto)) == texto


@pytest.mark.parametrize("fracao", [0.204, 0.346, 0.504, 0.3, math.nan, math.inf, None, True, "0.2"])
def test_recusa_fracao_fora_do_contrato(perfil_por_nome, fracao):
    with pytest.raises(api.MotorErro):
        api._checar(api.Opcoes(modo="sintese", fracao=fracao), perfil_por_nome["mec-sase-relatorio"])


@pytest.mark.parametrize("fracao", [0.2, 0.35, 0.5])
def test_aceita_as_tres_fracoes_publicas(perfil_por_nome, fracao):
    api._checar(api.Opcoes(modo="sintese", fracao=fracao), perfil_por_nome["mec-sase-relatorio"])


@pytest.mark.parametrize("paginas", [[0], [-1], [2], [], [True], [1.0]])
def test_folha_recusa_indices_invalidos(tmp_path, paginas):
    pdf = pdf_texto(tmp_path / "entrada.pdf")
    with pytest.raises(ValueError, match="paginas"):
        api.folha_contato(pdf, tmp_path / "folha", paginas=paginas)
    assert not (tmp_path / "folha").exists()


def test_folha_recusa_dpi_invalido(tmp_path):
    pdf = pdf_texto(tmp_path / "entrada.pdf")
    with pytest.raises(ValueError, match="DPI"):
        api.folha_contato(pdf, tmp_path / "folha", dpi=0)


def test_verifica_o_arquivo_atual_mesmo_com_outro_docx_na_pasta(tmp_path):
    texto = "Conteudo correto do documento original para verificacao completa."
    pdf = pdf_texto(tmp_path / "Z_ATUAL.pdf", texto)
    stale = Document(); stale.add_paragraph("Documento antigo.")
    stale.save(tmp_path / "A_ANTIGO_COMPACTO.docx")
    atual = Document(); atual.add_paragraph(texto)
    atual.save(tmp_path / "Z_ATUAL_COMPACTO.docx")
    meta = {"origem": pdf.name, "paginas": 1, "removidas": {}, "stats": {
        "linhas_resposta_orig": 0, "imagens": 0, "tabelas": 0, "quadros": 0}}
    rep = ficha.verificar(pdf, tmp_path, {"meta": meta, "elementos": [{"t": "p", "text": texto}]})
    assert rep["cobertura_texto_pct"] == 100


def test_contagem_dt21_reaproveita_modelo_sem_nova_extracao(tmp_path, perfil_por_nome, monkeypatch):
    texto = "Conteudo correto do documento original para verificacao completa."
    pdf = pdf_texto(tmp_path / "entrada.pdf", texto)
    model = {"meta": {"removidas": {}}, "elementos": []}

    def proibida(*args, **kwargs):
        raise AssertionError("Nao deve extrair outra vez")

    monkeypatch.setattr(extract, "extrair", proibida)
    assert api.contar_palavras(pdf, perfil_por_nome["mathema-saeb"], modelo=model) == 8


def test_relatorio_sintese_usa_modelo_original_na_contagem(tmp_path, perfil_por_nome, monkeypatch):
    pdf = pdf_texto(tmp_path / "entrada.pdf")
    docx = tmp_path / "saida.docx"
    d = Document(); d.add_paragraph("Conteudo"); d.save(docx)
    meta = {"tipo": "relatorio", "paginas": 1, "origem": pdf.name, "stats": {}, "removidas": {}}
    model = {"meta": meta, "elementos": [{"t": "p", "runs": [[
        "A primeira frase explica o assunto tratado no relatorio. A segunda frase apresenta outra conclusao do estudo.", 0]], "pg": 1}]}

    def rodar(*args, **kwargs):
        novo = kwargs["transformar"](model)
        return {"meta": meta, "modelo": novo, "verificacao": None, "render": {},
                "docx": docx, "pdf": tmp_path / "saida.pdf", "paginas_pdf": 1, "tempos_s": {}}

    chamadas = []

    def contar(path, perfil, *, modelo):
        chamadas.append(modelo)
        return 91037

    monkeypatch.setattr(api.pipeline, "rodar", rodar)
    monkeypatch.setattr(api, "contar_palavras", contar)
    result = api.processar(pdf, api.Opcoes(modo="sintese", fracao=0.35), perfil_por_nome["mec-sase-relatorio"], tmp_path)
    assert result.relatorio.palavras_original == 91037
    assert chamadas == [model]


def test_aviso_informa_resultado_quando_nucleo_impede_alvo():
    model = {"meta": {"tipo": "relatorio", "origem": "teste.pdf"}, "elementos": [
        {"t": "h1", "text": "Diagnostico"}, {"t": "h2", "text": "PROBLEMA"},
        {"t": "p", "runs": [["O problema central e a baixa conclusao na idade certa.", 0]], "pg": 1}]}
    _, rep = documento.sintetizar(model, 0.2)
    assert rep["fracao_real"] == 1.0
    assert "100%" in rep["aviso"] and "20% inviável" in rep["aviso"]


def test_conversao_para_pdf_vazio_falha(tmp_path, perfil_por_nome, monkeypatch):
    pdf = pdf_texto(tmp_path / "entrada.pdf")

    def converter(docx, out):
        path = Path(out) / (Path(docx).stem + ".pdf")
        with pymupdf.open() as d:
            d.new_page(); d.save(path)
        return path

    monkeypatch.setattr(pipeline.soffice, "para_pdf", converter)
    with pytest.raises(api.RenderFalhou, match="sem conteúdo visível"):
        api.processar(pdf, api.Opcoes(), perfil_por_nome["mathema-saeb"], tmp_path / "out")


def test_conversao_truncada_com_codigo_zero_falha(tmp_path, perfil_por_nome, monkeypatch):
    """Revisão 2026-11-20, A2: PDF que perdeu texto do DOCX não é entregue, mesmo com conversão 'ok'."""
    pdf = pdf_texto(tmp_path / "entrada.pdf")

    def converter(docx, out):
        path = Path(out) / (Path(docx).stem + ".pdf")
        with pymupdf.open() as d:
            d.new_page().insert_text((72, 100), "Saida parcial.")
            d.save(path)
        return path

    monkeypatch.setattr(pipeline.soffice, "para_pdf", converter)
    with pytest.raises(api.RenderFalhou, match="PDF incompleto"):
        api.processar(pdf, api.Opcoes(), perfil_por_nome["mathema-saeb"], tmp_path / "out")


def _docx_e_pdf(tmp_path, texto_docx, texto_pdf):
    from docx import Document
    docx = tmp_path / "s.docx"
    d = Document()
    for t in texto_docx:
        d.add_paragraph(t)
    d.save(docx)
    pdf = tmp_path / "s.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        for i, t in enumerate(texto_pdf):
            p.insert_text((72, 100 + 14 * i), t)
        d.save(pdf)
    return docx, pdf


def test_pdf_completo_passa_na_completude(tmp_path):
    linhas = ["Resolva a atividade com calma.", "A resposta correta tem 12 lados e 30 arestas."]
    docx, pdf = _docx_e_pdf(tmp_path, linhas, linhas)
    assert pipeline.validar_pdf_renderizado(pdf, docx) == 1


def test_pdf_sem_parte_do_docx_falha_na_completude(tmp_path):
    linhas = [f"Questao {i}: calcule o valor pedido no enunciado." for i in range(20)]
    docx, pdf = _docx_e_pdf(tmp_path, linhas, linhas[:19])  # última linha sumiu: 95%
    with pytest.raises(RuntimeError, match="PDF incompleto"):
        pipeline.validar_pdf_renderizado(pdf, docx)


def test_pdf_com_imagem_informativa_e_aceito(tmp_path):
    path = tmp_path / "imagem.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.set_rect(pix.irect, (10, 100, 200))
        p.insert_image(pymupdf.Rect(100, 100, 300, 300), pixmap=pix)
        d.save(path)
    assert pipeline.validar_pdf_renderizado(path) == 1


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_cli_json_independe_do_encoding_do_host(tmp_path, encoding):
    path = tmp_path / "perfil.json"
    path.write_text(json.dumps({"nome": "x", "tipo": "livro"}), encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING=encoding)
    run = subprocess.run([sys.executable, "-m", "docredux_engine", "validar-perfil", str(path)],
                         cwd=Path(api.__file__).resolve().parents[1], env=env, capture_output=True, timeout=30)
    assert run.returncode == 0
    resposta = json.loads(run.stdout.decode("utf-8"))
    assert resposta["valido"] is False and any("obrigat" in e for e in resposta["erros"])
