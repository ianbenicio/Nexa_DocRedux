"""Chamada do motor em subprocesso (DT-5): `python -m docredux_engine ...`, um JSON no stdout.

Timeout duro por tipo de job; o subprocesso e toda a árvore dele (LibreOffice) são mortos no timeout ou
quando o job deixa de ser deste worker (heartbeat perdido). O heartbeat roda no laço de espera.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

PULSO_S = 15  # §7: heartbeat do job a cada 15 s


class MotorTimeout(Exception):
    pass


class JobPerdido(Exception):
    """O job não é mais deste worker (o coletor devolveu à fila ou a exclusão cancelou)."""


class MotorFalhou(Exception):
    def __init__(self, saida: dict, codigo: int):
        super().__init__(f"{saida.get('erro', 'erro')}: {str(saida.get('mensagem', ''))[:300]}")
        self.saida, self.codigo = saida, codigo

    @property
    def motivo(self) -> str | None:
        return self.saida.get("motivo")


def _matar_arvore(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


def executar(args: list[str], *, timeout_s: float, pulso: Callable[[], bool] | None = None,
             cwd: Path | None = None) -> dict:
    """Roda o motor e devolve o JSON. `pulso()` é chamado a cada PULSO_S; False → mata e levanta JobPerdido."""
    cmd = [sys.executable, "-m", "docredux_engine", *args]
    extra = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    err_path = (cwd or Path(".")) / "motor.stderr.log"
    with open(err_path, "wb") as err:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, cwd=cwd, **extra)
        inicio = ultimo = time.monotonic()
        try:
            while True:
                try:
                    out, _ = p.communicate(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    pass
                agora = time.monotonic()
                if agora - inicio > timeout_s:
                    _matar_arvore(p)
                    raise MotorTimeout(f"motor passou de {timeout_s:.0f} s")
                if pulso and agora - ultimo >= PULSO_S:
                    ultimo = agora
                    if not pulso():
                        _matar_arvore(p)
                        raise JobPerdido()
        except BaseException:
            _matar_arvore(p)
            raise
    try:
        saida = json.loads(out.decode("utf-8").strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        raise MotorFalhou({"erro": "SaidaInvalida", "mensagem": f"código {p.returncode}"}, p.returncode) from e
    if p.returncode != 0 or "erro" in saida:
        raise MotorFalhou(saida, p.returncode)
    return saida
