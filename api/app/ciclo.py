"""Ciclo de vida do documento e da conta (spec §5 regra do estorno, §7 exclusão durante um job, §14).

"Apagar conteúdo" é um só, usado pela API (excluir, excluir conta), pelo worker (exclusão pendente no
fim do job) e pela retenção diária. O estorno é decidido pela reserva aberta no ledger, nunca pelo status.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from . import supabase
from .db import transacao
from .erros import ApiErro

log = logging.getLogger("docredux.ciclo")

ESTADOS_APAGADOS = ("excluido", "expirado")


def travar_documento(conn: psycopg.Connection, documento_id: UUID, usuario_id: UUID | None = None) -> dict:
    """SELECT ... FOR UPDATE do documento; 404 se não existe ou não é do usuário (§9: B recebe 404)."""
    if usuario_id is None:
        row = conn.execute("select * from public.documentos where id = %s for update", (documento_id,)).fetchone()
    else:
        row = conn.execute("select * from public.documentos where id = %s and usuario_id = %s for update",
                           (documento_id, usuario_id)).fetchone()
    if row is None:
        raise ApiErro("nao_encontrado")
    return row


def estornar_se_aberta(conn: psycopg.Connection, documento_id: UUID, motivo: str, admin: UUID | None = None) -> bool:
    reserva = conn.execute("select private.reserva_aberta(%s) as r", (documento_id,)).fetchone()["r"]
    if reserva is None:
        return False
    conn.execute("select private.credito_estornar(%s, %s, %s)", (reserva, motivo, admin))
    return True


def _sem_chave(valor: Any, chave: str) -> Any:
    if isinstance(valor, dict):
        return {k: _sem_chave(v, chave) for k, v in valor.items() if k != chave}
    if isinstance(valor, list):
        return [_sem_chave(v, chave) for v in valor]
    return valor


def _limpar_textos_citaveis(conn: psycopg.Connection, documento_id: UUID) -> None:
    """§14: `evidencias` e `resumo` do juiz e `descricao` das modificações podem citar o documento."""
    for row in conn.execute("select id, criterios from public.avaliacoes_juiz where documento_id = %s",
                            (documento_id,)).fetchall():
        conn.execute("update public.avaliacoes_juiz set resumo = null, criterios = %s where id = %s",
                     (Jsonb(_sem_chave(row["criterios"], "evidencias")), row["id"]))
    for row in conn.execute("select versao, relatorio from public.entregas where documento_id = %s",
                            (documento_id,)).fetchall():
        rel = dict(row["relatorio"] or {})
        rel["modificacoes"] = [{k: v for k, v in m.items() if k != "descricao"} for m in rel.get("modificacoes", [])]
        conn.execute("update public.entregas set relatorio = %s where documento_id = %s and versao = %s",
                     (Jsonb(rel), documento_id, row["versao"]))


def concluir_apagamento(conn: psycopg.Connection, doc: dict, estado_final: str, motivo: str) -> None:
    """Estorna (se a reserva estiver aberta), apaga Storage e textos citáveis e fecha o estado.
    O documento deve estar travado e sem job `processando`."""
    estornar_se_aberta(conn, doc["id"], motivo)
    base = f"{doc['usuario_id']}/{doc['id']}"
    supabase.apagar_prefixo(supabase.ORIGINAIS, base)
    supabase.apagar_prefixo(supabase.SAIDAS, base)
    _limpar_textos_citaveis(conn, doc["id"])
    conn.execute(
        """update public.documentos set status = %s, motivo_recusa = null, exclusao_pendente = false,
                  reprocessando = false
            where id = %s""", (estado_final, doc["id"]))  # check: motivo_recusa só em `recusado`
    log.info("documento %s apagado (%s)", doc["id"], estado_final)


def apagar_documento(conn: psycopg.Connection, doc: dict, estado_final: str, motivo: str) -> str:
    """Apagar conteúdo (§14). Cancela os jobs na fila; com job `processando`, marca `exclusao_pendente`
    e o worker conclui no fim dele (§7). Devolve 'pendente' ou o estado final."""
    conn.execute(
        """update public.jobs set status = 'cancelado', concluido_em = now(), erro = %s
            where documento_id = %s and status = 'na_fila'""", (f"cancelado: {motivo}", doc["id"]))
    vivo = conn.execute("select 1 from public.jobs where documento_id = %s and status = 'processando'",
                        (doc["id"],)).fetchone()
    if vivo:
        conn.execute("update public.documentos set exclusao_pendente = true where id = %s", (doc["id"],))
        return "pendente"
    concluir_apagamento(conn, doc, estado_final, motivo)
    return estado_final


def estado_final_pendente(doc: dict) -> str:
    """Estado final de uma exclusão pendente concluída pelo worker."""
    if doc["status"] in ESTADOS_APAGADOS:
        return doc["status"]
    expirou = doc.get("expirou")
    return "expirado" if expirou else "excluido"


def exclusao_marcada(doc: dict) -> bool:
    return bool(doc["exclusao_pendente"]) or doc["status"] in ESTADOS_APAGADOS


# ---------------------------------------------------------------- usuário
def excluir_documento(usuario_id: UUID, documento_id: UUID) -> dict:
    with transacao() as conn:
        doc = travar_documento(conn, documento_id, usuario_id)
        if doc["status"] == "excluido":
            return {"id": str(documento_id), "status": "excluido"}
        r = apagar_documento(conn, doc, "excluido", "exclusão pelo usuário")
    if r == "pendente":
        return {"id": str(documento_id), "status": doc["status"], "exclusao_pendente": True}
    return {"id": str(documento_id), "status": r}


def cancelar_documento(usuario_id: UUID, documento_id: UUID) -> dict:
    """Só em `na_fila` (FR-21, DT-13). Um `pronto` em reprocessamento continua `pronto` → 409."""
    with transacao() as conn:
        doc = travar_documento(conn, documento_id, usuario_id)
        if doc["status"] != "na_fila":
            raise ApiErro("estado_invalido")
        cancelados = conn.execute(
            """update public.jobs set status = 'cancelado', concluido_em = now(), erro = 'cancelado pelo usuário'
                where documento_id = %s and tipo = 'processar' and status = 'na_fila' returning id""",
            (documento_id,)).fetchall()
        if not cancelados:  # o worker já pegou o job: vai para processando
            raise ApiErro("estado_invalido")
        estornar_se_aberta(conn, documento_id, "cancelado pelo usuário")
        conn.execute("update public.documentos set status = 'cancelado' where id = %s", (documento_id,))
    return {"id": str(documento_id), "status": "cancelado"}


# ---------------------------------------------------------------- conta (§14, Q7)
def excluir_conta(usuario_id: UUID, admin: UUID | None) -> dict:
    """1. ban no Auth; 2. exclusão de todos os documentos; 3. e-mail e textos livres apagados;
    4. ajuste do saldo e remoção do Auth quando não houver job vivo (já, ou depois pelo worker)."""
    try:
        supabase.banir_usuario(str(usuario_id))
    except supabase.SupabaseErro as e:
        raise ApiErro("indisponivel", "Não foi possível bloquear o login agora. Tente de novo.") from e
    with transacao() as conn:
        conn.execute(
            """update public.perfis_usuario set exclusao_conta_em = coalesce(exclusao_conta_em, now()), email = null
                where id = %s""", (usuario_id,))
        docs = conn.execute(
            """select * from public.documentos where usuario_id = %s and status not in ('excluido', 'expirado')
                order by criado_em for update""", (usuario_id,)).fetchall()
        pendentes = sum(apagar_documento(conn, d, "excluido", "exclusão da conta") == "pendente" for d in docs)
        conn.execute(
            """update public.feedback_usuario f set comentario = null from public.documentos d
                where d.id = f.documento_id and d.usuario_id = %s""", (usuario_id,))
        conn.execute(
            """update public.problemas_marcados p set descricao = null from public.documentos d
                where d.id = p.documento_id and d.usuario_id = %s""", (usuario_id,))
    concluida = pendentes == 0 and finalizar_conta(usuario_id, admin)
    return {"exclusao_conta": "concluida" if concluida else "pendente", "documentos_pendentes": pendentes}


def finalizar_conta(usuario_id: UUID, admin: UUID | None = None) -> bool:
    """Ajuste do saldo para zero e remoção do Auth, se a conta está marcada e sem job vivo (§14 item 4).
    Idempotente: chamada pela API, pelo worker (fim da exclusão pendente) e pela tarefa diária."""
    with transacao() as conn:
        perfil = conn.execute("select exclusao_conta_em from public.perfis_usuario where id = %s",
                              (usuario_id,)).fetchone()
        if perfil is None or perfil["exclusao_conta_em"] is None:
            return False
        vivo = conn.execute(
            """select 1 from public.documentos d
                where d.usuario_id = %s
                  and (d.exclusao_pendente or d.status not in ('excluido', 'expirado')
                       or exists (select 1 from public.jobs j where j.documento_id = d.id
                                   and j.status in ('na_fila', 'processando')))
                limit 1""", (usuario_id,)).fetchone()
        if vivo:
            return False
        conn.execute("select private.credito_ajuste_exclusao(%s, %s)", (usuario_id, admin))
    try:
        supabase.remover_usuario(str(usuario_id))
    except supabase.SupabaseErro:
        log.warning("remoção do usuário %s no Auth falhou; a tarefa diária tenta de novo", usuario_id)
        return False
    return True
