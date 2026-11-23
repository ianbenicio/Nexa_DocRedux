"""Story 2.17 (achado M12, resto): ordem de palavras, rótulo de aula, imagem↔texto e célula de tabela."""
import pymupdf
import pytest
from docx import Document
from docx.shared import Pt

from docredux_engine import api
from docredux_engine.verify import ordem


# ----------------------------------------------------------------------------------------------- palavras
def test_palavras_trocadas_na_frase_sao_detectadas():
    """Caso da auditoria (test_cobertura_nao_pode_ignorar_a_ordem_das_palavras): mesmo multiconjunto, outro sentido."""
    r = ordem.palavras(["O aluno ajuda o professor."], ["O professor ajuda o aluno."])
    assert r["total"] == 1 and r["trechos"] == ["O aluno ajuda o professor."]


def test_palavras_na_mesma_ordem_passam():
    assert ordem.palavras(["O aluno ajuda o professor."], ["O aluno ajuda o professor."])["total"] == 0


def test_blocos_reordenados_e_linhas_juntadas_passam():
    """O render muda a ordem dos blocos e junta linhas num parágrafo; a ordem dentro da linha fica."""
    orig = ["Leia o texto com atenção", "e responda às perguntas abaixo.", "AULA 3 · TIRINHA divertida hoje"]
    saida = ["AULA 3 · TIRINHA divertida hoje", "Leia o texto com atenção e responda às perguntas abaixo."]
    assert ordem.palavras(orig, saida)["total"] == 0


def test_palavra_ausente_fica_com_a_cobertura():
    """Trio com palavra que falta na saída não conta aqui: a cobertura já cobra a palavra."""
    assert ordem.palavras(["os dados do censo escolar"], ["os do censo escolar"])["total"] == 0


def test_trio_repetido_precisa_aparecer_as_mesmas_vezes():
    orig = ["o aluno ajuda o colega", "o aluno ajuda o colega"]
    assert ordem.palavras(orig, ["o aluno ajuda o colega", "o colega ajuda o aluno"])["total"] == 1


# ----------------------------------------------------------------------------------------------- rotulos
def _pdf(tmp_path, paginas, nome="orig.pdf"):
    """paginas: [[(x, y, texto)]]"""
    path = tmp_path / nome
    with pymupdf.open() as d:
        for linhas in paginas:
            p = d.new_page()
            for x, y, t in linhas:
                p.insert_text((x, y), t, fontsize=10)
        d.save(path)
    return path


@pytest.fixture
def aulas(tmp_path):
    return _pdf(tmp_path, [
        [(60, 60, "AULA 10"), (60, 80, "VAMOS CANTAR?"), (60, 120, "Leia a letra da canção.")],
        [(60, 60, "AULA 11"), (60, 80, "POEMA QUE EMBALA O SONO"), (60, 120, "Prepare-se para ler.")],
        [(60, 60, "AULAS 1 E 2"), (60, 80, "PARA COMEÇO DE CONVERSA")],
    ])


def test_rotulo_com_o_titulo_da_sua_pagina_passa(aulas):
    saida = ["AULA 10 · VAMOS CANTAR?", "AULA 11 · POEMA QUE EMBALA O SONO", "Aulas 1 E 2 · PARA COMEÇO DE CONVERSA"]
    with pymupdf.open(aulas) as doc:
        assert ordem.rotulos(doc, {}, saida)["total"] == 0


def test_selo_de_aula_trocado_e_detectado(aulas):
    """Limite da 2.13: trocar "AULA 10" com "AULA 11" passava, porque um vizinho basta na fronteira."""
    saida = ["AULA 11 · VAMOS CANTAR?", "AULA 10 · POEMA QUE EMBALA O SONO"]
    with pymupdf.open(aulas) as doc:
        r = ordem.rotulos(doc, {}, saida)
    assert r["total"] == 2 and r["trechos"][0] == "AULA 11 · VAMOS CANTAR?"


def test_selo_com_numero_que_nao_existe_no_original_e_detectado(aulas):
    with pymupdf.open(aulas) as doc:
        assert ordem.rotulos(doc, {}, ["AULA 12 · VAMOS CANTAR?", "Aulas 3 E 2 · PARA COMEÇO DE CONVERSA"])["total"] == 2


def test_rotulo_em_pagina_removida_nao_conta(aulas):
    with pymupdf.open(aulas) as doc:
        assert ordem.rotulos(doc, {"1": "capa", "2": "capa", "3": "capa"}, ["AULA 11 · VAMOS CANTAR?"])["total"] == 0


