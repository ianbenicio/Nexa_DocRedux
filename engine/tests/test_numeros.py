"""Story 2.7 (M09): integridade numérica. Número ou sinal trocado na saída bloqueia a entrega."""
import pymupdf
import pytest
from docx import Document

from docredux_engine import api
from docredux_engine.relatorio import extract as ER
from docredux_engine.relatorio import render as RR
from docredux_engine.verify import ficha, numeros
from docredux_engine.verify import relatorio as VR


# ------------------------------------------------------------------ tokens
@pytest.mark.parametrize("texto,esperado", [
    ("a média foi 6,9 pontos", ["6,9"]),
    ("1.234.567 habitantes", ["1.234.567"]),
    ("três quartos: 3/4 e 3 / 4", ["3/4", "3/4"]),
    ("cresceu 25 % e depois 25%", ["25%", "25%"]),
    ("5º ano, 5ᵒ ano, 2ª série", ["5º", "5º", "2ª"]),
    ("saldo de -3 e de −3", ["3", "3"]),  # o sinal vira "op:-" (ver test_operadores_entre_numeros)
    ("PNE 2014-2024", ["2014", "2024"]),  # hífen de intervalo não vira sinal
    ("jan./jun.2009", ["2009"]),
    ("jan./jun. 2009", ["2009"]),
    ("arquivo pt2023v48", []),  # dígito colado a letra não é número solto
    ("calcule 3 × -2 e (−5)", ["3", "2", "5"]),  # número sem sinal; o sinal é token à parte
    ("3 -4 e 3 - 4", ["3", "4", "3", "4"]),  # depois de dígito é operador, com ou sem espaço (A4)
    ("x-3 e x - 3", ["3", "3"]),  # depois de letra é hífen, não sinal
    ("itens:\n- 3 lápis", ["3"]),  # travessão de lista separado do número
])
def test_tokens_numericos(texto, esperado):
    assert [t for t in numeros.tokens(texto) if not t.startswith("op:")] == esperado


@pytest.mark.parametrize("texto,ops", [
    ("3 + 4 = 7", ["op:+", "op:="]),
    ("3 − 4", ["op:-"]),
    ("2×5 e 2 * 5", ["op:×", "op:×"]),
    ("x ≥ 2", []),  # só conta operador entre dois números
    ("10 < 20 ≤ 30", ["op:<", "op:≤"]),
    ("1 : 10 = 0,1", ["op::", "op:="]),
    ("3 - 4 e 3-4", ["op:-", "op:-"]),  # hífen ASCII como subtração (A3)
    ("3 +4", ["op:+"]),  # espaço só de um lado (A4)
    ("3 × -2", ["op:×", "op:-"]),  # sinal unário conta uma vez
    ("saldo de -3 e de −3", ["op:-", "op:-"]),
    ("2,0% -24,8% e 12,0 -24,8%", ["op:-", "op:-"]),  # unário depois de % e operador depois de dígito: um só
    ("x-3 e x - 3", []),  # hífen colado a letra
    ("PNE 2014-2024", ["op:-"]),  # intervalo: mesmo token dos dois lados
])
def test_operadores_entre_numeros(texto, ops):
    assert sorted(t for t in numeros.tokens(texto) if t.startswith("op:")) == sorted(ops)


@pytest.mark.parametrize("original,saida", [
    ("A resposta é 2.", "A resposta é 9."),          # troca de dígito isolado
    ("Calcule 3 + 4.", "Calcule 3 − 4."),            # troca de sinal
    ("Calcule 1 : 10.", "Calcule 1 × 10."),          # divisão escolar
    ("A média foi 6,9.", "A média foi 6,8."),        # decimal
    ("Pinte 3/4 da figura.", "Pinte 3/5 da figura."),  # fração
    ("Subiu 12%.", "Subiu 21%."),                    # porcentagem
    ("Calcule 3 - 4.", "Calcule 3 + 4."),            # subtração com hífen ASCII (A3)
    ("Calcule 3 - 4.", "Calcule 3 4."),              # sinal sumiu (A3)
    ("Calcule 3 × -2.", "Calcule 3 × 2."),           # sinal do negativo sumiu
])
def test_troca_de_numero_ou_sinal_e_detectada(original, saida):
    r = numeros.comparar(numeros.contar([original]), numeros.contar([saida]))
    assert not r["ok"] and r["faltando"]


@pytest.mark.parametrize("original,saida", [
    ("Calcule 3 +4.", "Calcule 3 + 4."),  # A4: espaço não muda o valor
    ("Calcule 3-4.", "Calcule 3 - 4."),
    ("Calcule 3 − 4.", "Calcule 3 - 4."),  # sinal de menos tipográfico e hífen
])
def test_mesma_conta_com_outro_espacamento_passa(original, saida):
    assert numeros.comparar(numeros.contar([original]), numeros.contar([saida]))["ok"]


def test_celulas_negativas_juntas_numa_linha_passam():
    """Ex. 05: cada célula numa linha do original; o DOCX junta a linha da tabela com espaços."""
    original = ["2,0%", "-24,8%", "-18,9%", "12,0", "-13,5%"]
    saida = ["2,0% -24,8% -18,9% 12,0 -13,5%"]
    assert numeros.comparar(numeros.contar(original), numeros.contar(saida))["ok"]


def test_sinal_negativo_perdido_e_detectado():
    assert not numeros.comparar(numeros.contar(["Saldo de -3."]), numeros.contar(["Saldo de 3."]))["ok"]


def test_mesmos_numeros_em_outra_quebra_de_linha_passam():
    r = numeros.comparar(numeros.contar(["Entre 2009 e", "2021, 6,9%"]), numeros.contar(["Entre 2009 e 2021, 6,9%"]))
    assert r["ok"] and r["tokens_original"] == 3


