"""Story 2.8 (E1): pool de perfis do LibreOffice. Sem LibreOffice: `_rodar` é simulado."""
import subprocess
import types

import pytest

from docredux_engine import soffice


@pytest.fixture
def pool(tmp_path, monkeypatch):
    p = tmp_path / "pool"
    monkeypatch.setenv(soffice.POOL_ENV, str(p))
    chamadas = []

    def rodar(perfil, docx, outdir, timeout):
        chamadas.append(perfil)
        if not rodar.falha(perfil):
            (outdir / (docx.stem + ".pdf")).write_bytes(b"%PDF-1.7")
        return types.SimpleNamespace(returncode=0 if not rodar.falha(perfil) else 1, stderr=b"falhou")

    rodar.falha = lambda perfil: False
    monkeypatch.setattr(soffice, "_rodar", rodar)
    docx = tmp_path / "a.docx"
    docx.write_bytes(b"x")
    return types.SimpleNamespace(dir=p, chamadas=chamadas, rodar=rodar, docx=docx, out=tmp_path)


def test_perfil_reaproveitado_entre_chamadas(pool):
    soffice.para_pdf(pool.docx, pool.out)
    soffice.para_pdf(pool.docx, pool.out)
    assert pool.chamadas[0] == pool.chamadas[1]
    assert pool.chamadas[0].parent == pool.dir
    assert not (pool.chamadas[0] / soffice.MARCA).exists()


def test_perfil_em_uso_nao_e_compartilhado(pool):
    soffice.para_pdf(pool.docx, pool.out)
    trava = soffice._travar(pool.dir / "perfil0.lock")  # outra conversão segura o perfil 0
    try:
        soffice.para_pdf(pool.docx, pool.out)
    finally:
        soffice._soltar(trava)
    assert pool.chamadas[1] != pool.chamadas[0] and pool.chamadas[1].name.startswith("perfil1-")


def test_pool_cheio_usa_perfil_temporario(pool, monkeypatch):
    monkeypatch.setattr(soffice, "MAX_PERFIS", 1)
    soffice.para_pdf(pool.docx, pool.out)
    trava = soffice._travar(pool.dir / "perfil0.lock")
    try:
        soffice.para_pdf(pool.docx, pool.out)
    finally:
        soffice._soltar(trava)
    temporario = pool.chamadas[1]
    assert temporario.parent != pool.dir and not temporario.exists()  # apagado no fim


def test_falha_no_perfil_reaproveitado_tenta_perfil_novo_e_troca_o_do_pool(pool):
    soffice.para_pdf(pool.docx, pool.out)
    reaproveitado = pool.chamadas[0]
    pool.rodar.falha = lambda perfil: perfil == reaproveitado
    assert soffice.para_pdf(pool.docx, pool.out).exists()  # segunda tentativa, perfil temporário
    assert pool.chamadas[1] == reaproveitado and pool.chamadas[2].parent != pool.dir
    pool.rodar.falha = lambda perfil: False
    soffice.para_pdf(pool.docx, pool.out)
    novo = pool.chamadas[3]
    assert novo.parent == pool.dir and novo != reaproveitado  # caminho novo (órfão do antigo não atende)
    assert not reaproveitado.exists()


def test_conversao_que_morreu_no_meio_troca_o_perfil(pool):
    soffice.para_pdf(pool.docx, pool.out)
    antigo = pool.chamadas[0]
    (antigo / soffice.MARCA).touch()  # processo morto durante a conversão: a marca sobrou
    soffice.para_pdf(pool.docx, pool.out)
    assert pool.chamadas[1] != antigo and not antigo.exists()


def test_timeout_propaga_e_solta_a_trava(pool, monkeypatch):
    def estoura(perfil, docx, outdir, timeout):
        pool.chamadas.append(perfil)
        raise subprocess.TimeoutExpired("soffice", timeout)

    monkeypatch.setattr(soffice, "_rodar", estoura)
    with pytest.raises(subprocess.TimeoutExpired):
        soffice.para_pdf(pool.docx, pool.out, timeout=1)
    trava = soffice._travar(pool.dir / "perfil0.lock")
    assert trava is not None  # trava solta
    soffice._soltar(trava)
    assert (pool.chamadas[0] / soffice.MARCA).exists()  # próxima reserva troca o perfil


def test_sem_variavel_usa_pool_do_processo(monkeypatch):
    monkeypatch.delenv(soffice.POOL_ENV, raising=False)
    monkeypatch.setattr(soffice, "_pool_processo", None)
    a = soffice.pool()
    assert a.exists() and soffice.pool() == a


# ---------------------------------------------------------------- revisão 2026-11-20
def _parcial(pool, codigo):
    """Conversor que grava um PDF e sai com `codigo` (saída parcial do LibreOffice)."""
    def rodar(perfil, docx, outdir, timeout):
        pool.chamadas.append(perfil)
        (outdir / (docx.stem + ".pdf")).write_bytes(b"%PDF-parcial")
        return types.SimpleNamespace(returncode=codigo, stderr=b"conversao incompleta")
    return rodar


def test_codigo_de_erro_com_pdf_nao_e_entregue(pool, monkeypatch):
    """A2: código != 0 e arquivo no disco = parcial. Nada é entregue e o erro sobe."""
    monkeypatch.setattr(soffice, "_rodar", _parcial(pool, 23))
    with pytest.raises(RuntimeError, match="código 23"):
        soffice.para_pdf(pool.docx, pool.out)
    assert not (pool.out / "a.pdf").exists()
    assert len(pool.chamadas) == 2  # perfil do pool e depois perfil temporário


def test_codigo_de_erro_troca_o_perfil_do_pool(pool, monkeypatch):
    """A2: o perfil da conversão com código de erro não volta como saudável."""
    monkeypatch.setattr(soffice, "_rodar", _parcial(pool, 23))
    with pytest.raises(RuntimeError):
        soffice.para_pdf(pool.docx, pool.out)
    ruim = pool.chamadas[0]
    perfil, trava = soffice._reservar()
    try:
        assert perfil != ruim and not ruim.exists()
    finally:
        soffice._soltar(trava)


def test_codigo_de_erro_no_pool_e_sucesso_no_temporario(pool, monkeypatch):
    """A2: o PDF que sai é o do perfil temporário (código 0), não o parcial do pool."""
    parcial = _parcial(pool, 23)

    def rodar(perfil, docx, outdir, timeout):
        if perfil.parent == pool.dir:
            return parcial(perfil, docx, outdir, timeout)
        pool.chamadas.append(perfil)
        (outdir / (docx.stem + ".pdf")).write_bytes(b"%PDF-completo")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(soffice, "_rodar", rodar)
    assert soffice.para_pdf(pool.docx, pool.out).read_bytes() == b"%PDF-completo"
    assert (pool.chamadas[0] / soffice.MARCA).exists()


def test_pool_relativo_vira_absoluto(tmp_path, monkeypatch):
    """A6: DOCREDUX_LO_POOL relativo; o URI do perfil precisa de caminho absoluto."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(soffice.POOL_ENV, "cache/_libreoffice")
    p = soffice.pool()
    assert p.is_absolute() and p == (tmp_path / "cache" / "_libreoffice").resolve()
    perfil, trava = soffice._reservar()
    try:
        assert perfil.as_uri().startswith("file:")
    finally:
        soffice._soltar(trava)
