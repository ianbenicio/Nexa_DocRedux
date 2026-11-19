"""Alvo de cada processo do worker. Fica fora do `__main__`: com `spawn` e `python -m worker`, o filho não
reimporta o `__main__` do pacote e não acharia a função lá."""
from __future__ import annotations

import logging
import signal

from app.config import carregar_env_local


def processo(indice: int) -> None:
    carregar_env_local()
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s %(levelname)s w{indice} %(name)s %(message)s")
    from .laco import Worker
    w = Worker()
    signal.signal(signal.SIGTERM, lambda *_: w.parar.set())
    try:
        w.rodar()
    except KeyboardInterrupt:
        pass
