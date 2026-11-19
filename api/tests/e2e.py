"""E2E da unidade 4 (story 4.6): API no uvicorn + worker no container + motor real (LibreOffice) + Supabase local.

Fora da suíte padrão (o pytest só coleta test_*.py). Pré-requisitos:
    supabase start (db/) · python scripts/dev_setup.py · docker build -t docredux-engine engine/
    docker compose -f deploy/docker-compose.dev.yml up -d --build worker
    uvicorn app.main:app --port 8000          (em api/, com o .venv)
    python -m pytest tests/e2e.py -v -s       (em api/; DOCREDUX_FIXTURES aponta para os exemplos)

Variáveis: E2E_API (padrão http://127.0.0.1:8000), E2E_WORKER (container do worker, padrão docredux-dev-worker-1).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pymupdf
import pytest

from tests.conftest import invariantes, requer_db
from tests.test_worker import exemplo

pytestmark = requer_db
API = os.environ.get("E2E_API", "http://127.0.0.1:8000")
WORKER = os.environ.get("E2E_WORKER", "docredux-dev-worker-1")
BASELINE = {"03": 5, "05": 142}  # engine/tests/baseline.json


@pytest.fixture(scope="module")
def api():
    with httpx.Client(base_url=API, timeout=60) as c:
        try:
            saude = c.get("/health").json()
        except httpx.HTTPError:
            pytest.skip(f"API fora do ar em {API} (rode o uvicorn)")
        assert saude["db"] is True, saude
        yield c


@pytest.fixture(scope="module", autouse=True)
def fila_vazia(admin):
    admin.execute("""update public.jobs set status = 'cancelado', concluido_em = now(), erro = 'limpeza do e2e'
                      where status in ('na_fila', 'processando')""")
    yield
    assert invariantes(admin) == []


def docker(*args: str) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def estado(admin, doc_id) -> dict:
    return admin.execute("select status::text, entrega_atual from public.documentos where id = %s", (doc_id,)).fetchone()


def job(admin, doc_id, tipo="processar") -> dict | None:
    return admin.execute("""select id, status::text, tentativas, worker_id, erro, iniciado_em, concluido_em
                              from public.jobs where documento_id = %s and tipo = %s order by id desc limit 1""",
                         (doc_id, tipo)).fetchone()


def lancamentos(admin, doc_id) -> list[str]:
    return [r["tipo"] for r in admin.execute(
        "select tipo::text from public.creditos_ledger where documento_id = %s order by id", (doc_id,)).fetchall()]


def esperar(cond, timeout_s: float, *, passo=2.0, msg=""):
    fim = time.monotonic() + timeout_s
    while time.monotonic() < fim:
        v = cond()
        if v:
            return v
        time.sleep(passo)
    pytest.fail(f"tempo esgotado ({timeout_s:.0f} s): {msg}")


def enviar(api, conta, pdf: Path) -> str:
    """Navegador: POST /documentos → partes direto no Storage com o JWT do usuário → upload-concluido."""
    dados = pdf.read_bytes()
    r = api.post("/documentos", json={"nome_arquivo": pdf.name, "tamanho_bytes": len(dados),
                                      "sha256": hashlib.sha256(dados).hexdigest()},
                 headers={**conta.h, "Idempotency-Key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    j = r.json()
    pos = 0
    for p in j["caminhos"]:
        s = httpx.post(f"{os.environ['SUPABASE_URL']}/storage/v1/object/originais/{p['caminho']}",
                       content=dados[pos:pos + p["tamanho_bytes"]], timeout=300,
                       headers={"apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"], "Authorization": conta.h["Authorization"],
                                "Content-Type": "application/octet-stream"})
        assert s.status_code == 200, s.text
        pos += p["tamanho_bytes"]
    r = api.post(f"/documentos/{j['documento_id']}/upload-concluido", headers=conta.h)
    assert r.json()["status"] == "triagem", r.text
    return j["documento_id"]


def triar(api, conta, doc_id) -> dict:
    esperar(lambda: api.get(f"/documentos/{doc_id}", headers=conta.h).json()["status"] != "triagem", 300,
            msg="triagem")
    d = api.get(f"/documentos/{doc_id}", headers=conta.h).json()
    assert d["status"] == "aguardando_confirmacao", d
    return d


def confirmar(api, conta, doc_id, opcoes) -> int:
    o = api.post(f"/documentos/{doc_id}/orcamento", json={"opcoes": opcoes}, headers=conta.h).json()
    r = api.post(f"/documentos/{doc_id}/confirmar", json={"opcoes": opcoes, "creditos_esperados": o["total"]},
                 headers=conta.h)
    assert r.status_code == 200, r.text
    return o["total"]


def baixar_entrega(api, conta, doc_id, formato="pdf") -> bytes:
    r = api.get(f"/documentos/{doc_id}/arquivo", params={"formato": formato}, headers=conta.h)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    b = httpx.get(url, timeout=120)
    assert b.status_code == 200
    return b.content


# ---------------------------------------------------------------- AC 1 e 4
@pytest.mark.parametrize("n,opcoes", [("03", {"docx": True}), ("05", {})])
def test_fluxo_completo(api, admin, conta, n, opcoes):
    c = conta()
    pdf = exemplo(n)
    d = enviar(api, c, pdf)
    partes = admin.execute("select partes from public.documentos where id = %s", (d,)).fetchone()["partes"]
    assert partes == (2 if n == "05" else 1)
    t = triar(api, c, d)
    print(f"\nex{n}: {t['paginas']} págs, {t['palavras']} palavras, tipo {t['tipo']}")
    total = confirmar(api, c, d, opcoes)
    assert c.saldo(admin) == 150 - total
    esperar(lambda: estado(admin, d)["status"] in ("pronto", "em_revisao", "falhou"), 600, passo=3, msg=f"ex{n}")
    j = job(admin, d)
    duracao = (j["concluido_em"] - j["iniciado_em"]).total_seconds()
    print(f"ex{n}: processar em {duracao:.0f} s, job {j['status']} tentativas {j['tentativas']}")
    assert estado(admin, d)["status"] == "pronto", j
    det = api.get(f"/documentos/{d}", headers=c.h).json()
    assert det["entrega"]["paginas_depois"] == BASELINE[n]
    saida = pymupdf.open(stream=baixar_entrega(api, c, d), filetype="pdf")
    assert saida.page_count == BASELINE[n]
    if opcoes.get("docx"):
        assert baixar_entrega(api, c, d, "docx")[:2] == b"PK"
    if n == "05":
        assert duracao <= 180, f"ex05 levou {duracao:.0f} s (AC 4: ≤ 3 min)"
    assert c.saldo(admin) == 150 - total
    assert lancamentos(admin, d) == ["reserva", "captura"]


# ---------------------------------------------------------------- AC 2
def test_worker_cai_no_meio_e_job_e_retomado(api, admin, conta):
    c = conta()
    d = enviar(api, c, exemplo("03"))
    triar(api, c, d)
    confirmar(api, c, d, {})
    j1 = esperar(lambda: (x := job(admin, d)) and x["status"] == "processando" and x, 120, passo=0.5,
                 msg="worker pegar o job")
    docker("kill", WORKER)  # queda: SIGKILL no container, motor junto
    print(f"\nworker {j1['worker_id']} derrubado com o job {j1['id']} em processando")
    try:
        assert job(admin, d)["status"] == "processando"
    finally:
        docker("start", WORKER)
    # coletor (heartbeat > 2 min, roda a cada 60 s) devolve à fila com espera de 30 s; outro processo termina
    esperar(lambda: estado(admin, d)["status"] == "pronto", 600, passo=5, msg="retomada")
    j = job(admin, d)
    assert j["id"] == j1["id"] and j["status"] == "concluido" and j["tentativas"] == 2
    assert j["worker_id"] != j1["worker_id"]
    assert pymupdf.open(stream=baixar_entrega(api, c, d), filetype="pdf").page_count == BASELINE["03"]


# ---------------------------------------------------------------- AC 3
def test_falha_nas_3_tentativas_estorna(api, admin, conta):
    """Perfil privado quebrado: o motor sai com PerfilInvalido sem `motivo` → falha transitória, 3 tentativas."""
    c = conta()
    d = enviar(api, c, exemplo("03"))
    triar(api, c, d)
    perfil = f"e2e-quebrado-{uuid.uuid4().hex[:8]}"
    admin.execute("""insert into public.perfis_template (id, dono, tipo, json)
                     values (%s, %s, 'ficha', jsonb_build_object('nome', %s::text, 'tipo', 'ficha'))""", (perfil, c.id, perfil))
    admin.execute("update public.documentos set perfil_id = %s where id = %s", (perfil, d))
    antes = c.saldo(admin)
    total = confirmar(api, c, d, {})
    assert c.saldo(admin) == antes - total
    esperar(lambda: estado(admin, d)["status"] == "falhou", 600, passo=5, msg="3 tentativas")
    j = job(admin, d)
    assert j["status"] == "falhou" and j["tentativas"] == 3 and "PerfilInvalido" in (j["erro"] or "")
    assert c.saldo(admin) == antes
    assert lancamentos(admin, d) == ["reserva", "estorno"]
