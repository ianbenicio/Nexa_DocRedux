"""Tarefa diária (spec §7, §14): retenção de 30 dias, exclusões órfãs, contas em exclusão, cache local.

Roda no laço do worker, em um worker só (`pg_try_advisory_lock`), no máximo uma vez a cada 23 h
(`config.limpeza_executada_em`). Não é job da fila.
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import psycopg

from app import ciclo, supabase

from .falhas import concluir_exclusao, pos_exclusao, travar_doc

log = logging.getLogger("docredux.limpeza")

TRAVA = "docredux_limpeza"


def talvez_rodar(conn: psycopg.Connection, cache: Path | None = None, *, forcar: bool = False) -> dict | None:
    if not conn.execute("select pg_try_advisory_lock(hashtext(%s)) as ok", (TRAVA,)).fetchone()["ok"]:
        return None
    try:
        if not forcar:
            recente = conn.execute(
                """select (valor #>> '{}')::timestamptz > now() - interval '23 hours' as r
                     from public.config where chave = 'limpeza_executada_em'""").fetchone()
            if recente and recente["r"]:
                return None
        r = rodar(conn, cache)
        conn.execute("update public.config set valor = to_jsonb(now()), atualizado_em = now() "
                     "where chave = 'limpeza_executada_em'")
        log.info("limpeza diária: %s", r)
        return r
    finally:
        conn.execute("select pg_advisory_unlock(hashtext(%s))", (TRAVA,))


def rodar(conn: psycopg.Connection, cache: Path | None = None) -> dict:
    r = {"expirados": 0, "expiracao_pendente": 0, "orfaos": 0, "contas": 0, "cache": 0}
    # 1. Retenção (§14): tudo que passou de expira_em e não foi apagado
    for row in conn.execute(
            """select id from public.documentos
                where expira_em < now() and status not in ('excluido', 'expirado') and not exclusao_pendente
                order by expira_em""").fetchall():
        try:
            with conn.transaction():
                doc = travar_doc(conn, row["id"])
                if doc["status"] in ciclo.ESTADOS_APAGADOS or doc["exclusao_pendente"]:
                    continue
                res = ciclo.apagar_documento(conn, doc, "expirado", "expiração (30 dias)")
            r["expiracao_pendente" if res == "pendente" else "expirados"] += 1
        except Exception as e:  # noqa: BLE001 — um documento não trava a limpeza dos outros
            log.warning("expirar %s falhou: %s", row["id"], type(e).__name__)
    # 2. Exclusões pendentes sem job vivo (o worker caiu depois de marcar, ou o job foi cancelado)
    contas = set()
    for row in conn.execute(
            """select d.id from public.documentos d
                where d.exclusao_pendente and not exists (
                      select 1 from public.jobs j where j.documento_id = d.id and j.status = 'processando')""").fetchall():
        try:
            with conn.transaction():
                doc = travar_doc(conn, row["id"])
                if doc["exclusao_pendente"]:
                    u = concluir_exclusao(conn, doc)
                    if u:
                        contas.add(u)
                    r["orfaos"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("exclusão órfã %s falhou: %s", row["id"], type(e).__name__)
    # 3. Contas em exclusão (§14 item 4): ajuste do saldo e remoção do Auth quando nada mais estiver vivo
    for row in conn.execute("select id, saldo from public.perfis_usuario where exclusao_conta_em is not null").fetchall():
        try:
            if row["id"] in contas or row["saldo"] > 0 or supabase.usuario_existe(str(row["id"])):
                if ciclo.finalizar_conta(row["id"]):
                    r["contas"] += 1
        except Exception as e:  # noqa: BLE001
            log.warning("finalizar conta %s falhou: %s", row["id"], type(e).__name__)
    # 4. Cache local de originais com mais de 1 dia
    if cache and cache.exists():
        limite = time.time() - 86400
        for p in cache.iterdir():
            # "_*" não é documento (pool de perfis do LibreOffice, story 2.8): apagar em uso quebraria a conversão
            if p.is_dir() and not p.name.startswith("_") and p.stat().st_mtime < limite:
                shutil.rmtree(p, ignore_errors=True)
                r["cache"] += 1
    return r


__all__ = ["talvez_rodar", "rodar", "pos_exclusao"]
