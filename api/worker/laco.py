"""Laço de um processo do worker (spec §7, DT-1, DT-15).

A cada volta: `workers.visto_em`; coletor a cada 60 s; reenvio de avisos a cada 60 s; tarefa diária
(tentada a cada hora); reivindica um job dos tipos deste worker. Ocioso: dorme 2 s.
Uma conexão por processo, em autocommit; cada passo usa `conn.transaction()`.
"""
from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path

import psycopg

from app import notificacoes
from app.db import conectar

from . import falhas, jobs, limpeza, motor

log = logging.getLogger("docredux.worker")

OCIOSO_S = 2
COLETOR_S = 60
AVISOS_S = 60
LIMPEZA_S = 3600


def timeouts_padrao() -> dict[str, float]:
    return {"triagem": float(os.environ.get("WORKER_TIMEOUT_TRIAGEM_S", 300)),  # 5 min (DT-21, story 4.4)
            "processar": float(os.environ.get("WORKER_TIMEOUT_PROCESSAR_S", 900)),
            "juiz": float(os.environ.get("WORKER_TIMEOUT_JUIZ_S", 300))}


def tipos_padrao() -> list[str]:
    return [t.strip() for t in os.environ.get("WORKER_TIPOS", "triagem,processar").split(",") if t.strip()]


def reivindicar(conn: psycopg.Connection, worker_id: str, tipos: list[str]) -> dict | None:
    """§7 com SKIP LOCKED no job e no documento (a API trava o documento primeiro; aqui não se espera)."""
    with conn.transaction():
        cand = conn.execute(
            """select id, documento_id from public.jobs
                where status = 'na_fila' and disponivel_em <= now() and tipo = any(%s::public.tipo_job[])
                order by prioridade, criado_em
                for update skip locked limit 1""", (tipos,)).fetchone()
        if cand is None:
            return None
        doc = conn.execute("select id, status::text from public.documentos where id = %s for update skip locked",
                           (cand["documento_id"],)).fetchone()
        if doc is None:  # a API está mexendo no documento agora: tenta na próxima volta
            return None
        job = conn.execute(
            """update public.jobs set status = 'processando', worker_id = %s, iniciado_em = now(),
                      heartbeat_em = now(), tentativas = tentativas + 1, erro = null
                where id = %s returning *""", (worker_id, cand["id"])).fetchone()
        if job["tipo"] == "processar" and doc["status"] == "na_fila":
            conn.execute("update public.documentos set status = 'processando' where id = %s", (doc["id"],))
    return job


class Worker:
    def __init__(self, worker_id: str | None = None, tipos: list[str] | None = None, *,
                 cache: Path | None = None, timeouts: dict[str, float] | None = None,
                 email: notificacoes.ClienteEmail | None = None, conn: psycopg.Connection | None = None):
        # hostname-pid se repete quando o container reinicia (mesmos pids): o sufixo separa as vidas do processo.
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
        self.tipos = tipos or tipos_padrao()
        self.cache = cache or jobs.cache_padrao()
        # perfis do LibreOffice reaproveitados entre jobs (DT-5, story 2.8): um pool por máquina, herdado
        # pelo subprocesso do motor; cada conversão trava o seu perfil. A limpeza pula pastas com "_".
        os.environ.setdefault("DOCREDUX_LO_POOL", str(self.cache / jobs.POOL_LIBREOFFICE))
        self.timeouts = timeouts or timeouts_padrao()
        self.email = email
        self.conn = conn or conectar(autocommit=True)
        self.parar = threading.Event()
        self._ultimo = {"coletor": 0.0, "avisos": 0.0, "limpeza": 0.0}

    def registrar(self) -> None:
        self.conn.execute(
            """insert into public.workers (worker_id, host) values (%s, %s)
               on conflict (worker_id) do update set visto_em = now()""", (self.worker_id, socket.gethostname()))

    def _periodico(self, nome: str, intervalo: float, fn) -> None:
        agora = time.monotonic()
        if agora - self._ultimo[nome] >= intervalo:
            self._ultimo[nome] = agora
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — tarefa de fundo nunca derruba o laço
                log.warning("%s falhou: %s", nome, type(e).__name__)

    def volta(self) -> bool:
        """Uma volta do laço. True se executou um job."""
        self.registrar()
        self._periodico("coletor", COLETOR_S, lambda: falhas.coletar(self.conn))
        self._periodico("avisos", AVISOS_S, lambda: notificacoes.reenviar_pendentes(self.conn, self.email))
        self._periodico("limpeza", LIMPEZA_S, lambda: limpeza.talvez_rodar(self.conn, self.cache))
        job = reivindicar(self.conn, self.worker_id, self.tipos)
        if job is None:
            return False
        self.executar(job)
        return True

    def executar(self, job: dict) -> None:
        ctx = jobs.Contexto(self.conn, self.worker_id, job, self.cache, self.timeouts, self.email)
        log.info("job %s (%s) do documento %s, tentativa %s", job["id"], job["tipo"], job["documento_id"],
                 job["tentativas"])
        try:
            jobs.EXECUTORES[job["tipo"]](ctx)
        except motor.JobPerdido:
            log.warning("job %s não é mais deste worker; descartado", job["id"])
        except falhas.FalhaDefinitiva as e:
            falhas.devolver_ou_falhar(self.conn, job["id"], f"FalhaDefinitiva: {e}", definitiva=True)
        except BaseException as e:
            erro = f"{type(e).__name__}: {e}"
            if not isinstance(e, Exception):  # KeyboardInterrupt/SystemExit: devolve e propaga
                falhas.devolver_ou_falhar(self.conn, job["id"], "worker encerrado no meio do job")
                raise
            log.warning("job %s falhou: %s", job["id"], erro[:300])
            falhas.devolver_ou_falhar(self.conn, job["id"], erro)
        finally:
            jobs.enviar_avisos(ctx)

    def rodar(self, max_voltas: int | None = None) -> None:
        log.info("worker %s: tipos %s", self.worker_id, ",".join(self.tipos))
        voltas = 0
        try:
            while not self.parar.is_set() and (max_voltas is None or voltas < max_voltas):
                voltas += 1
                try:
                    trabalhou = self.volta()
                except psycopg.OperationalError as e:
                    log.warning("banco indisponível (%s); reconectando em 5 s", type(e).__name__)
                    self.parar.wait(5)
                    self.conn = conectar(autocommit=True)
                    continue
                if not trabalhou:
                    self.parar.wait(OCIOSO_S)
        finally:
            try:
                self.conn.execute("delete from public.workers where worker_id = %s", (self.worker_id,))
            except Exception:  # noqa: BLE001
                pass
