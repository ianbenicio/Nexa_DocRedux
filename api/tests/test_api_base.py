"""Story 4.1: autenticação, papéis, termo, /me, health, CORS, erros do §9."""
from __future__ import annotations

import os

from tests.conftest import requer_db, token

pytestmark = requer_db


def _erro(r, status, codigo):
    assert r.status_code == status, r.text
    assert r.json()["erro"]["codigo"] == codigo
    assert r.json()["erro"]["mensagem"]


def test_sem_token_e_token_invalido(client, conta):
    _erro(client.get("/me"), 401, "nao_autenticado")
    _erro(client.get("/me", headers={"Authorization": "Bearer xyz"}), 401, "nao_autenticado")
    c = conta()
    _erro(client.get("/me", headers={"Authorization": f"Bearer {token(c.id, exp_s=-10)}"}), 401, "nao_autenticado")
    _erro(client.get("/me", headers={"Authorization": f"Bearer {token(c.id, segredo='x' * 40)}"}), 401,
          "nao_autenticado")


def test_admin_403_e_papel_lido_do_banco(client, conta, admin):
    c = conta()
    _erro(client.delete(f"/admin/usuarios/{c.id}", headers=c.h), 403, "proibido")
    adm = conta(papel="admin")
    _erro(client.delete("/admin/usuarios/00000000-0000-0000-0000-000000000000", headers=adm.h), 404,
          "nao_encontrado")
    # rebaixado no banco: o mesmo token perde o acesso na hora
    admin.execute("update public.perfis_usuario set papel = 'usuario' where id = %s", (adm.id,))
    _erro(client.delete(f"/admin/usuarios/{c.id}", headers=adm.h), 403, "proibido")


def test_termo_pendente_nas_rotas_de_envio(client, conta):
    c = conta(termo=False)
    corpo = {"nome_arquivo": "a.pdf", "tamanho_bytes": 10}
    _erro(client.post("/documentos", json=corpo, headers={**c.h, "Idempotency-Key": "k" * 10}), 403,
          "termo_pendente")
    doc = "00000000-0000-0000-0000-000000000000"
    _erro(client.post(f"/documentos/{doc}/orcamento", json={}, headers=c.h), 403, "termo_pendente")
    _erro(client.post(f"/documentos/{doc}/confirmar", json={"creditos_esperados": 4}, headers=c.h), 403,
          "termo_pendente")
    assert client.get("/me", headers=c.h).json()["termo_aceito"] is False


def test_aceitar_termo(client, conta):
    c = conta(termo=False)
    _erro(client.post("/me/termo", json={"versao": "0"}, headers=c.h), 422, "validacao")
    r = client.post("/me/termo", json={"versao": "1"}, headers=c.h)
    assert r.status_code == 200 and r.json()["termo_versao"] == "1" and r.json()["termo_aceito_em"]
    assert client.get("/me", headers=c.h).json()["termo_aceito"] is True


def test_me_e_senha_trocada(client, conta):
    c = conta()
    me = client.get("/me", headers=c.h).json()
    assert me["saldo"] == 150 and me["senha_provisoria"] is True and me["email"] == c.email
    assert client.post("/me/senha-trocada", headers=c.h).json() == {"senha_provisoria": False}
    assert client.get("/me", headers=c.h).json()["senha_provisoria"] is False


def test_conta_em_exclusao_401_com_jwt_valido(client, conta, admin):
    c = conta()
    admin.execute("update public.perfis_usuario set exclusao_conta_em = now() where id = %s", (c.id,))
    _erro(client.get("/me", headers=c.h), 401, "nao_autenticado")


def test_health_sem_autenticacao(client, admin):
    admin.execute("delete from public.workers where worker_id = 'teste-health'")
    r = client.get("/health")
    assert r.json()["db"] is True and r.json()["versao"]
    admin.execute("insert into public.workers (worker_id, host) values ('teste-health', 'pytest')")
    try:
        assert client.get("/health").json()["worker"] is True
    finally:
        admin.execute("delete from public.workers where worker_id = 'teste-health'")


def test_cors_so_web_origin(client):
    origem = os.environ["WEB_ORIGIN"]
    ok = client.options("/me", headers={"Origin": origem, "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == origem
    fora = client.options("/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in fora.headers


def test_rota_inexistente_no_formato_do_spec(client):
    _erro(client.get("/nao-existe"), 404, "nao_encontrado")


def test_api_conecta_como_docredux_backend(client):
    from app.db import transacao
    with transacao() as conn:
        assert conn.execute("select current_user as u").fetchone()["u"] == "docredux_backend"
        assert conn.prepare_threshold is None
