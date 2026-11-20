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
