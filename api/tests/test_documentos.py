"""Stories 4.2 (orçamento, confirmação, extrato) e 4.3 (envio idempotente em partes)."""
from __future__ import annotations

import os
import threading
import uuid

import httpx
import pytest

from tests.conftest import doc_triado, invariantes, requer_db, token

pytestmark = requer_db
MB = 2**20


def _erro(r, status, codigo):
    assert r.status_code == status, r.text
    assert r.json()["erro"]["codigo"] == codigo


def subir_parte(c, caminho: str, dados: bytes, *, como_usuario=True) -> httpx.Response:
    """Upload direto ao Storage como o navegador faria (JWT do usuário + política do bucket)."""
    # Sem x-upsert: o usuário não sobrescreve parte (sem política de UPDATE); a parte de tamanho errado
    # é apagada pela API no upload-concluido antes da nova tentativa.
    h = {"apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"], "Content-Type": "application/octet-stream"}
    if como_usuario:
        h["Authorization"] = f"Bearer {token(c.id)}"
    return httpx.post(f"{os.environ['SUPABASE_URL']}/storage/v1/object/originais/{caminho}", content=dados,
                      headers=h, timeout=60)


def novo(client, c, tamanho, chave=None, **kw):
    return client.post("/documentos", json={"nome_arquivo": "rel.pdf", "tamanho_bytes": tamanho, **kw},
                       headers={**c.h, "Idempotency-Key": chave or str(uuid.uuid4())})


# ---------------------------------------------------------------- 4.3
def test_criar_idempotente_e_partes(client, conta, admin):
    c = conta()
    r = novo(client, c, 62 * 10**6, chave="chave-62mb-1")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "aguardando_upload" and j["partes"] == 2
    assert [p["caminho"] for p in j["caminhos"]] == [f"{c.id}/{j['documento_id']}/original.part01",
                                                     f"{c.id}/{j['documento_id']}/original.part02"]
    assert sum(p["tamanho_bytes"] for p in j["caminhos"]) == 62 * 10**6
    assert j["caminhos"][0]["tamanho_bytes"] == 42 * MB
    r2 = novo(client, c, 62 * 10**6, chave="chave-62mb-1")
    assert r2.json()["documento_id"] == j["documento_id"]
    n = admin.execute("select count(*) from public.documentos where usuario_id = %s", (c.id,)).fetchone()["count"]
    assert n == 1
    _erro(novo(client, c, 10, chave="chave-62mb-1"), 422, "validacao")  # mesma chave, outro arquivo


def test_sem_chave_e_limite(client, conta):
    c = conta()
    _erro(client.post("/documentos", json={"nome_arquivo": "a.pdf", "tamanho_bytes": 10}, headers=c.h), 422,
          "validacao")
    _erro(novo(client, c, 80 * MB + 1), 413, "limite_excedido")
    assert novo(client, c, 80 * MB).status_code == 200


def test_capacidade_piloto(client, conta, admin):
    c = conta()
    uso = admin.execute("""select coalesce(sum(tamanho_bytes), 0) as u from public.documentos
                            where status not in ('excluido', 'expirado')""").fetchone()["u"]
    teto_mb = uso // MB + 1
    admin.execute("update public.config set valor = to_jsonb(%s::int) where chave = 'storage_teto_mb'", (teto_mb,))
    try:
        _erro(novo(client, c, 2 * MB), 503, "capacidade_piloto")
    finally:
        admin.execute("update public.config set valor = '900' where chave = 'storage_teto_mb'")


