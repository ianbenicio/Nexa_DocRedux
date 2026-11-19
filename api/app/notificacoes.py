"""Avisos por e-mail via Resend (story 4.7, DT-22, Q8).

- Dois eventos: `pronto` (primeira entrega pronto) e `em_revisao` (gate bloqueado ou recusa template_novo).
- Idempotente por (documento_id, evento): uma linha em `notificacoes` (único) e o cabeçalho
  `Idempotency-Key` do Resend com a mesma chave. A linha é travada (`FOR UPDATE SKIP LOCKED`) durante o
  envio: dois workers nunca enviam o mesmo aviso.
- Falha no envio nunca derruba o job: a notificação fica `falhou` e o laço do worker tenta de novo até
  `MAX_TENTATIVAS` vezes.
- O e-mail leva só um texto fixo e o link `{WEB_URL}/documentos/{id}`: nem nome nem conteúdo do documento.
- A chave vem de RESEND_API_KEY (variável de ambiente). Sem ela, o aviso fica `desligado`.
"""
from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

import httpx
import psycopg

from .config import config

log = logging.getLogger("docredux.notificacoes")

RESEND_URL = "https://api.resend.com/emails"
MAX_TENTATIVAS = 3
EVENTOS = ("pronto", "em_revisao")


class ClienteEmail(Protocol):
    def enviar(self, *, para: str, assunto: str, texto: str, chave_idempotencia: str) -> str:
        """Envia e devolve o id do provedor. Levanta exceção em qualquer falha."""


class ResendCliente:
    def __init__(self, api_key: str, remetente: str, timeout_s: float = 10.0):
        self._api_key, self._remetente, self._timeout = api_key, remetente, timeout_s

    def enviar(self, *, para: str, assunto: str, texto: str, chave_idempotencia: str) -> str:
        r = httpx.post(RESEND_URL, timeout=self._timeout,
                       headers={"Authorization": f"Bearer {self._api_key}", "Idempotency-Key": chave_idempotencia},
                       json={"from": self._remetente, "to": [para], "subject": assunto, "text": texto})
        if r.status_code >= 400:
            raise RuntimeError(f"Resend HTTP {r.status_code}")  # sem corpo: pode ecoar o destinatário
        return str(r.json().get("id", ""))


def cliente_padrao() -> ClienteEmail | None:
    c = config()
    if not c.resend_api_key or not c.resend_from:
        return None
    return ResendCliente(c.resend_api_key, c.resend_from)


def chave_idempotencia(documento_id: UUID | str, evento: str) -> str:
    return f"docredux/{documento_id}/{evento}"


def montar(evento: str, documento_id: UUID | str, *, template_novo: bool, prazo_revisao: str | None) -> tuple[str, str]:
    """(assunto, texto). Texto fixo + link; nunca o nome nem o conteúdo do documento."""
    link = f"{config().web_url}/documentos/{documento_id}"
    rodape = "\n\nEste é um aviso automático do piloto DocRedux. Não responda a este e-mail."
    if evento == "pronto":
        return ("Seu documento está pronto",
                f"Olá!\n\nUm documento que você enviou ao DocRedux está pronto para baixar.\n\nAbra: {link}{rodape}")
    if template_novo:
        corpo = ("Um documento que você enviou ao DocRedux usa um modelo que ainda não conhecemos. "
                 "O modelo pode passar a ser aceito em até 2 meses; você pode apagar este envio quando quiser.")
    else:
        corpo = "Um documento que você enviou ao DocRedux entrou em revisão antes da entrega."
        if prazo_revisao:
            corpo += f" A revisão deve terminar em até {prazo_revisao}."
    return "Seu documento está em revisão", f"Olá!\n\n{corpo}\n\nAcompanhe: {link}{rodape}"


