"""Story 4.5: avaliação, problemas, cancelar, excluir, expirar, excluir conta (spec §8, §14)."""
from __future__ import annotations

import uuid

import httpx
import os
import pytest

from app import supabase
from tests.conftest import doc_triado, invariantes, requer_db

pytestmark = requer_db


def _erro(r, status, codigo):
    assert r.status_code == status, r.text
    assert r.json()["erro"]["codigo"] == codigo


def _subir(caminho_bucket: str, dados: bytes = b"%PDF-1.4 teste"):
    bucket, caminho = caminho_bucket.split("/", 1)
    r = httpx.post(f"{os.environ['SUPABASE_URL']}/storage/v1/object/{bucket}/{caminho}", content=dados,
                   headers={"apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"], "Content-Type": "application/pdf"})
    assert r.status_code == 200, r.text


def reservar(admin, d, total=4):
    admin.execute("update public.documentos set orcamento = %s::jsonb where id = %s",
                  (f'{{"itens": [], "total": {total}}}', d))
    admin.execute("select private.credito_reservar(%s)", (d,))
    admin.execute("""insert into public.metricas_entrega (documento_id, usuario_id, creditos)
                     select id, usuario_id, %s from public.documentos where id = %s on conflict do nothing""", (total, d))


def doc_com_entrega(admin, c, status="pronto", *, capturar=True) -> uuid.UUID:
    d = doc_triado(admin, c, status="na_fila")
    reservar(admin, d)
    _subir(f"originais/{c.id}/{d}/original.part01")
    _subir(f"saidas/{c.id}/{d}/v1/saida.pdf")
    admin.execute(
        """insert into public.entregas (documento_id, versao, caminho_pdf, relatorio, cobertura, paginas_depois, gate)
           values (%s, 1, %s, %s::jsonb, 1, 5, %s)""",
        (d, f"{c.id}/{d}/v1/saida.pdf",
         '{"modificacoes": [{"tipo": "removido", "descricao": "texto citado", "paginas_original": [1]}]}',
         "ok" if status == "pronto" else "bloqueado"))
    if capturar:
        r = admin.execute("select private.reserva_aberta(%s) as r", (d,)).fetchone()["r"]
        admin.execute("select private.credito_capturar(%s)", (r,))
    admin.execute("update public.documentos set status = %s, entrega_atual = 1 where id = %s", (status, d))
    return d


def status(admin, d):
    return admin.execute("select status::text, exclusao_pendente from public.documentos where id = %s", (d,)).fetchone()


def vazio(c, d) -> bool:
    return (supabase.listar(supabase.ORIGINAIS, f"{c.id}/{d}") == []
            and supabase.listar(supabase.SAIDAS, f"{c.id}/{d}") == [])


@pytest.fixture(autouse=True)
def _invariantes(admin):
    yield
    assert invariantes(admin) == []


# ---------------------------------------------------------------- avaliação e problemas (AC 1)
def test_avaliacao_e_problemas(client, conta, admin):
    c = conta()
    d = doc_com_entrega(admin, c)
    r = client.put(f"/documentos/{d}/avaliacao", json={"nota": 4, "comentario": "bom"}, headers=c.h)
    assert r.json() == {"versao": 1, "nota": 4}
    client.put(f"/documentos/{d}/avaliacao", json={"nota": 2}, headers=c.h)
    f = admin.execute("select nota, comentario from public.feedback_usuario where documento_id = %s", (d,)).fetchall()
    assert f == [{"nota": 2, "comentario": None}]
    _erro(client.post(f"/documentos/{d}/problemas", json={"pagina": 6, "tipo": "erro"}, headers=c.h), 422, "validacao")
    assert client.post(f"/documentos/{d}/problemas", json={"pagina": 5, "tipo": "formato", "descricao": "x"},
                       headers=c.h).status_code == 201
    m = admin.execute("select nota_usuario, problemas_formato, problemas_erro from public.metricas_entrega "
                      "where documento_id = %s", (d,)).fetchone()
    assert m == {"nota_usuario": 2, "problemas_formato": 1, "problemas_erro": 0}
    det = client.get(f"/documentos/{d}", headers=c.h).json()
    assert det["feedback"]["nota"] == 2 and len(det["problemas"]) == 1
    outro = doc_triado(admin, c)
    _erro(client.put(f"/documentos/{outro}/avaliacao", json={"nota": 4}, headers=c.h), 409, "estado_invalido")
    _erro(client.put(f"/documentos/{d}/avaliacao", json={"nota": 6}, headers=c.h), 422, "validacao")