# ------------------------------------------------------------------ verificação de ficha (ponta a ponta sem LibreOffice)
def _ficha(tmp_path, linhas, saida):
    pdf = tmp_path / "F.pdf"
    with pymupdf.open() as d:
        p = d.new_page()
        for y, t in linhas:
            p.insert_text((72, y), t)
        d.save(pdf)
    doc = Document()
    for t in saida:
        doc.add_paragraph(t)
    doc.save(tmp_path / "F_COMPACTO.docx")
    meta = {"origem": pdf.name, "paginas": 1, "removidas": {}, "stats": {
        "linhas_resposta_orig": 0, "imagens": 0, "tabelas": 0, "quadros": 0}}
    return ficha.verificar(pdf, tmp_path, {"meta": meta, "elementos": [{"t": "p", "text": t} for t in saida]})


def test_ficha_digito_trocado_passa_na_cobertura_mas_nao_nos_numeros(tmp_path):
    rep = _ficha(tmp_path, [(100, "Quantos lados tem a figura? A resposta correta e 2 lados.")],
                 ["Quantos lados tem a figura? A resposta correta e 9 lados."])
    assert rep["cobertura_texto_pct"] == 100  # a cobertura por palavras ignora dígito isolado
    assert not rep["numeros"]["ok"] and rep["numeros"]["faltando"] == {"2": 1}


def test_ficha_numero_de_pagina_no_rodape_nao_conta(tmp_path):
    rep = _ficha(tmp_path, [(100, "Some 12 com 30 e escreva o total."), (820, "7")], ["Some 12 com 30 e escreva o total."])
    assert rep["numeros"]["ok"]


def test_ficha_alternativa_so_de_digitos_conta(tmp_path):
    rep = _ficha(tmp_path, [(100, "Qual o resultado de 6 x 2?"), (130, "12"), (150, "14")],
                 ["Qual o resultado de 6 x 2?", "☐ A) 12", "☐ B) 41"])
    assert rep["numeros"]["faltando"] == {"14": 1}


# ------------------------------------------------------------------ gate
def _processar_com_verificacao(tmp_path, perfil, monkeypatch, verificacao):
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
    return api.processar(pdf, api.Opcoes(), perfil, tmp_path / "out").relatorio


def test_gate_bloqueia_numero_ausente_mesmo_com_cobertura_total(tmp_path, perfil_por_nome, monkeypatch):
    v = {"cobertura_texto_pct": 100.0, "palavras_original": 10,
         "numeros": {"ok": False, "tokens_original": 3, "faltando": {"2": 1, "op:+": 1}, "a_mais": {"9": 1}}}
    (tmp_path / "out").mkdir()
    rel = _processar_com_verificacao(tmp_path, perfil_por_nome["mathema-saeb"], monkeypatch, v)
    assert rel.gate == "bloqueado"
    assert "números do original ausentes" in rel.motivo_gate and "operador +" in rel.motivo_gate


def test_gate_ok_quando_numeros_conferem(tmp_path, perfil_por_nome, monkeypatch):
    v = {"cobertura_texto_pct": 100.0, "palavras_original": 10,
         "numeros": {"ok": True, "tokens_original": 3, "faltando": {}, "a_mais": {}}}
    (tmp_path / "out").mkdir()
    rel = _processar_com_verificacao(tmp_path, perfil_por_nome["mathema-saeb"], monkeypatch, v)
    assert rel.gate == "ok" and rel.motivo_gate is None


def test_gate_bloqueia_numero_fora_do_lugar(tmp_path, perfil_por_nome, monkeypatch):
    """M12, story 2.13: contagem global confere, mas o número saiu do lugar de origem."""
    v = {"cobertura_texto_pct": 100.0, "palavras_original": 10,
         "numeros": {"ok": False, "tokens_original": 2, "faltando": {}, "a_mais": {},
                     "fora_de_contexto": 2, "fora_de_contexto_trechos": ["o quadrado tem 4 lados", "o cubo tem 6 faces"]}}
    (tmp_path / "out").mkdir()
    rel = _processar_com_verificacao(tmp_path, perfil_por_nome["mathema-saeb"], monkeypatch, v)
    assert rel.gate == "bloqueado" and "ausentes" not in rel.motivo_gate
    assert "2 número(s) do original fora do lugar de origem" in rel.motivo_gate and "tem 4 lados" in rel.motivo_gate


# ------------------------------------------------------------------ marcador de nota em título/legenda (achado do M09)
def test_marcador_de_nota_em_legenda_nao_gruda_no_numero(tmp_path):
    runs = [["Gráfico 4 – Brasil – 2012-2023", ER.B], ["2", ER.B | ER.SUP]]
    assert ER.runs_text(runs) == "Gráfico 4 – Brasil – 2012-20232"  # o defeito: texto puro gruda o marcador
    legenda = ER.runs_text_sup(runs)
    assert legenda == "Gráfico 4 – Brasil – 2012-2023²"
    d = Document()
    p = d.add_paragraph()
    criados = RR.add_text(p, legenda)
    assert [(r.text, bool(r.font.superscript)) for r in criados] == [("Gráfico 4 – Brasil – 2012-2023", False), ("2", True)]
    d.save(tmp_path / "l.docx")
    assert VR.docx_textos(tmp_path / "l.docx") == ["Gráfico 4 – Brasil – 2012-2023"]  # sobrescrito fora da cobertura
    assert "2023" in numeros.tokens(VR.docx_textos(tmp_path / "l.docx")[0])
