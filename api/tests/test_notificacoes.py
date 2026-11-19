"""Story 4.7: avisos por e-mail (cliente falso; nenhum teste chama o Resend de verdade)."""
from __future__ import annotations

import threading

import pytest

from app import notificacoes
from tests.conftest import doc_triado, requer_db
from tests.test_worker import EmailFalso

pytestmark = requer_db


@pytest.fixture
def conn():
    from app.db import conectar
    with conectar(autocommit=True) as c:
        yield c


def _status(admin, doc, evento="pronto"):
    return admin.execute("select status::text, tentativas, erro, provedor_id from public.notificacoes "
                         "where documento_id = %s and evento = %s", (doc, evento)).fetchone()


def test_desligado_sem_chave(conta, admin, conn, monkeypatch):
    monkeypatch.setattr(notificacoes, "cliente_padrao", lambda: None)
    c = conta()
    d = doc_triado(admin, c, status="pronto")
    assert notificacoes.notificar(conn, d, "pronto") == "desligado"
    assert _status(admin, d)["status"] == "desligado"


def test_idempotente_por_evento_e_entre_workers(conta, admin, monkeypatch):
    from app.db import conectar
    c = conta()
    d = doc_triado(admin, c, status="pronto")
    email = EmailFalso()
    res = []

    def vai():
        with conectar(autocommit=True) as cx:
            res.append(notificacoes.notificar(cx, d, "pronto", email))
    ts = [threading.Thread(target=vai) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with conectar(autocommit=True) as cx:
        assert notificacoes.notificar(cx, d, "pronto", email) == "enviado"  # job repetido
    assert len(email.enviados) == 1
    assert email.enviados[0]["chave"] == f"docredux/{d}/pronto" and email.enviados[0]["para"] == c.email
    assert _status(admin, d)["provedor_id"] == "re_1"


def test_falha_nao_derruba_e_tenta_ate_3(conta, admin, conn):
    c = conta()
    d = doc_triado(admin, c, status="pronto")
    email = EmailFalso(falhar=10)
    assert notificacoes.notificar(conn, d, "pronto", email) == "falhou"
    s = _status(admin, d)
    assert s["status"] == "falhou" and s["tentativas"] == 1 and "500" in s["erro"]
    assert admin.execute("select status::text from public.documentos where id = %s", (d,)).fetchone()["status"] == "pronto"
    notificacoes.reenviar_pendentes(conn, email)
    notificacoes.reenviar_pendentes(conn, email)
    notificacoes.reenviar_pendentes(conn, email)
    assert _status(admin, d)["tentativas"] == 3 and email.enviados == []
    # recupera na segunda tentativa
    d2 = doc_triado(admin, c, status="pronto")
    e2 = EmailFalso(falhar=1)
    notificacoes.notificar(conn, d2, "pronto", e2)
    notificacoes.reenviar_pendentes(conn, e2)
    assert _status(admin, d2)["status"] == "enviado" and len(e2.enviados) == 1


def test_sem_destinatario(conta, admin, conn):
    c = conta()
    email = EmailFalso()
    d = doc_triado(admin, c, status="excluido")
    assert notificacoes.notificar(conn, d, "pronto", email) == "sem_destinatario"
    c2 = conta()
    d2 = doc_triado(admin, c2, status="pronto")
    admin.execute("update public.perfis_usuario set email = null where id = %s", (c2.id,))
    assert notificacoes.notificar(conn, d2, "pronto", email) == "sem_destinatario"
    assert email.enviados == []


def test_prazo_da_revisao_vem_do_config(conta, admin, conn):
    c = conta()
    email = EmailFalso()
    admin.execute("""update public.config set valor = '"5 dias úteis"' where chave = 'notificacao_prazo_revisao'""")
    try:
        d = doc_triado(admin, c, status="em_revisao")
        notificacoes.notificar(conn, d, "em_revisao", email)
    finally:
        admin.execute("update public.config set valor = 'null' where chave = 'notificacao_prazo_revisao'")
    assert "5 dias úteis" in email.enviados[0]["texto"]
    d2 = doc_triado(admin, c, status="em_revisao")
    notificacoes.notificar(conn, d2, "em_revisao", email)
    assert "até" not in email.enviados[1]["texto"]  # Q19: nulo → não promete prazo


def test_resend_cliente_cabecalhos(monkeypatch):
    capturado = {}

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "abc"}

    def post(url, **kw):
        capturado.update(url=url, **kw)
        return Resp()
    monkeypatch.setattr(notificacoes.httpx, "post", post)
    cli = notificacoes.ResendCliente("re_teste", "DocRedux <avisos@exemplo.com>")
    assert cli.enviar(para="a@b.c", assunto="x", texto="y", chave_idempotencia="docredux/1/pronto") == "abc"
    assert capturado["url"] == "https://api.resend.com/emails"
    assert capturado["headers"]["Idempotency-Key"] == "docredux/1/pronto"
    assert capturado["json"]["to"] == ["a@b.c"]


def test_texto_sem_nome_do_documento():
    assunto, texto = notificacoes.montar("pronto", "abc", template_novo=False, prazo_revisao=None)
    assert texto.count("/documentos/abc") == 1 and ".pdf" not in texto