# ---------------------------------------------------------------- cancelar (AC 2)
def test_cancelar(client, conta, admin):
    c = conta()
    d = doc_triado(admin, c, paginas=10)
    assert client.post(f"/documentos/{d}/confirmar", json={"creditos_esperados": 4}, headers=c.h).status_code == 200
    assert c.saldo(admin) == 146
    assert client.post(f"/documentos/{d}/cancelar", headers=c.h).json()["status"] == "cancelado"
    assert c.saldo(admin) == 150
    assert admin.execute("select status::text from public.jobs where documento_id = %s", (d,)).fetchone()["status"] == "cancelado"
    # processando → 409
    d2 = doc_triado(admin, c, paginas=10)
    client.post(f"/documentos/{d2}/confirmar", json={"creditos_esperados": 4}, headers=c.h)
    admin.execute("update public.jobs set status = 'processando' where documento_id = %s", (d2,))
    admin.execute("update public.documentos set status = 'processando' where id = %s", (d2,))
    _erro(client.post(f"/documentos/{d2}/cancelar", headers=c.h), 409, "estado_invalido")
    admin.execute("update public.jobs set status = 'falhou' where documento_id = %s", (d2,))
    admin.execute("select private.credito_estornar(private.reserva_aberta(%s), 'teste', null)", (d2,))
    admin.execute("update public.documentos set status = 'falhou' where id = %s", (d2,))
    # pronto em reprocessamento → 409 e a versão atual continua acessível
    d3 = doc_com_entrega(admin, c)
    admin.execute("update public.documentos set reprocessando = true where id = %s", (d3,))
    _erro(client.post(f"/documentos/{d3}/cancelar", headers=c.h), 409, "estado_invalido")
    assert client.get(f"/documentos/{d3}/arquivo", headers=c.h).status_code == 200


# ---------------------------------------------------------------- excluir (AC 3, 4)
@pytest.mark.parametrize("estado", ["aguardando_upload", "upload_falhou", "recusado", "aguardando_confirmacao",
                                    "cancelado", "falhou"])
def test_excluir_estados_sem_reserva(client, conta, admin, estado):
    c = conta()
    d = doc_triado(admin, c, status="aguardando_upload")
    _subir(f"originais/{c.id}/{d}/original.part01")
    extra = ", motivo_recusa = 'escaneado'" if estado == "recusado" else ""
    admin.execute(f"update public.documentos set status = %s{extra} where id = %s", (estado, d))
    assert client.delete(f"/documentos/{d}", headers=c.h).json()["status"] == "excluido"
    assert status(admin, d)["status"] == "excluido" and vazio(c, d) and c.saldo(admin) == 150


def test_excluir_na_fila_estorna(client, conta, admin):
    c = conta()
    d = doc_triado(admin, c, paginas=10)
    client.post(f"/documentos/{d}/confirmar", json={"creditos_esperados": 4}, headers=c.h)
    assert client.delete(f"/documentos/{d}", headers=c.h).json()["status"] == "excluido"
    assert c.saldo(admin) == 150


