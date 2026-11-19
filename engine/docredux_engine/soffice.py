"""LibreOffice headless: DOCX -> PDF.

O executável vem de DOCREDUX_SOFFICE (spec §4). Sem a variável, procura `soffice` no PATH e depois o
caminho padrão do SO. Cada chamada usa um perfil de usuário próprio (-env:UserInstallation), apagado
no fim (DT-5): conversões em paralelo não disputam o mesmo perfil e uma falha não contamina a próxima.
"""
import os
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


def para_pdf(docx, outdir, timeout=900):
    """Converte `docx` em `outdir/<stem>.pdf` e devolve o caminho. Levanta RuntimeError se o PDF não sair."""
    docx, outdir = Path(docx), Path(outdir)
    alvo = outdir / (docx.stem + ".pdf")
    if alvo.exists():
        alvo.unlink()
    perfil = Path(tempfile.mkdtemp(prefix="docredux-lo-"))
    try:
        r = subprocess.run([localizar(), f"-env:UserInstallation={perfil.as_uri()}", "--headless", "--norestore",
                            "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
                           capture_output=True, timeout=timeout)
    finally:
        shutil.rmtree(perfil, ignore_errors=True)
    if not alvo.exists():
        err = (r.stderr or b"").decode(errors="replace")[-500:]
        raise RuntimeError(f"LibreOffice não gerou o PDF (código {r.returncode}): {err}")
    return alvo
