"""`python -m worker [--processos N]`: N processos do laço (WORKER_PROCESSOS, padrão 2; spec §7).

Variáveis: as da API (DATABASE_URL no modo sessão, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, RESEND_*) e
WORKER_TIPOS, WORKER_PROCESSOS, WORKER_TIMEOUT_TRIAGEM_S, WORKER_TIMEOUT_PROCESSAR_S, WORKER_CACHE,
DOCREDUX_SOFFICE, DOCREDUX_LO_POOL (padrão: <WORKER_CACHE>/_libreoffice). Fora do container, lê api/.env se existir.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import signal
import sys

from .processo import processo


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="worker")
    ap.add_argument("--processos", type=int, default=int(os.environ.get("WORKER_PROCESSOS", 2)))
    a = ap.parse_args(argv)
    if a.processos <= 1:
        processo(0)
        return
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=processo, args=(i,), name=f"worker-{i}") for i in range(a.processos)]
    for p in procs:
        p.start()

    def _encerrar(*_):
        for p in procs:
            if p.is_alive():
                p.terminate()

    signal.signal(signal.SIGTERM, _encerrar)
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        _encerrar()
    sys.exit(max((p.exitcode or 0) for p in procs))


if __name__ == "__main__":
    main()