def test_excluir_em_revisao_estorna_e_limpa_textos(client, conta, admin):
    c = conta()
    d = doc_com_entrega(admin, c, "em_revisao", capturar=False)
    admin.execute("insert into public.jobs (documento_id, tipo) values (%s, 'juiz')", (d,))
    assert c.saldo(admin) == 146
    assert client.delete(f"/documentos/{d}", headers=c.h).json()["status"] == "excluido"
    assert c.saldo(admin) == 150 and vazio(c, d)
    rel = admin.execute("select relatorio from public.entregas where documento_id = %s", (d,)).fetchone()["relatorio"]
    assert "descricao" not in rel["modificacoes"][0]
    j = admin.execute("select status::text from public.jobs where documento_id = %s and tipo = 'juiz'", (d,)).fetchone()
    assert j["status"] == "cancelado"  # AC 4: nenhuma chamada à Anthropic
    assert admin.execute("select count(*) from public.metricas_entrega where documento_id = %s", (d,)).fetchone()["count"] == 1


def test_excluir_pronto_sem_estorno(client, conta, admin):
    c = conta()
    d = doc_com_entrega(admin, c)
    assert client.delete(f"/documentos/{d}", headers=c.h).json()["status"] == "excluido"
    assert c.saldo(admin) == 146 and vazio(c, d)
    _erro(client.get(f"/documentos/{d}/arquivo", headers=c.h), 409, "estado_invalido")


def test_excluir_processando_fica_pendente_e_worker_conclui(client, conta, admin):
    from app.db import conectar
    from worker import falhas
    c = conta()
    d = doc_triado(admin, c, paginas=10)
    client.post(f"/documentos/{d}/confirmar", json={"creditos_esperados": 4}, headers=c.h)
    job = admin.execute("""update public.jobs set status = 'processando', worker_id = 'w', heartbeat_em = now(),
                                  tentativas = 1 where documento_id = %s returning id""", (d,)).fetchone()["id"]
    admin.execute("update public.documentos set status = 'processando' where id = %s", (d,))
    r = client.delete(f"/documentos/{d}", headers=c.h).json()
    assert r["exclusao_pendente"] is True and status(admin, d) == {"status": "processando", "exclusao_pendente": True}
    with conectar(autocommit=True) as conn:
        falhas.devolver_ou_falhar(conn, job, "fim do job")  # o worker termina (ou cai) e confere a exclusão
    assert status(admin, d) == {"status": "excluido", "exclusao_pendente": False} and c.saldo(admin) == 150


# ---------------------------------------------------------------- expirar (AC 5)
def test_expirar(client, conta, admin):
    from app.db import conectar
    from worker import limpeza
    c = conta()
    na_fila = doc_triado(admin, c, paginas=10)
    client.post(f"/documentos/{na_fila}/confirmar", json={"creditos_esperados": 4}, headers=c.h)
    pronto = doc_com_entrega(admin, c)
    partes = doc_triado(admin, c, status="aguardando_confirmacao")
    _subir(f"originais/{c.id}/{partes}/original.part01")
    _subir(f"originais/{c.id}/{partes}/original.part02")
    proc = doc_triado(admin, c, status="triagem")
    admin.execute("insert into public.jobs (documento_id, tipo, status, worker_id, heartbeat_em) "
                  "values (%s, 'triagem', 'processando', 'w', now())", (proc,))
    ids = [na_fila, pronto, partes, proc]
    admin.execute("update public.documentos set expira_em = now() - interval '1 minute' where id = any(%s)", (ids,))
    with conectar(autocommit=True) as conn:
        r = limpeza.talvez_rodar(conn, forcar=True)
        assert r["expirados"] >= 3 and r["expiracao_pendente"] >= 1
        assert limpeza.talvez_rodar(conn) is None  # uma vez por dia
    for d in (na_fila, pronto, partes):
        assert status(admin, d)["status"] == "expirado" and vazio(c, d)
    assert status(admin, proc) == {"status": "triagem", "exclusao_pendente": True}
    assert c.saldo(admin) == 146  # estornou a reserva do na_fila; a do pronto já foi capturada
    assert admin.execute("select count(*) from public.metricas_entrega where documento_id = %s",
                         (pronto,)).fetchone()["count"] == 1
    # o worker termina a triagem → expirado
    with conectar(autocommit=True) as conn:
        from worker import falhas
        job = admin.execute("select id from public.jobs where documento_id = %s", (proc,)).fetchone()["id"]
        falhas.devolver_ou_falhar(conn, job, "fim")
    assert status(admin, proc) == {"status": "expirado", "exclusao_pendente": False}