def test_upload_concluido_retomada_e_404_de_outro(client, conta, admin):
    c, b = conta(), conta()
    dados = os.urandom(3000)
    j = novo(client, c, len(dados), chave="retomada-1").json()
    doc, cam = j["documento_id"], j["caminhos"][0]["caminho"]
    # B não vê nada do documento de A (AC 8)
    for metodo, rota in [("get", f"/documentos/{doc}"), ("post", f"/documentos/{doc}/upload-concluido"),
                         ("get", f"/documentos/{doc}/arquivo"), ("delete", f"/documentos/{doc}"),
                         ("post", f"/documentos/{doc}/cancelar")]:
        _erro(getattr(client, metodo)(rota, headers=b.h), 404, "nao_encontrado")
    # B não sobe parte no prefixo de A (política do Storage)
    assert subir_parte(b, cam, dados).status_code >= 400
    # upload interrompido: parte truncada → upload_falhou e a parte errada sai do Storage
    assert subir_parte(c, cam, dados[:1000]).status_code == 200
    r = client.post(f"/documentos/{doc}/upload-concluido", headers=c.h).json()
    assert r["status"] == "upload_falhou" and r["partes_com_tamanho_errado"] == 1
    # parte só sobe em aguardando_upload
    assert subir_parte(c, cam, dados).status_code >= 400
    # nova tentativa com a mesma chave → aguardando_upload → upload completo
    assert novo(client, c, len(dados), chave="retomada-1").json()["status"] == "aguardando_upload"
    assert subir_parte(c, cam, dados).status_code == 200
    assert client.post(f"/documentos/{doc}/upload-concluido", headers=c.h).json()["status"] == "triagem"
    job = admin.execute("select tipo::text, status::text from public.jobs where documento_id = %s", (doc,)).fetchall()
    assert job == [{"tipo": "triagem", "status": "na_fila"}]
    # repetição é inofensiva
    assert client.post(f"/documentos/{doc}/upload-concluido", headers=c.h).json()["status"] == "triagem"
    admin.execute("update public.jobs set status = 'cancelado' where documento_id = %s", (doc,))


def test_upload_concluido_parte_faltando(client, conta):
    c = conta()
    j = novo(client, c, 5000).json()
    r = client.post(f"/documentos/{j['documento_id']}/upload-concluido", headers=c.h).json()
    assert r["status"] == "upload_falhou" and r["partes_faltando"] == [1]


def test_listar_e_detalhe(client, conta, admin):
    c = conta()
    ids = [str(doc_triado(admin, c)) for _ in range(3)]
    p1 = client.get("/documentos?limite=2", headers=c.h).json()
    assert len(p1["itens"]) == 2 and p1["proximo_cursor"]
    p2 = client.get("/documentos", params={"limite": 2, "cursor": p1["proximo_cursor"]}, headers=c.h).json()
    assert {i["id"] for i in p1["itens"] + p2["itens"]} == set(ids) and p2["proximo_cursor"] is None
    d = client.get(f"/documentos/{ids[0]}", headers=c.h).json()
    assert d["status"] == "aguardando_confirmacao" and d["entrega"] is None
    assert d["opcoes_disponiveis"]["sintese"] is True


def test_arquivo_so_em_pronto(client, conta, admin):
    c = conta()
    doc = doc_triado(admin, c)
    _erro(client.get(f"/documentos/{doc}/arquivo", headers=c.h), 409, "estado_invalido")
    _erro(client.get(f"/documentos/{doc}/arquivo?formato=txt", headers=c.h), 422, "validacao")


# ---------------------------------------------------------------- 4.2
def test_orcamento_e_confirmacao(client, conta, admin):
    c = conta()
    doc = doc_triado(admin, c, paginas=294, palavras=91037)
    op = {"modo": "sintese", "fracao": 0.2}
    o = client.post(f"/documentos/{doc}/orcamento", json={"opcoes": op}, headers=c.h).json()
    assert o["total"] == 89 and o["saldo"] == 150 and o["saldo_depois"] == 61 and o["suficiente"] is True
    assert c.saldo(admin) == 150  # orçamento não reserva
    _erro(client.post(f"/documentos/{doc}/confirmar", json={"opcoes": op, "creditos_esperados": 88}, headers=c.h),
          409, "orcamento_mudou")
    r = client.post(f"/documentos/{doc}/confirmar", json={"opcoes": op, "creditos_esperados": 89}, headers=c.h)
    assert r.status_code == 200, r.text
    assert r.json() == {"documento_id": str(doc), "status": "na_fila", "creditos": 89, "saldo": 61}
    assert admin.execute("select tipo::text from public.jobs where documento_id = %s and status = 'na_fila'",
                         (doc,)).fetchone()["tipo"] == "processar"
    m = admin.execute("select modo::text, fracao_pedida, creditos, paginas_antes from public.metricas_entrega "
                      "where documento_id = %s", (doc,)).fetchone()
    assert m["modo"] == "sintese" and float(m["fracao_pedida"]) == 0.2 and m["creditos"] == 89
    # segunda confirmação → estado_invalido
    _erro(client.post(f"/documentos/{doc}/confirmar", json={"opcoes": op, "creditos_esperados": 89}, headers=c.h),
          409, "estado_invalido")
    assert invariantes(admin) == []


