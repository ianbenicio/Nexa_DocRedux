"""Nova tentativa e tratador de falha final (spec §7): um só caminho para worker, timeout e coletor.

Ordem de travas em todo o worker: documento, depois job (a mesma da API), para não haver deadlock.
A reivindicação é a exceção e usa SKIP LOCKED nas duas linhas.
"""
from __future__ import annotations

import logging
from uuid import UUID

import psycopg

from app import ciclo

log = logging.getLogger("docredux.worker")

BACKOFF_S = 30  # §7: disponivel_em = now() + 30 s × tentativas


class FalhaDefinitiva(Exception):
    """Falha que não melhora com nova tentativa (tamanho ou SHA-256 divergente, PDF recusado no processar)."""


def travar_doc(conn: psycopg.Connection, documento_id: UUID) -> dict:
    return conn.execute("select *, expira_em <= now() as expirou from public.documentos where id = %s for update",
                        (documento_id,)).fetchone()


def fechar_job(conn: psycopg.Connection, job_id: int, status: str, erro: str | None = None) -> None:
    conn.execute("""update public.jobs set status = %s::public.status_job, concluido_em = now(), erro = %s,
                           heartbeat_em = null where id = %s""", (status, erro, job_id))


def concluir_exclusao(conn: psycopg.Connection, doc: dict) -> UUID | None:
    """Exclusão pendente (§7): o job deste worker já foi fechado. Se nenhum outro job do documento está
    `processando`, apaga o conteúdo (estorno se a reserva estiver aberta). Devolve o usuário, para
    `ciclo.finalizar_conta` depois do commit."""
    outro = conn.execute("select 1 from public.jobs where documento_id = %s and status = 'processando'",
                         (doc["id"],)).fetchone()
    if outro:
        return None
    conn.execute("""update public.jobs set status = 'cancelado', concluido_em = now(), erro = 'exclusão do documento'
                     where documento_id = %s and status = 'na_fila'""", (doc["id"],))
    estado = ciclo.estado_final_pendente(doc)
    ciclo.concluir_apagamento(conn, doc, estado, "expiração (30 dias)" if estado == "expirado"
                              else "exclusão durante o processamento")
    return doc["usuario_id"]


def pos_exclusao(usuario_id: UUID | None) -> None:
    """Fora da transação: conta em exclusão sem mais nada vivo → ajuste do saldo e remoção do Auth."""
    if usuario_id is None:
        return
    try:
        ciclo.finalizar_conta(usuario_id)
    except Exception as e:  # noqa: BLE001 — a tarefa diária tenta de novo
        log.warning("finalizar conta %s falhou: %s", usuario_id, type(e).__name__)


def falha_final(conn: psycopg.Connection, job: dict, doc: dict, erro: str) -> UUID | None:
    """Tratador de falha final (§7, §5 reprocessamento). Documento e job já travados."""
    fechar_job(conn, job["id"], "falhou", erro[:1000])
    if ciclo.exclusao_marcada(doc):
        return concluir_exclusao(conn, doc) if doc["status"] not in ciclo.ESTADOS_APAGADOS else None
    if job["tipo"] == "triagem":
        if doc["status"] == "triagem":
            conn.execute("update public.documentos set status = 'upload_falhou' where id = %s", (doc["id"],))
    elif job["tipo"] == "processar":
        if job["origem"] == "reprocessar" and job["estado_origem"] == "pronto":
            # mantém a versão anterior; sem estorno (o crédito já foi capturado)
            conn.execute("update public.documentos set reprocessando = false where id = %s", (doc["id"],))
        else:
            ciclo.estornar_se_aberta(conn, doc["id"], "falha no processamento")
            conn.execute("update public.documentos set status = 'falhou', reprocessando = false where id = %s",
                         (doc["id"],))
        conn.execute("update public.metricas_entrega set tentativas = %s, atualizado_em = now() where documento_id = %s",
                     (job["tentativas"], doc["id"]))
    # juiz: o motivo `juiz_falhou` da fila do backoffice sai do próprio job falho (unidades 5 e 6)
    log.warning("job %s (%s) falhou de vez: %s", job["id"], job["tipo"], erro[:200])
    return None


def devolver_ou_falhar(conn: psycopg.Connection, job_id: int, erro: str, *, definitiva: bool = False,
                       so_se_parado: bool = False) -> None:
    """Nova tentativa (fila com backoff) ou falha final. `so_se_parado`: coletor, só se o heartbeat
    ainda estiver velho depois da trava (outro worker pode ter acabado de pulsar)."""
    pendente_conta = None
    with conn.transaction():
        doc_id = conn.execute("select documento_id from public.jobs where id = %s", (job_id,)).fetchone()
        if doc_id is None:
            return
        doc = travar_doc(conn, doc_id["documento_id"])
        job = conn.execute("select * from public.jobs where id = %s for update", (job_id,)).fetchone()
        if job["status"] != "processando":
            return
        if so_se_parado and conn.execute("select %s::timestamptz < now() - interval '2 minutes' as velho",
                                          (job["heartbeat_em"],)).fetchone()["velho"] is not True:
            return
        if ciclo.exclusao_marcada(doc):
            fechar_job(conn, job_id, "cancelado", f"exclusão; {erro}"[:1000])
            if doc["status"] not in ciclo.ESTADOS_APAGADOS:
                pendente_conta = concluir_exclusao(conn, doc)
        elif not definitiva and job["tentativas"] < job["max_tentativas"]:
            conn.execute(
                """update public.jobs set status = 'na_fila', worker_id = null, heartbeat_em = null, erro = %s,
                          disponivel_em = now() + make_interval(secs => %s) where id = %s""",
                (erro[:1000], BACKOFF_S * job["tentativas"], job_id))
            if job["tipo"] == "processar" and doc["status"] == "processando":
                conn.execute("update public.documentos set status = 'na_fila' where id = %s", (doc["id"],))
            log.info("job %s volta à fila (tentativa %s/%s): %s", job_id, job["tentativas"], job["max_tentativas"],
                     erro[:200])
        else:
            pendente_conta = falha_final(conn, job, doc, erro)
    pos_exclusao(pendente_conta)


def coletar(conn: psycopg.Connection) -> int:
    """Coletor (§7, a cada 60 s em qualquer worker): heartbeat há mais de 2 min → nova tentativa."""
    ids = [r["id"] for r in conn.execute(
        """select id from public.jobs where status = 'processando' and heartbeat_em < now() - interval '2 minutes'
            order by id""").fetchall()]
    for i in ids:
        try:
            devolver_ou_falhar(conn, i, "heartbeat parado há mais de 2 min (worker caiu?)", so_se_parado=True)
        except Exception as e:  # noqa: BLE001
            log.warning("coletor: job %s: %s", i, type(e).__name__)
    return len(ids)