# ---------------------------------------------------------------- excluir conta (AC 6)
def test_excluir_conta(client, conta, admin):
    c = conta()
    d = doc_com_entrega(admin, c)
    client.put(f"/documentos/{d}/avaliacao", json={"nota": 5, "comentario": "meu nome é Fulano"}, headers=c.h)
    client.post(f"/documentos/{d}/problemas", json={"pagina": 1, "tipo": "erro", "descricao": "x"}, headers=c.h)
    agregado = admin.execute("select count(*) from public.metricas_entrega").fetchone()["count"]
    _erro(client.request("DELETE", "/me", json={"confirmacao": "sim"}, headers=c.h), 422, "validacao")
    r = client.request("DELETE", "/me", json={"confirmacao": "EXCLUIR"}, headers=c.h)
    assert r.status_code == 202 and r.json()["exclusao_conta"] == "concluida"
    _erro(client.get("/me", headers=c.h), 401, "nao_autenticado")
    p = admin.execute("select email, saldo, exclusao_conta_em from public.perfis_usuario where id = %s", (c.id,)).fetchone()
    assert p["email"] is None and p["saldo"] == 0 and p["exclusao_conta_em"] is not None
    assert admin.execute("select comentario from public.feedback_usuario where documento_id = %s", (d,)).fetchone()["comentario"] is None
    assert admin.execute("select descricao from public.problemas_marcados where documento_id = %s", (d,)).fetchone()["descricao"] is None
    assert status(admin, d)["status"] == "excluido" and vazio(c, d)
    assert not supabase.usuario_existe(str(c.id))
    tipos = [x["tipo"] for x in admin.execute("select tipo::text from public.creditos_ledger where usuario_id = %s "
                                              "order by id", (c.id,)).fetchall()]
    assert tipos[-1] == "ajuste"
    assert admin.execute("select count(*) from public.metricas_entrega").fetchone()["count"] == agregado


def test_excluir_conta_com_documento_processando(client, conta, admin):
    from app.db import conectar
    from worker import falhas
    adm, c = conta(papel="admin"), conta()
    d = doc_triado(admin, c, paginas=10)
    client.post(f"/documentos/{d}/confirmar", json={"creditos_esperados": 4}, headers=c.h)
    job = admin.execute("""update public.jobs set status = 'processando', worker_id = 'w', heartbeat_em = now(),
                                  tentativas = 1 where documento_id = %s returning id""", (d,)).fetchone()["id"]
    admin.execute("update public.documentos set status = 'processando' where id = %s", (d,))
    r = client.delete(f"/admin/usuarios/{c.id}", headers=adm.h)
    assert r.status_code == 202 and r.json() == {"exclusao_conta": "pendente", "documentos_pendentes": 1}
    _erro(client.get("/me", headers=c.h), 401, "nao_autenticado")  # login bloqueado na hora
    assert supabase.usuario_existe(str(c.id))
    u = httpx.get(f"{os.environ['SUPABASE_URL']}/auth/v1/admin/users/{c.id}", headers=supabase._cabecalhos()).json()
    assert u.get("banned_until")  # ban no Auth: novos logins recusados
    with conectar(autocommit=True) as conn:
        falhas.devolver_ou_falhar(conn, job, "fim do job")
    assert status(admin, d)["status"] == "excluido"
    assert not supabase.usuario_existe(str(c.id))
    p = admin.execute("select saldo from public.perfis_usuario where id = %s", (c.id,)).fetchone()
    assert p["saldo"] == 0
    ajuste = admin.execute("select valor, criado_por from public.creditos_ledger where usuario_id = %s and tipo = 'ajuste'",
                           (c.id,)).fetchone()
    assert ajuste["valor"] == -150