def test_sintese_so_em_relatorio(client, conta, admin):
    c = conta()
    doc = doc_triado(admin, c, tipo="ficha")
    _erro(client.post(f"/documentos/{doc}/orcamento", json={"opcoes": {"modo": "sintese", "fracao": 0.5}},
                      headers=c.h), 422, "validacao")


def test_saldo_insuficiente_nao_grava(client, conta, admin):
    c = conta()
    doc = doc_triado(admin, c, paginas=294, palavras=91037)
    op = {"modo": "sintese", "fracao": 0.5}  # 126
    outro = doc_triado(admin, c, paginas=294)  # 34 → saldo 116 < 126
    assert client.post(f"/documentos/{outro}/confirmar", json={"creditos_esperados": 34}, headers=c.h).status_code == 200
    _erro(client.post(f"/documentos/{doc}/confirmar", json={"opcoes": op, "creditos_esperados": 126}, headers=c.h),
          402, "saldo_insuficiente")
    d = admin.execute("select status::text, opcoes, orcamento from public.documentos where id = %s", (doc,)).fetchone()
    assert d == {"status": "aguardando_confirmacao", "opcoes": None, "orcamento": None}
    assert c.saldo(admin) == 116
    assert admin.execute("select count(*) from public.jobs where documento_id = %s", (doc,)).fetchone()["count"] == 0


def test_template_novo_sobretaxa(client, conta, admin):
    c = conta()
    doc = doc_triado(admin, c, paginas=16, template_novo=True)
    assert client.post(f"/documentos/{doc}/orcamento", json={}, headers=c.h).json()["total"] == 18


def test_confirmacoes_concorrentes_reservam_uma_vez(client, conta, admin):
    c = conta()
    doc = doc_triado(admin, c, paginas=10)
    res = []

    def vai():
        res.append(client.post(f"/documentos/{doc}/confirmar", json={"creditos_esperados": 4}, headers=c.h))

    ts = [threading.Thread(target=vai) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(r.status_code for r in res) == [200, 409]
    assert c.saldo(admin) == 146
    assert admin.execute("select count(*) from public.creditos_ledger where documento_id = %s and tipo = 'reserva'",
                         (doc,)).fetchone()["count"] == 1
    assert invariantes(admin) == []


def test_extrato_paginado(client, conta, admin):
    c = conta()
    for _ in range(2):
        d = doc_triado(admin, c, paginas=5)
        client.post(f"/documentos/{d}/confirmar", json={"creditos_esperados": 2}, headers=c.h)
    p1 = client.get("/creditos/extrato?limite=2", headers=c.h).json()
    assert p1["saldo"] == 146 and [i["tipo"] for i in p1["itens"]] == ["reserva", "reserva"]
    assert p1["itens"][0]["saldo_apos"] == 146 and p1["proximo_cursor"]
    p2 = client.get(f"/creditos/extrato?cursor={p1['proximo_cursor']}", headers=c.h).json()
    assert [i["tipo"] for i in p2["itens"]] == ["concessao"] and p2["proximo_cursor"] is None


@pytest.mark.parametrize("corpo", [{"opcoes": {"modo": "sintese"}}, {"opcoes": {"fracao": 0.2}},
                                   {"opcoes": {"modo": "x"}}, {"extra": 1}])
def test_opcoes_invalidas_422(client, conta, admin, corpo):
    c = conta()
    doc = doc_triado(admin, c)
    _erro(client.post(f"/documentos/{doc}/orcamento", json=corpo, headers=c.h), 422, "validacao")
