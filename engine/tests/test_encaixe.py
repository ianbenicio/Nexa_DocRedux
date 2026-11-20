"""R52: imagem que nao cabe no fim da pagina encolhe ate 15% para nao deixar buraco (revisao do A1)."""
import pymupdf

from docredux_engine.ficha import encaixe as E


def _pix():
    p = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    p.clear_with(120)
    return p.tobytes("png")


def _pdf(tmp_path, paginas):
    """paginas: lista de (y_fim_texto, [(y0, y1) das imagens]). Texto sempre comeca no topo da margem."""
    doc = pymupdf.open(); png = _pix()
    for fim, imgs in paginas:
        p = doc.new_page(width=595.3, height=841.9)
        y = 55
        while y <= fim:
            p.insert_text((50, y), "Linha de texto da ficha.", fontsize=10.5); y += 13
        for y0, y1 in imgs:
            p.insert_image(pymupdf.Rect(60, y0, 500, y1), stream=png)
    f = tmp_path / "s.pdf"; doc.save(f)
    return f


def _modelo(n, **extra):
    els = [{"t": "p", "text": "x"}]
    for i in range(n):
        els += [{"t": "img", "file": f"i{i}.png", "w": 440, "h": 460, "pct": 20, "side": [], **extra}, {"t": "p", "text": "y"}]
    return {"elementos": els}


def test_buraco_resolvido_com_pouca_reducao(tmp_path):
    # pag 1 termina em ~300 (buraco de ~500 pt); pag 2 abre com titulo e a imagem de 460 pt
    pdf = _pdf(tmp_path, [(300, []), (70, [(90, 550)])])
    a = E.candidato(pdf, _modelo(1))
    assert a is not None and a["idx"] == [1] and a["pagina"] == 0
    assert 1 - E.ENCOLHE_MAX <= a["k"] < 1


def test_sem_candidato_quando_precisa_encolher_demais_ou_ja_tentado_ou_figura_r7(tmp_path):
    longe = _pdf(tmp_path, [(500, []), (70, [(90, 550)])])  # buraco de ~300 pt: precisaria de -40%
    assert E.candidato(longe, _modelo(1)) is None
    perto = _pdf(tmp_path, [(300, []), (70, [(90, 550)])])
    assert E.candidato(perto, _modelo(1), pular={1}) is None
    assert E.candidato(perto, _modelo(1, manter_tamanho=True)) is None


def test_mapeamento_incerto_nao_mexe(tmp_path):
    pdf = _pdf(tmp_path, [(300, []), (70, [(90, 550)])])
    assert E.candidato(pdf, _modelo(2)) is None  # 2 imagens no modelo, 1 no PDF


def test_linha_de_resposta_conta_como_conteudo(tmp_path):
    """Borda horizontal tem altura 0; sem conta-la o buraco parecia maior (ex. 04, pag. 5)."""
    pdf = _pdf(tmp_path, [(300, []), (70, [(90, 550)])])
    with pymupdf.open(pdf) as d:
        d[0].draw_line((50, 760), (540, 760)); d.saveIncr()
    assert E.candidato(pdf, _modelo(1)) is None


def test_aceito_so_se_a_imagem_subiu_sem_pagina_a_mais(tmp_path):
    alvo = {"idx": [1], "k": 0.93, "pagina": 0}
    subiu = _pdf(tmp_path, [(300, [(330, 760)])])
    assert E.aceito(subiu, _modelo(1), alvo, paginas_antes=2)
    ficou = _pdf(tmp_path, [(300, []), (70, [(90, 520)])])
    assert not E.aceito(ficou, _modelo(1), alvo, paginas_antes=2)
    assert not E.aceito(ficou, _modelo(1), alvo, paginas_antes=1)