@pytest.mark.parametrize("paginas, saida", [
    ([[(60, 60, "Tabela 1"), (60, 80, "Escolas por rede")], [(60, 60, "Tabela 2 - Matrículas por região")]],
     "Tabela 2 - Matrículas por região"),
    ([[(60, 60, "QUESTÃO 1"), (60, 80, "Observe a figura.")], [(60, 60, "QUESTÃO 2 Leia o texto abaixo.")]],
     "QUESTÃO 2 Leia o texto abaixo."),
    ([[(60, 60, "Ano 2022"), (60, 80, "dados do censo")], [(60, 60, "Em 2023 houve queda. Ano 2023 houve alta.")]],
     "Ano 2023 houve alta."),
    ([[(60, 60, "AULAS 1 E 2"), (60, 80, "Leitura em voz alta")], [(60, 60, "Nas aulas 3 e 4 vamos ler poemas.")]],
     "Aulas 3 · poemas"),
], ids=["legenda-na-linha", "questao-na-linha", "ano-no-texto", "par-com-e"])
def test_numero_que_existe_no_original_dentro_da_linha_nao_e_selo_alterado(tmp_path, paginas, saida):
    """QA da 2.17: a palavra de um rótulo solto ("Tabela 1") não transforma em selo alterado o mesmo "palavra número"
    que o original traz dentro da linha."""
    with pymupdf.open(_pdf(tmp_path, paginas)) as doc:
        assert ordem.rotulos(doc, {}, [saida])["total"] == 0


# ----------------------------------------------------------------------------------------------- imagens
def _png(tmp_path, nome):
    """Bytes distintos por nome: python-docx reaproveita (e renomeia igual) imagem de conteúdo idêntico."""
    path = tmp_path / nome
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    pix.clear_with(sum(nome.encode()) % 256)
    pix.save(path)
    return path


@pytest.fixture
def duas_imagens(tmp_path):
    pdf = _pdf(tmp_path, [
        [(60, 100, "1. Observe a capa da revista em quadrinhos."), (60, 500, "Rodapé sem relação nenhuma aqui.")],
        [(60, 100, "2. Observe o gráfico de setores abaixo."), (60, 500, "Outro texto longe demais da figura.")],
    ])
    model = {"elementos": [
        {"t": "img", "file": "p01_img1.png", "bbox": [60, 120, 300, 300]},
        {"t": "img", "file": "p02_img1.png", "bbox": [60, 120, 300, 300]},
    ]}
    imgs = {n: _png(tmp_path, n) for n in ("p01_img1.png", "p02_img1.png")}
    return pdf, model, imgs


def _docx(blocos, imgs):
    d = Document()
    for b in blocos:
        if b in imgs:
            d.add_picture(str(imgs[b]), width=Pt(40))
        else:
            d.add_paragraph(b)
    return d


def _blocos(img1, img2):
    """Cada questão seguida da imagem e de duas linhas de resposta (a janela olha 2 parágrafos de cada lado)."""
    return ["1. Observe a capa da revista em quadrinhos.", img1, "a) ____", "b) ____",
            "2. Observe o gráfico de setores abaixo.", img2, "c) ____", "d) ____"]


def test_imagem_junto_da_sua_questao_passa(duas_imagens):
    pdf, model, imgs = duas_imagens
    d = _docx(_blocos("p01_img1.png", "p02_img1.png"), imgs)
    with pymupdf.open(pdf) as doc:
        assert ordem.imagens(doc, model, d)["total"] == 0


def test_imagens_trocadas_sao_detectadas(duas_imagens):
    pdf, model, imgs = duas_imagens
    d = _docx(_blocos("p02_img1.png", "p01_img1.png"), imgs)
    with pymupdf.open(pdf) as doc:
        r = ordem.imagens(doc, model, d)
    assert r["total"] == 2 and r["trechos"][0].startswith("p02_img1.png (pág. 2) junto de")


def test_imagem_sem_texto_perto_no_original_nao_conta(tmp_path):
    pdf = _pdf(tmp_path, [[(60, 780, "Texto muito abaixo da imagem.")]])
    model = {"elementos": [{"t": "img", "file": "p01_img1.png", "bbox": [60, 40, 300, 200]}]}
    d = _docx(["Qualquer coisa", "p01_img1.png"], {"p01_img1.png": _png(tmp_path, "p01_img1.png")})
    with pymupdf.open(pdf) as doc:
        assert ordem.imagens(doc, model, d)["total"] == 0


# ----------------------------------------------------------------------------------------------- tabelas
TABELA = [[(60, 100, "Região"), (200, 100, "Escolas"), (300, 100, "Percentual")],
          [(60, 120, "Norte"), (200, 120, "21.911"), (300, 120, "26,2")],
          [(60, 140, "Nordeste"), (200, 140, "12.110"), (300, 140, "14,5")],
          [(60, 160, "Sudeste"), (200, 160, "32.266"), (300, 160, "38,5")]]


def _tabela(linhas, imagem=None):
    d = Document()
    t = d.add_table(rows=len(linhas), cols=len(linhas[0]))
    for i, row in enumerate(linhas):
        for j, c in enumerate(row):
            t.cell(i, j).text = c
    if imagem:
        t.cell(0, 0).paragraphs[0].add_run().add_picture(str(imagem), width=Pt(20))
    return d


def _linhas_tabela():
    return [[t for _, _, t in row] for row in TABELA]


@pytest.fixture
def pdf_tabela(tmp_path):
    return _pdf(tmp_path, [[c for row in TABELA for c in row]])


def test_tabela_igual_ao_original_passa(pdf_tabela):
    with pymupdf.open(pdf_tabela) as doc:
        assert ordem.tabelas(doc, {}, _tabela(_linhas_tabela()))["total"] == 0


