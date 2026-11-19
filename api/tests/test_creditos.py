"""Story 4.2 AC 1: tabela de preços do spec §5 (função pura, inteiros)."""
import pytest

from app.creditos import Opcoes, orcar

O = Opcoes()


def total(paginas, palavras=0, template_novo=False, **op):
    return orcar(paginas, palavras, Opcoes(**op), template_novo)["total"]


@pytest.mark.parametrize("paginas,esperado", [(5, 2), (6, 4), (15, 4), (16, 6), (20, 6), (21, 8), (40, 8),
                                              (41, 10), (60, 10), (61, 12)])
def test_fronteiras(paginas, esperado):
    assert total(paginas) == esperado


def test_exemplos_do_spec():
    assert total(10) == 4            # ficha de 10 págs.
    assert total(120) == 16
    assert total(294) == 34          # ex. 05
    assert total(15, docx=True, manter_capa_elementos=True) == 6
    assert total(16, docx=True, manter_capa_elementos=True) == 11


@pytest.mark.parametrize("paginas,esperado", [(15, 12), (16, 18), (60, 22), (61, 28)])
def test_template_novo(paginas, esperado):
    assert total(paginas, template_novo=True) == esperado


@pytest.mark.parametrize("fracao", [0.2, 0.35, 0.5])
def test_sintese_2000_palavras(fracao):
    assert total(10, 2000, modo="sintese", fracao=fracao) - total(10) == 2


def test_sintese_minimo_1():
    assert total(3, 400, modo="sintese", fracao=0.2) - total(3) == 1


@pytest.mark.parametrize("fracao,sint,tot", [(0.2, 55, 89), (0.35, 73, 107), (0.5, 92, 126)])
def test_ex05_sintese_dt21(fracao, sint, tot):
    o = orcar(294, 91037, Opcoes(modo="sintese", fracao=fracao), False)
    assert o["itens"][-1]["creditos"] == sint
    assert o["total"] == tot


def test_rotulos_e_itens():
    o = orcar(16, 2000, Opcoes(modo="sintese", fracao=0.35, docx=True, manter_capa_elementos=True), True)
    assert [i["rotulo"] for i in o["itens"]] == ["Serviço padrão", "DOCX editável", "Manter capa e elementos",
                                                 "Síntese 35%", "Template novo"]
    assert all(isinstance(i["creditos"], int) for i in o["itens"])


def test_opcoes_invalidas():
    with pytest.raises(ValueError):
        Opcoes(modo="sintese")
    with pytest.raises(ValueError):
        Opcoes(fracao=0.2)
    with pytest.raises(ValueError):
        Opcoes(fracao=0.3, modo="sintese")
    with pytest.raises(ValueError):
        orcar(0, 0, O, False)