def notificar(conn: psycopg.Connection, documento_id: UUID, evento: str, cliente: ClienteEmail | None = None) -> str | None:
    """Registra o aviso (uma vez por documento e evento) e tenta enviar. Nunca levanta exceção.
    `conn` em autocommit (o worker) ou sem transação aberta. Devolve o status final ou None."""
    if evento not in EVENTOS:
        raise ValueError(evento)
    try:
        with conn.transaction():
            row = conn.execute(
                """insert into public.notificacoes (documento_id, usuario_id, evento)
                   select id, usuario_id, %s::public.evento_notificacao from public.documentos where id = %s
                   on conflict (documento_id, evento) do nothing returning id""", (evento, documento_id)).fetchone()
        if row is None:
            row = conn.execute("select id from public.notificacoes where documento_id = %s and evento = %s",
                               (documento_id, evento)).fetchone()
        return tentar(conn, row["id"], cliente if cliente is not None else cliente_padrao())
    except Exception as e:  # noqa: BLE001 — AC 6: falha no aviso não derruba o job
        log.warning("aviso %s do documento %s não registrado: %s", evento, documento_id, type(e).__name__)
        return None


def tentar(conn: psycopg.Connection, notificacao_id: int, cliente: ClienteEmail | None) -> str | None:
    with conn.transaction():
        n = conn.execute(
            """select n.id, n.documento_id, n.evento::text, n.status::text, n.tentativas,
                      d.status::text as doc_status, d.motivo_recusa::text, d.exclusao_pendente,
                      u.email, u.exclusao_conta_em
                 from public.notificacoes n
                 join public.documentos d on d.id = n.documento_id
                 join public.perfis_usuario u on u.id = n.usuario_id
                where n.id = %s
                for update of n skip locked""", (notificacao_id,)).fetchone()
        if n is None:  # outro worker está enviando
            return None
        if n["status"] in ("enviado", "desligado", "sem_destinatario"):
            return n["status"]
        if (not n["email"] or n["exclusao_conta_em"] is not None or n["exclusao_pendente"]
                or n["doc_status"] in ("excluido", "expirado")):
            return _fechar(conn, n["id"], "sem_destinatario")
        if cliente is None:
            return _fechar(conn, n["id"], "desligado")
        if n["tentativas"] >= MAX_TENTATIVAS:
            return n["status"]
        prazo = conn.execute("select valor #>> '{}' as v from public.config where chave = 'notificacao_prazo_revisao'"
                             ).fetchone()
        assunto, texto = montar(n["evento"], n["documento_id"],
                                template_novo=n["motivo_recusa"] == "template_novo",
                                prazo_revisao=(prazo or {}).get("v"))
        try:
            provedor_id = cliente.enviar(para=n["email"], assunto=assunto, texto=texto,
                                         chave_idempotencia=chave_idempotencia(n["documento_id"], n["evento"]))
        except Exception as e:  # noqa: BLE001
            conn.execute(
                """update public.notificacoes set status = 'falhou', tentativas = tentativas + 1, erro = %s
                    where id = %s""", (f"{type(e).__name__}: {str(e)[:200]}", n["id"]))
            log.warning("aviso %s do documento %s falhou (tentativa %s)", n["evento"], n["documento_id"],
                        n["tentativas"] + 1)
            return "falhou"
        conn.execute(
            """update public.notificacoes set status = 'enviado', tentativas = tentativas + 1, erro = null,
                      provedor_id = %s, enviado_em = now() where id = %s""", (provedor_id, n["id"]))
        return "enviado"


def _fechar(conn: psycopg.Connection, notificacao_id: int, status: str) -> str:
    conn.execute("update public.notificacoes set status = %s::public.status_notificacao where id = %s",
                 (status, notificacao_id))
    return status


def reenviar_pendentes(conn: psycopg.Connection, cliente: ClienteEmail | None = None, limite: int = 20) -> int:
    """Laço do worker: tenta de novo os avisos `pendente`/`falhou` com menos de 3 tentativas (até 1 dia)."""
    cliente = cliente if cliente is not None else cliente_padrao()
    ids = [r["id"] for r in conn.execute(
        """select id from public.notificacoes
            where status in ('pendente', 'falhou') and tentativas < %s and criado_em > now() - interval '1 day'
            order by criado_em limit %s""", (MAX_TENTATIVAS, limite)).fetchall()]
    enviados = 0
    for i in ids:
        try:
            enviados += tentar(conn, i, cliente) == "enviado"
        except Exception as e:  # noqa: BLE001
            log.warning("reenvio do aviso %s falhou: %s", i, type(e).__name__)
    return enviados
