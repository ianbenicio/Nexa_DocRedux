"""M12 (story 2.13): número certo vindo de outro lugar não compensa número trocado no lugar de origem."""
import pymupdf
import pytest
from docx import Document

from docredux_engine.verify import ficha, numeros


def _v(original, saida):
    return numeros.verificar(original, saida)


@pytest.mark.parametrize("original,saida", [
    # valores trocados entre duas questões: a contagem global é a mesma
    (["Questão 1: o quadrado tem 4 lados.", "Questão 2: o cubo tem 6 faces."],
     ["Questão 1: o quadrado tem 6 lados.", "Questão 2: o cubo tem 4 faces."]),
    # alternativas com valores trocados
    (["a) 12 metros", "b) 15 metros"], ["a) 15 metros", "b) 12 metros"]),
    # número do corpo compensado por um número de outra frase (A5 genérico)
    (["A resposta correta é 2 lados.", "Página da turma 9 de maio."],
     ["A resposta correta é 9 lados.", "Página da turma 2 de maio."]),
])
def test_numero_em_outro_lugar_e_detectado(original, saida):
    r = _v(original, saida)
    assert not r["faltando"], "a contagem global não vê esta troca"
    assert not r["ok"] and r["fora_de_contexto"] >= 1 and r["fora_de_contexto_trechos"]


@pytest.mark.parametrize("original,saida", [
    # quebra de linha diferente (linha do PDF -> parágrafo do DOCX)
    (["Em 2004 a cobertura era de", "17% e subiu para 37% em", "2022."], ["Em 2004 a cobertura era de 17% e subiu para 37% em 2022."]),
    # selo "Aula 5" mudou de lugar em relação ao título
    (["Gráfico setorial", "Aula 5", "1. Observe o gráfico."], ["Aula 5", "Gráfico setorial", "1. Observe o gráfico."]),
    # tabela lida por linha no original e por coluna no DOCX: fica com a contagem global
    (["Formação de professor", "1.171", "1.194", "2,0%", "Biologia", "8.607", "6.469", "24,8%"],
     ["Formação de professor", "Biologia", "1.171 8.607", "1.194 6.469", "2,0% 24,8%"]),
    # gabarito numerado
    (["Sua resenha", "1 2 3 4 5"], ["Sua resenha", "1", "2", "3", "4", "5"]),
    # URL partida em outra posição
    (["Disponível em: https://x.org/a?b=%5b%7b%22idx%22%3a0%2c%22toggled", "list%22%3a%5b0%5d"],
     ["Disponível em: https://x.org/a?b=%5b%7b%22idx%22%3a0%2c%22toggledlist%22%3a%5b0%5d"]),
])
def test_mudanca_de_layout_sem_troca_passa(original, saida):
    r = _v(original, saida)
    assert r["ok"] and r["fora_de_contexto"] == 0, r


def test_troca_dentro_de_tabela_continua_so_com_a_contagem_global():
    """Limite conhecido: célula numérica entre números não tem vizinho de texto para ancorar."""
    r = _v(["Ano 2022", "10 20 30"], ["Ano 2022", "30 20 10"])
    assert r["ok"]


def test_sequencia_normaliza_como_tokens():
    toks, ini = numeros.sequencia(["Pinte 3 / 4 da figura, 25 % e 5ᵒ ano.", "Fim 2"])
    assert toks == ["pinte", "3/4", "da", "figura", "25%", "e", "5º", "ano", "fim", "2"] and ini == {0, 8}


def test_numero_em_pedaco_de_url_fica_com_a_contagem_global():
    toks, _ = numeros.sequencia(["view/86203/81685 e saude-2024-brasil, COVID-19 e 5 lados"])
    assert [t for t in toks if t[0] in "~0123456789"] == ["~86203/81685", "~2024", "~19", "5"]


def test_ficha_ponta_a_ponta(tmp_path):
    pdf = tmp_path / "F.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        p.insert_text((72, 100), "Questao 1: o quadrado tem 4 lados.")
        p.insert_text((72, 130), "Questao 2: o cubo tem 6 faces.")
        d.save(pdf)
    doc = Document()
    doc.add_paragraph("Questao 1: o quadrado tem 6 lados.")
    doc.add_paragraph("Questao 2: o cubo tem 4 faces.")
    doc.save(tmp_path / "F_COMPACTO.docx")
    model = {"meta": {"origem": "F.pdf", "paginas": 1, "removidas": {}, "stats": {
        "linhas_resposta_orig": 0, "imagens": 0, "tabelas": 0, "quadros": 0}}, "elementos": []}
    rep = ficha.verificar(pdf, tmp_path, model, docx_path=tmp_path / "F_COMPACTO.docx")
    assert rep["cobertura_texto_pct"] == 100 and not rep["numeros"]["faltando"]
    assert not rep["numeros"]["ok"] and rep["numeros"]["fora_de_contexto"] == 2
