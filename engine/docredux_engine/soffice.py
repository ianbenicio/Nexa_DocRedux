"""LibreOffice headless: DOCX -> PDF.

O executável vem de DOCREDUX_SOFFICE (spec §4). Sem a variável, procura `soffice` no PATH e depois o
caminho padrão do SO.

Perfil de usuário (-env:UserInstallation, DT-5, story 2.8): criar um perfil novo custa ~6 s por chamada
(ex. 03: 10,6 s com perfil novo, 4,4 s reaproveitado). Cada conversão reserva um perfil de um pool, com trava
de arquivo exclusiva: duas conversões simultâneas nunca usam o mesmo perfil, e a trava se solta sozinha se o
processo morrer. O pool fica em DOCREDUX_LO_POOL (o worker aponta para o cache dele, um pool por máquina) ou,
sem a variável, num temporário do processo, apagado na saída.

Uma falha não contamina a próxima: o perfil fica marcado durante a conversão. Se a marca sobrar (conversão
que falhou, estourou o tempo ou morreu no meio), a próxima reserva descarta o perfil e cria outro num caminho
novo; o LibreOffice deriva o canal de comunicação do caminho do perfil, então um processo órfão do perfil
antigo não recebe a conversão seguinte. Sem perfil livre no pool, ou se a conversão no perfil reaproveitado
falhar, a chamada usa um perfil temporário só dela, apagado no fim (o comportamento anterior).
"""
import atexit
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_PADRAO = {
    "win32": [r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"],
    "darwin": ["/Applications/LibreOffice.app/Contents/MacOS/soffice"],
}
_LINUX = ["/opt/libreoffice/program/soffice", "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"]

POOL_ENV = "DOCREDUX_LO_POOL"
MAX_PERFIS = 8          # perfis por pool; acima disso a conversão usa perfil temporário
MARCA = ".docredux-convertendo"
_pool_processo = None


class SofficeNaoEncontrado(RuntimeError):
    pass


def localizar():
    env = os.environ.get("DOCREDUX_SOFFICE")
    if env:
        if Path(env).exists() or shutil.which(env):
            return env
        raise SofficeNaoEncontrado(f"DOCREDUX_SOFFICE={env!r} não existe")
    for nome in ("soffice", "libreoffice"):
        achado = shutil.which(nome)
        if achado:
            return achado
    for c in _PADRAO.get(sys.platform, _LINUX):
        if Path(c).exists():
            return c
    raise SofficeNaoEncontrado("LibreOffice não encontrado: defina DOCREDUX_SOFFICE")


def versao():
    exe = localizar()
    if sys.platform == "win32" and exe.lower().endswith("soffice.exe"):
        com = exe[:-4] + ".com"  # soffice.exe não escreve no console no Windows
        exe = com if Path(com).exists() else exe
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=120)
    return (r.stdout or r.stderr).strip()


# ---------------------------------------------------------------- pool de perfis
def _travar(caminho):
    """Trava exclusiva, sem esperar, num arquivo. Devolve o arquivo aberto ou None se outro já trava."""
    f = open(caminho, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _soltar(f):
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        f.close()  # fechar também solta (flock e LockFile)


def pool():
    global _pool_processo
    env = os.environ.get(POOL_ENV)
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if _pool_processo is None:
        _pool_processo = Path(tempfile.mkdtemp(prefix="docredux-lo-pool-"))
        atexit.register(shutil.rmtree, _pool_processo, True)
    return _pool_processo


def _reservar():
    """(perfil, trava) de um slot livre do pool, ou None. Perfil marcado por conversão anterior é trocado."""
    base = pool()
    for i in range(MAX_PERFIS):
        trava = _travar(base / f"perfil{i}.lock")
        if trava is None:
            continue
        atual = base / f"perfil{i}.atual"
        nome = atual.read_text().strip() if atual.exists() else ""
        perfil = base / nome if nome else None
        if perfil is None or (perfil / MARCA).exists():
            if perfil is not None:
                shutil.rmtree(perfil, ignore_errors=True)
            perfil = base / f"perfil{i}-{secrets.token_hex(4)}"  # caminho novo: órfão do antigo não atende
            atual.write_text(perfil.name)
        perfil.mkdir(exist_ok=True)
        return perfil, trava
    return None


def _rodar(perfil, docx, outdir, timeout):
    return subprocess.run([localizar(), f"-env:UserInstallation={perfil.as_uri()}", "--headless", "--norestore",
                           "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
                          capture_output=True, timeout=timeout)


def para_pdf(docx, outdir, timeout=900):
    """Converte `docx` em `outdir/<stem>.pdf` e devolve o caminho. Levanta RuntimeError se o PDF não sair."""
    docx, outdir = Path(docx), Path(outdir)
    alvo = outdir / (docx.stem + ".pdf")
    if alvo.exists():
        alvo.unlink()
    r = None
    slot = _reservar()
    if slot is not None:
        perfil, trava = slot
        try:
            marca = perfil / MARCA
            marca.touch()
            r = _rodar(perfil, docx, outdir, timeout)  # timeout propaga; a marca fica e o perfil é trocado
            if alvo.exists():
                marca.unlink()
        finally:
            _soltar(trava)
    if not alvo.exists():  # sem perfil livre, ou o reaproveitado falhou: perfil só desta chamada
        perfil = Path(tempfile.mkdtemp(prefix="docredux-lo-"))
        try:
            r = _rodar(perfil, docx, outdir, timeout)
        finally:
            shutil.rmtree(perfil, ignore_errors=True)
    if not alvo.exists():
        err = (r.stderr or b"").decode(errors="replace")[-500:]
        raise RuntimeError(f"LibreOffice não gerou o PDF (código {r.returncode}): {err}")
    return alvo
