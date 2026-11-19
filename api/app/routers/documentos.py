"""Documentos (spec §9 Usuário; stories 4.2, 4.3, 4.5)."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from .. import ciclo, supabase
from ..auth import Usuario, exigir_termo, usuario_atual
from ..creditos import Opcoes, orcar
from ..db import config_valor, transacao
from ..erros import ApiErro

router = APIRouter(prefix="/documentos")

MB = 2**20


def usuario_com_termo(u: Usuario = Depends(usuario_atual)) -> Usuario:
    return exigir_termo(u)


def caminho_parte(usuario_id, documento_id, n: int) -> str:
    return f"{usuario_id}/{documento_id}/original.part{n:02d}"


def tamanhos_partes(tamanho: int, parte_bytes: int) -> list[int]:
    n = max(1, math.ceil(tamanho / parte_bytes))
    return [min(parte_bytes, tamanho - i * parte_bytes) for i in range(n)]


def _limites(conn) -> tuple[int, int, int]:
    max_mb = int(config_valor(conn, "max_mb", 80))
    parte_mb = int(config_valor(conn, "parte_mb", 42))
    teto_mb = int(config_valor(conn, "storage_teto_mb", 900))
    return max_mb * MB, parte_mb * MB, teto_mb * MB


def uso_storage(conn) -> int:
    """DT-20: originais + saídas de documentos com conteúdo (não excluídos/expirados)."""
    return conn.execute(
        """select coalesce(sum(d.tamanho_bytes), 0)
                  + coalesce((select sum(e.tamanho_bytes) from public.entregas e
                               join public.documentos x on x.id = e.documento_id
                              where x.status not in ('excluido', 'expirado')), 0) as uso
             from public.documentos d where d.status not in ('excluido', 'expirado')""").fetchone()["uso"]


def _resumo_envio(doc: dict, parte_bytes: int) -> dict:
    partes = tamanhos_partes(doc["tamanho_bytes"], parte_bytes)
    return {
        "documento_id": str(doc["id"]), "status": doc["status"], "partes": len(partes),
        "bucket": supabase.ORIGINAIS,
        "caminhos": [{"parte": i + 1, "caminho": caminho_parte(doc["usuario_id"], doc["id"], i + 1),
                      "tamanho_bytes": t} for i, t in enumerate(partes)],
    }


# ---------------------------------------------------------------- envio (4.3)
class NovoDocumento(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome_arquivo: str = Field(min_length=1, max_length=255)
    tamanho_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


@router.post("")
def criar_documento(corpo: NovoDocumento, u: Usuario = Depends(usuario_com_termo),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not idempotency_key or not 8 <= len(idempotency_key) <= 200:
        raise ApiErro("validacao", "Cabeçalho Idempotency-Key obrigatório (8 a 200 caracteres).")
    sha = corpo.sha256.lower() if corpo.sha256 else None
    with transacao() as conn:
        max_bytes, parte_bytes, teto = _limites(conn)
        if corpo.tamanho_bytes > max_bytes:
            raise ApiErro("limite_excedido")
        existente = conn.execute(
            "select * from public.documentos where usuario_id = %s and idempotency_key = %s for update",
            (u.id, idempotency_key)).fetchone()
        if existente is None:
            # Trava de transação: dois envios simultâneos não passam juntos pelo teto (DT-20).
            conn.execute("select pg_advisory_xact_lock(hashtext('docredux_storage_teto'))")
            if uso_storage(conn) + corpo.tamanho_bytes > teto:
                raise ApiErro("capacidade_piloto")
            doc = conn.execute(
                """insert into public.documentos (usuario_id, idempotency_key, nome_arquivo, tamanho_bytes, partes, sha256)
                   values (%s, %s, %s, %s, %s, %s)
                   on conflict (usuario_id, idempotency_key) do nothing returning *""",
                (u.id, idempotency_key, corpo.nome_arquivo, corpo.tamanho_bytes,
                 len(tamanhos_partes(corpo.tamanho_bytes, parte_bytes)), sha)).fetchone()
            if doc is None:  # corrida com a mesma chave
                doc = conn.execute("select * from public.documentos where usuario_id = %s and idempotency_key = %s",
                                   (u.id, idempotency_key)).fetchone()
            return _resumo_envio(doc, parte_bytes)
        if existente["tamanho_bytes"] != corpo.tamanho_bytes or (existente["sha256"] or None) != sha:
            raise ApiErro("validacao", "Idempotency-Key já usada com outro arquivo.")
        if existente["status"] == "upload_falhou":  # NFR-2: nova tentativa do mesmo documento (§8)
            existente = conn.execute(
                "update public.documentos set status = 'aguardando_upload' where id = %s returning *",
                (existente["id"],)).fetchone()
        return _resumo_envio(existente, parte_bytes)


@router.post("/{documento_id}/upload-concluido")
def upload_concluido(documento_id: UUID, u: Usuario = Depends(usuario_atual)):
    with transacao() as conn:
        doc = ciclo.travar_documento(conn, documento_id, u.id)
        if doc["status"] != "aguardando_upload":
            if doc["status"] in ("triagem", "aguardando_confirmacao", "recusado"):
                return {"documento_id": str(documento_id), "status": doc["status"]}  # repetição
            raise ApiErro("estado_invalido")
        _, parte_bytes, _ = _limites(conn)
        esperados = tamanhos_partes(doc["tamanho_bytes"], parte_bytes)
        prefixo = f"{doc['usuario_id']}/{doc['id']}"
        try:
            achados = supabase.tamanhos(supabase.ORIGINAIS, prefixo)
        except supabase.SupabaseErro as e:
            raise ApiErro("indisponivel") from e
        errados, faltando = [], []
        for i, t in enumerate(esperados, start=1):
            nome = f"original.part{i:02d}"
            if nome not in achados:
                faltando.append(i)
            elif achados[nome] != t:
                errados.append(f"{prefixo}/{nome}")
        if errados:
            supabase.apagar(supabase.ORIGINAIS, errados)
        if faltando or errados:
            conn.execute("update public.documentos set status = 'upload_falhou' where id = %s", (documento_id,))
            return {"documento_id": str(documento_id), "status": "upload_falhou",
                    "partes_faltando": faltando, "partes_com_tamanho_errado": len(errados)}
        conn.execute("update public.documentos set status = 'triagem' where id = %s", (documento_id,))
        conn.execute("insert into public.jobs (documento_id, tipo) values (%s, 'triagem')", (documento_id,))
    return {"documento_id": str(documento_id), "status": "triagem"}


# ---------------------------------------------------------------- leitura (4.3)
CAMPOS = """d.id, d.nome_arquivo, d.tamanho_bytes, d.status::text, d.motivo_recusa::text, d.paginas, d.palavras,
            d.tipo::text, d.perfil_id, d.template_novo, d.opcoes, d.orcamento, d.creditos_reservados, d.partes,
            d.entrega_atual, d.reprocessando, d.exclusao_pendente, d.criado_em, d.confirmado_em, d.entregue_em,
            d.expira_em"""


@router.get("")
def listar(cursor: str | None = None, limite: int = 20, u: Usuario = Depends(usuario_atual)):
    limite = max(1, min(limite, 100))
    antes = None
    if cursor:
        try:
            antes = datetime.fromisoformat(cursor)
        except ValueError as e:
            raise ApiErro("validacao", "cursor inválido") from e
    with transacao() as conn:
        rows = conn.execute(
            f"""select {CAMPOS} from public.documentos d
                 where d.usuario_id = %s and d.status <> 'excluido' and (%s::timestamptz is null or d.criado_em < %s)
                 order by d.criado_em desc limit %s""", (u.id, antes, antes, limite + 1)).fetchall()
    proximo = rows[limite - 1]["criado_em"].isoformat() if len(rows) > limite else None
    return {"itens": [_publico(r) for r in rows[:limite]], "proximo_cursor": proximo}


def _publico(r: dict) -> dict:
    r = dict(r)
    r["id"] = str(r["id"])
    r["opcoes_disponiveis"] = {
        "sintese": r["tipo"] == "relatorio", "docx": True, "manter_capa_elementos": True,
        "fracoes": [0.2, 0.35, 0.5] if r["tipo"] == "relatorio" else [],
    }
    return r


@router.get("/{documento_id}")
def detalhe(documento_id: UUID, u: Usuario = Depends(usuario_atual)):
    with transacao() as conn:
        doc = conn.execute(f"select {CAMPOS} from public.documentos d where d.id = %s and d.usuario_id = %s",
                           (documento_id, u.id)).fetchone()
        if doc is None:
            raise ApiErro("nao_encontrado")
        out = _publico(doc)
        out["entrega"] = None
        out["feedback"] = None
        out["problemas"] = []
        if doc["status"] == "pronto" and doc["entrega_atual"]:  # nunca em em_revisao (§9)
            e = conn.execute(
                """select versao, paginas_depois, cobertura, gate::text, relatorio, caminho_docx is not null as tem_docx,
                          criado_em from public.entregas where documento_id = %s and versao = %s""",
                (documento_id, doc["entrega_atual"])).fetchone()
            out["entrega"] = e
            out["feedback"] = conn.execute(
                "select nota, comentario, atualizado_em from public.feedback_usuario where documento_id = %s and versao = %s",
                (documento_id, doc["entrega_atual"])).fetchone()
            out["problemas"] = conn.execute(
                """select id, versao, pagina, tipo::text, descricao, status::text, criado_em
                     from public.problemas_marcados where documento_id = %s order by id""", (documento_id,)).fetchall()
    return out


@router.get("/{documento_id}/arquivo")
def arquivo(documento_id: UUID, formato: Literal["pdf", "docx"] = "pdf", u: Usuario = Depends(usuario_atual)):
    with transacao() as conn:
        row = conn.execute(
            """select d.status::text, d.nome_arquivo, e.caminho_pdf, e.caminho_docx
                 from public.documentos d
                 left join public.entregas e on e.documento_id = d.id and e.versao = d.entrega_atual
                where d.id = %s and d.usuario_id = %s""", (documento_id, u.id)).fetchone()
    if row is None:
        raise ApiErro("nao_encontrado")
    if row["status"] != "pronto" or not row["caminho_pdf"]:
        raise ApiErro("estado_invalido")
    caminho = row["caminho_pdf"] if formato == "pdf" else row["caminho_docx"]
    if not caminho:
        raise ApiErro("nao_encontrado", "O DOCX não foi pedido para este documento.")
    base = row["nome_arquivo"].rsplit(".", 1)[0] or "documento"
    try:
        url = supabase.assinar(supabase.SAIDAS, caminho, 60, baixar_como=f"{base}_DocRedux.{formato}")
    except supabase.SupabaseErro as e:
        raise ApiErro("indisponivel") from e
    return {"url": url, "expira_em_s": 60, "formato": formato}


# ---------------------------------------------------------------- orçamento e confirmação (4.2)
class PedidoOrcamento(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opcoes: Opcoes = Opcoes()


class PedidoConfirmacao(PedidoOrcamento):
    creditos_esperados: int = Field(ge=0)


def _orcar_documento(doc: dict, opcoes: Opcoes) -> dict:
    if doc["status"] != "aguardando_confirmacao":
        raise ApiErro("estado_invalido")
    if opcoes.modo == "sintese" and doc["tipo"] != "relatorio":  # Q4
        raise ApiErro("validacao", "A síntese só está disponível para relatórios.")
    return orcar(doc["paginas"], doc["palavras"] or 0, opcoes, doc["template_novo"])


@router.post("/{documento_id}/orcamento")
def orcamento(documento_id: UUID, corpo: PedidoOrcamento, u: Usuario = Depends(usuario_com_termo)):
    with transacao() as conn:
        doc = conn.execute("select * from public.documentos where id = %s and usuario_id = %s",
                           (documento_id, u.id)).fetchone()
        if doc is None:
            raise ApiErro("nao_encontrado")
        o = _orcar_documento(doc, corpo.opcoes)
    return {**o, "saldo": u.saldo, "saldo_depois": u.saldo - o["total"], "suficiente": u.saldo >= o["total"]}


@router.post("/{documento_id}/confirmar")
def confirmar(documento_id: UUID, corpo: PedidoConfirmacao, u: Usuario = Depends(usuario_com_termo)):
    with transacao() as conn:
        doc = ciclo.travar_documento(conn, documento_id, u.id)
        o = _orcar_documento(doc, corpo.opcoes)
        if o["total"] != corpo.creditos_esperados:
            raise ApiErro("orcamento_mudou")
        opcoes = corpo.opcoes.model_dump()
        conn.execute("update public.documentos set opcoes = %s, orcamento = %s where id = %s",
                     (Jsonb(opcoes), Jsonb(o), documento_id))
        try:
            with conn.transaction():
                conn.execute("select private.credito_reservar(%s)", (documento_id,))
        except pg_errors.RaiseException as e:
            if (e.diag.message_primary or "") == "saldo_insuficiente":
                raise ApiErro("saldo_insuficiente") from e
            if (e.diag.message_primary or "") == "reserva_aberta":
                raise ApiErro("estado_invalido") from e
            raise
        conn.execute(
            """update public.documentos set status = 'na_fila', creditos_reservados = %s, confirmado_em = now()
                where id = %s""", (o["total"], documento_id))
        conn.execute("insert into public.jobs (documento_id, tipo) values (%s, 'processar')", (documento_id,))
        conn.execute(
            """insert into public.metricas_entrega
                 (documento_id, usuario_id, interno, tipo, perfil_id, template_novo, modo, fracao_pedida, docx,
                  manter_capa_elementos, creditos, paginas_antes, palavras_original)
               select d.id, d.usuario_id, u.interno, d.tipo, d.perfil_id, d.template_novo, %s, %s, %s, %s, %s,
                      d.paginas, d.palavras
                 from public.documentos d join public.perfis_usuario u on u.id = d.usuario_id where d.id = %s
               on conflict (documento_id) do update set modo = excluded.modo, fracao_pedida = excluded.fracao_pedida,
                  docx = excluded.docx, manter_capa_elementos = excluded.manter_capa_elementos,
                  creditos = excluded.creditos, atualizado_em = now()""",
            (opcoes["modo"], opcoes["fracao"], opcoes["docx"], opcoes["manter_capa_elementos"], o["total"],
             documento_id))
        saldo = conn.execute("select saldo from public.perfis_usuario where id = %s", (u.id,)).fetchone()["saldo"]
    return {"documento_id": str(documento_id), "status": "na_fila", "creditos": o["total"], "saldo": saldo}


# ---------------------------------------------------------------- pós-entrega e ciclo (4.5)
class Avaliacao(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nota: int = Field(ge=1, le=5)
    comentario: str | None = Field(default=None, max_length=2000)


@router.put("/{documento_id}/avaliacao")
def avaliar(documento_id: UUID, corpo: Avaliacao, u: Usuario = Depends(usuario_atual)):
    with transacao() as conn:
        doc = ciclo.travar_documento(conn, documento_id, u.id)
        if doc["status"] != "pronto" or not doc["entrega_atual"]:
            raise ApiErro("estado_invalido")
        conn.execute(
            """insert into public.feedback_usuario (documento_id, versao, nota, comentario) values (%s, %s, %s, %s)
               on conflict (documento_id, versao) do update set nota = excluded.nota, comentario = excluded.comentario,
                  atualizado_em = now()""", (documento_id, doc["entrega_atual"], corpo.nota, corpo.comentario))
        conn.execute("update public.metricas_entrega set nota_usuario = %s, atualizado_em = now() where documento_id = %s",
                     (corpo.nota, documento_id))
    return {"versao": doc["entrega_atual"], "nota": corpo.nota}


class Problema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pagina: int = Field(ge=1)
    tipo: Literal["erro", "formato"]
    descricao: str | None = Field(default=None, max_length=2000)


@router.post("/{documento_id}/problemas", status_code=201)
def marcar_problema(documento_id: UUID, corpo: Problema, u: Usuario = Depends(usuario_atual)):
    with transacao() as conn:
        doc = ciclo.travar_documento(conn, documento_id, u.id)
        if doc["status"] != "pronto" or not doc["entrega_atual"]:
            raise ApiErro("estado_invalido")
        paginas = conn.execute("select paginas_depois from public.entregas where documento_id = %s and versao = %s",
                               (documento_id, doc["entrega_atual"])).fetchone()["paginas_depois"]
        if corpo.pagina > paginas:
            raise ApiErro("validacao", f"A página deve estar entre 1 e {paginas}.")
        row = conn.execute(
            """insert into public.problemas_marcados (documento_id, versao, pagina, tipo, descricao)
               values (%s, %s, %s, %s, %s) returning id""",
            (documento_id, doc["entrega_atual"], corpo.pagina, corpo.tipo, corpo.descricao)).fetchone()
        coluna = "problemas_erro" if corpo.tipo == "erro" else "problemas_formato"
        conn.execute(f"update public.metricas_entrega set {coluna} = {coluna} + 1, atualizado_em = now() "
                     "where documento_id = %s", (documento_id,))
    return {"id": row["id"], "versao": doc["entrega_atual"]}


@router.post("/{documento_id}/cancelar")
def cancelar(documento_id: UUID, u: Usuario = Depends(usuario_atual)):
    return ciclo.cancelar_documento(u.id, documento_id)


@router.delete("/{documento_id}")
def excluir(documento_id: UUID, u: Usuario = Depends(usuario_atual)):
    return ciclo.excluir_documento(u.id, documento_id)