def test_celulas_trocadas_entre_linhas_sao_detectadas(pdf_tabela):
    rows = _linhas_tabela()
    rows[1][1], rows[3][1] = rows[3][1], rows[1][1]  # Norte 32.266, Sudeste 21.911
    with pymupdf.open(pdf_tabela) as doc:
        r = ordem.tabelas(doc, {}, _tabela(rows))
    assert r["total"] >= 1 and "tabela 1, linha 2, coluna 2" in r["trechos"][0]


def test_celulas_trocadas_na_mesma_linha_sao_detectadas(pdf_tabela):
    rows = _linhas_tabela()
    rows[2][1], rows[2][2] = rows[2][2], rows[2][1]  # Nordeste 14,5 | 12.110
    with pymupdf.open(pdf_tabela) as doc:
        r = ordem.tabelas(doc, {}, _tabela(rows))
    assert r["total"] >= 1 and any("ordem na linha" in t for t in r["trechos"])


def test_tabela_de_diagramacao_com_imagem_fica_com_a_checagem_de_imagens(pdf_tabela, tmp_path):
    rows = _linhas_tabela()
    rows[1][1], rows[3][1] = rows[3][1], rows[1][1]
    with pymupdf.open(pdf_tabela) as doc:
        assert ordem.tabelas(doc, {}, _tabela(rows, imagem=_png(tmp_path, "x.png")))["total"] == 0


def test_grade_so_de_numeros_ambiguos_nao_e_checada(tmp_path):
    """Limite: "1", "2", "3" aparecem em todo lugar; sem célula localizável sem ambiguidade, a tabela fica de fora."""
    pdf = _pdf(tmp_path, [[(60, 100, "1"), (60, 120, "2"), (300, 300, "1"), (300, 320, "2")]])
    with pymupdf.open(pdf) as doc:
        assert ordem.tabelas(doc, {}, _tabela([["2", ""], ["1", ""]]))["total"] == 0


# ----------------------------------------------------------------------------------------------- gate
def _processar(tmp_path, perfil, monkeypatch, verificacao):
    pdf = tmp_path / "entrada.pdf"
    with pymupdf.open() as d:
        d.new_page().insert_text((72, 100), "Texto do original.")
        d.save(pdf)
    docx = tmp_path / "saida.docx"
    Document().save(docx)
    meta = {"paginas": 1, "origem": pdf.name, "stats": {}, "removidas": {}}

    def rodar(*args, **kwargs):
        return {"meta": meta, "modelo": {"meta": meta, "elementos": []}, "verificacao": verificacao, "render": {},
                "docx": docx, "pdf": tmp_path / "saida.pdf", "paginas_pdf": 1, "tempos_s": {}}

    monkeypatch.setattr(api.pipeline, "rodar", rodar)
    (tmp_path / "out").mkdir()
    return api.processar(pdf, api.Opcoes(), perfil, tmp_path / "out").relatorio


def _ordem(**totais):
    r = {k: {"total": 0, "trechos": []} for k in ("palavras", "rotulos", "imagens", "tabelas")}
    for k, (n, trechos) in totais.items():
        r[k] = {"total": n, "trechos": trechos}
    r["ok"] = not totais
    return r


def test_ordem_divergente_bloqueia_com_trechos(tmp_path, perfil_por_nome, monkeypatch):
    v = {"cobertura_texto_pct": 100.0, "palavras_original": 10,
         "ordem": _ordem(palavras=(1, ["O aluno ajuda o professor."]), rotulos=(2, ["AULA 11 · VAMOS CANTAR?"]))}
    rel = _processar(tmp_path, perfil_por_nome["mathema-saeb"], monkeypatch, v)
    assert rel.gate == "bloqueado"
    assert rel.motivo_gate == ("ordem ou associação divergente do original: 1 palavra(s) fora de ordem: "
                               "“O aluno ajuda o professor.” | 2 rótulo(s) com outro conteúdo: "
                               "“AULA 11 · VAMOS CANTAR?”")


def test_ordem_conferida_libera(tmp_path, perfil_por_nome, monkeypatch):
    v = {"cobertura_texto_pct": 100.0, "palavras_original": 10, "ordem": _ordem()}
    rel = _processar(tmp_path, perfil_por_nome["mathema-saeb"], monkeypatch, v)
    assert rel.gate == "ok" and rel.motivo_gate is None


def test_ordem_soma_ao_motivo_da_cobertura(tmp_path, perfil_por_nome, monkeypatch):
    v = {"cobertura_texto_pct": 98.0, "palavras_original": 10,
         "ordem": _ordem(tabelas=(1, ["tabela 1, linha 2, coluna 2 (ordem na coluna): 32.266"]))}
    rel = _processar(tmp_path, perfil_por_nome["mec-sase-relatorio"], monkeypatch, v)
    assert rel.motivo_gate.startswith("cobertura 98,00% <")
    assert rel.motivo_gate.endswith("1 célula(s) fora da linha ou coluna: "
                                    "“tabela 1, linha 2, coluna 2 (ordem na coluna): 32.266”")
