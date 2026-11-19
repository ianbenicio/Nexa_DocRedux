"""Health, perfil, termo, senha, extrato e exclusão da conta (spec §9 Usuário; stories 4.1, 4.2, 4.5)."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import VERSAO, ciclo
from ..auth import Usuario, exigir_admin, usuario_atual
from ..db import transacao
from ..erros import ApiErro

router = APIRouter()


@router.get("/health")
def health():
    """DB ok, algum worker com `visto_em` < 60 s (DT-15), versão. Sem autenticação."""
    try:
        with transacao() as conn:
            worker = conn.execute(
                "select exists (select 1 from public.workers where visto_em > now() - interval '60 seconds') as ok"
            ).fetchone()["ok"]
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "db": False, "worker": False, "versao": VERSAO}, status_code=503)
    return {"ok": bool(worker), "db": True, "worker": bool(worker), "versao": VERSAO}


@router.get("/me")
def me(u: Usuario = Depends(usuario_atual)):
    return {"id": str(u.id), "email": u.email, "papel": u.papel, "saldo": u.saldo,
            "termo_aceito": u.termo_aceito, "termo_versao": u.termo_versao, "termo_versao_atual": u.termo_atual,
            "termo_aceito_em": u.termo_aceito_em, "senha_provisoria": u.senha_provisoria}


class TermoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")
    versao: str = Field(min_length=1, max_length=20)


@router.post("/me/termo")
def aceitar_termo(corpo: TermoEntrada, u: Usuario = Depends(usuario_atual)):
    if corpo.versao != u.termo_atual:
        raise ApiErro("validacao", f"Versão do termo inválida: a atual é {u.termo_atual}.")
    with transacao() as conn:
        row = conn.execute(
            """update public.perfis_usuario set termo_versao = %s, termo_aceito_em = now()
                where id = %s returning termo_versao, termo_aceito_em""", (corpo.versao, u.id)).fetchone()
    return {"termo_versao": row["termo_versao"], "termo_aceito_em": row["termo_aceito_em"]}


@router.post("/me/senha-trocada")
def senha_trocada(u: Usuario = Depends(usuario_atual)):
    with transacao() as conn:
        conn.execute("update public.perfis_usuario set senha_provisoria = false where id = %s", (u.id,))
    return {"senha_provisoria": False}


@router.get("/creditos/extrato")
def extrato(cursor: int | None = None, limite: int = 50, u: Usuario = Depends(usuario_atual)):
    limite = max(1, min(limite, 200))
    with transacao() as conn:
        rows = conn.execute(
            """select id, tipo::text, valor, saldo_apos, documento_id, reserva_id, motivo, criado_em
                 from public.creditos_ledger
                where usuario_id = %s and (%s::bigint is null or id < %s)
                order by id desc limit %s""", (u.id, cursor, cursor, limite + 1)).fetchall()
    proximo = rows[limite - 1]["id"] if len(rows) > limite else None
    return {"saldo": u.saldo, "itens": rows[:limite], "proximo_cursor": proximo}


class ExclusaoConta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmacao: Literal["EXCLUIR"]


@router.delete("/me", status_code=202)
def excluir_me(corpo: ExclusaoConta, u: Usuario = Depends(usuario_atual)):
    return ciclo.excluir_conta(u.id, admin=None)


@router.delete("/admin/usuarios/{usuario_id}", status_code=202)
def excluir_usuario_admin(usuario_id: str, u: Usuario = Depends(usuario_atual)):
    exigir_admin(u)
    try:
        alvo = UUID(usuario_id)
    except ValueError as e:
        raise ApiErro("nao_encontrado") from e
    with transacao() as conn:
        if conn.execute("select 1 from public.perfis_usuario where id = %s", (alvo,)).fetchone() is None:
            raise ApiErro("nao_encontrado")
    return ciclo.excluir_conta(alvo, admin=u.id)
