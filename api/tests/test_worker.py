"""Story 4.4 (fila, worker, triagem, processar) e a parte do worker da 4.7 (avisos).

A triagem usa o motor de verdade (só PyMuPDF). O `processar` usa um motor falso aqui (o LibreOffice roda
no container; o E2E com o motor real está em tests/e2e.py, story 4.6).
"""
from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path

import pymupdf
import pytest

from tests.conftest import invariantes, requer_db

pytestmark = requer_db
EXEMPLOS = Path(os.environ.get("DOCREDUX_FIXTURES", "H:/Nexa/03_Comercial_Clientes/Projetos/DocRedux/exemplos"))


def exemplo(n: str) -> Path:
    achados = sorted(EXEMPLOS.glob(f"{n}-*/original/*.pdf"))
    if not achados:
        pytest.skip(f"fixture do ex. {n} ausente (DOCREDUX_FIXTURES)")
    return achados[0]


class EmailFalso:
    def __init__(self, falhar: int = 0):
        self.enviados: list[dict] = []
        self.falhar = falhar

    def enviar(self, *, para, assunto, texto, chave_idempotencia):
        if self.falhar:
            self.falhar -= 1
            raise RuntimeError("Resend HTTP 500")
        self.enviados.append({"para": para, "assunto": assunto, "texto": texto, "chave": chave_idempotencia})
        return f"re_{len(self.enviados)}"


@pytest.fixture
def fila_limpa(admin):
    """Os testes do worker pegam qualquer job da fila: cancela o que sobrou de outros testes."""
    admin.execute("""update public.jobs set status = 'cancelado', concluido_em = now(), erro = 'limpeza de teste'
                      where status in ('na_fila', 'processando')""")
    yield


@pytest.fixture
def email():
    return EmailFalso()


@pytest.fixture
def worker(tmp_path, email, fila_limpa):
    from app.db import conectar
    from worker.laco import Worker
    criados = []

    def _novo(nome="w-teste", tipos=("triagem", "processar"), **kw):
        w = Worker(f"{nome}-{uuid.uuid4().hex[:6]}", list(tipos), cache=tmp_path / "cache",
                   timeouts={"triagem": 120, "processar": 120, "juiz": 60}, email=kw.pop("email", email),
                   conn=conectar(autocommit=True))
        criados.append(w)
        return w
    yield _novo
    for w in criados:
        w.conn.close()


def enviar(client, conta, pdf: Path | bytes, *, sha=True) -> str:
    """Fluxo do navegador: POST /documentos, partes no Storage, upload-concluido → triagem."""
    import httpx
    dados = pdf.read_bytes() if isinstance(pdf, Path) else pdf
    corpo = {"nome_arquivo": "doc.pdf", "tamanho_bytes": len(dados)}
    if sha:
        corpo["sha256"] = hashlib.sha256(dados).hexdigest()
    j = client.post("/documentos", json=corpo, headers={**conta.h, "Idempotency-Key": str(uuid.uuid4())}).json()
    pos = 0
    for p in j["caminhos"]:
        r = httpx.post(f"{os.environ['SUPABASE_URL']}/storage/v1/object/originais/{p['caminho']}",
                       content=dados[pos:pos + p["tamanho_bytes"]], timeout=120,
                       headers={"apikey": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
                                "Authorization": conta.h["Authorization"], "Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        pos += p["tamanho_bytes"]
    assert client.post(f"/documentos/{j['documento_id']}/upload-concluido", headers=conta.h).json()["status"] == "triagem"
    return j["documento_id"]


def pdf_desconhecido() -> bytes:
    d = pymupdf.open()
    for i in range(3):
        d.new_page().insert_text((72, 72), f"Documento de um modelo novo, página {i + 1}. " * 3, fontname="cour")
    return d.tobytes()


def doc(admin, doc_id) -> dict:
    return admin.execute("select *, status::text as status, motivo_recusa::text as motivo from public.documentos "
                         "where id = %s", (doc_id,)).fetchone()


# ---------------------------------------------------------------- motor falso do processar
def motor_falso(monkeypatch, *, gate="ok", cobertura=1.0, ao_rodar=None, falhar=None):
    from worker import motor
    real = motor.executar
    chamadas = []

    def executar(args, *, timeout_s, pulso=None, cwd=None):
        if args[0] != "processar":
            return real(args, timeout_s=timeout_s, pulso=pulso, cwd=cwd)
        chamadas.append(args)
        if ao_rodar:
            ao_rodar()
        if falhar:
            raise falhar
        saida = Path(args[2])
        saida.mkdir(parents=True, exist_ok=True)
        d = pymupdf.open()
        d.new_page().insert_text((72, 72), "saída")
        d.save(saida / "saida.pdf")
        docx = None
        if "--docx" in args:
            docx = saida / "saida.docx"
            docx.write_bytes(b"PK docx falso")
        return {"pdf": str(saida / "saida.pdf"), "docx": str(docx) if docx else None,
                "relatorio": {"versao_motor": "teste", "perfil_id": "mathema-saeb", "paginas_antes": 16,
                              "paginas_depois": 9, "palavras_original": 1334, "palavras_saida": 1334,
                              "cobertura": cobertura, "fracao_real": 0.56, "condensado": False, "gate": gate,
                              "motivo_gate": None if gate == "ok" else "cobertura baixa", "estrutura": {},
                              "modificacoes": [{"tipo": "removido", "descricao": "capa", "paginas_original": [1],
                                                "pagina_saida": None}],
                              "regras_disparadas": [], "tempos_s": {"total": 1.0}, "avisos": []}}
    monkeypatch.setattr(motor, "executar", executar)
    return chamadas


def triar_e_confirmar(client, conta, admin, w, pdf, opcoes=None) -> str:
    d = enviar(client, conta, pdf)
    assert w.volta()
    o = client.post(f"/documentos/{d}/orcamento", json={"opcoes": opcoes or {}}, headers=conta.h).json()
    r = client.post(f"/documentos/{d}/confirmar", json={"opcoes": opcoes or {}, "creditos_esperados": o["total"]},
                    headers=conta.h)
    assert r.status_code == 200, r.text
    return d


# ---------------------------------------------------------------- triagem
def test_triagem_real_ex03(client, conta, admin, worker):
    c, w = conta(), worker()
    d = enviar(client, c, exemplo("03"))
    assert w.volta()
    x = doc(admin, d)
    assert (x["status"], x["paginas"], x["palavras"], x["tipo"], x["perfil_id"]) == \
        ("aguardando_confirmacao", 16, 1334, "ficha", "mathema-saeb")
    assert x["sha256"] == hashlib.sha256(exemplo("03").read_bytes()).hexdigest()
    j = admin.execute("select status::text, tentativas from public.jobs where documento_id = %s", (d,)).fetchone()
    assert j == {"status": "concluido", "tentativas": 1}


def test_template_desconhecido_recusado_com_aviso(client, conta, admin, worker, email):
    c, w = conta(), worker()
    d = enviar(client, c, pdf_desconhecido())
    assert w.volta()
    x = doc(admin, d)
    assert (x["status"], x["motivo"], x["tipo"]) == ("recusado", "template_novo", "desconhecido")
    assert c.saldo(admin) == 150
    assert len(email.enviados) == 1 and "2 meses" in email.enviados[0]["texto"]
    assert email.enviados[0]["chave"] == f"docredux/{d}/em_revisao"
    assert str(d) in email.enviados[0]["texto"] and "doc.pdf" not in email.enviados[0]["texto"]


def test_sha_divergente_falha_definitiva(client, conta, admin, worker):
    c, w = conta(), worker()
    d = enviar(client, c, pdf_desconhecido())
    admin.execute("update public.documentos set sha256 = %s where id = %s", ("0" * 64, d))
    assert w.volta()
    assert doc(admin, d)["status"] == "upload_falhou"
    j = admin.execute("select status::text, tentativas, erro from public.jobs where documento_id = %s", (d,)).fetchone()
    assert j["status"] == "falhou" and j["tentativas"] == 1 and "SHA-256" in j["erro"]


def test_remonta_partes_com_mesmo_sha(client, conta, admin, worker, monkeypatch):
    """AC 6 com parte de 1 MiB (o ex. 05 real, 62 MB em 2 partes, está no E2E)."""
    admin.execute("update public.config set valor = '1' where chave = 'parte_mb'")
    try:
        c, w = conta(), worker()
        dados = exemplo("03").read_bytes()
        d = enviar(client, c, dados)
        assert doc(admin, d)["partes"] == 2
        assert w.volta()
        x = doc(admin, d)
        assert x["status"] == "aguardando_confirmacao" and x["sha256"] == hashlib.sha256(dados).hexdigest()
    finally:
        admin.execute("update public.config set valor = '42' where chave = 'parte_mb'")


# ---------------------------------------------------------------- fila
def test_dois_workers_nao_pegam_o_mesmo_job(conta, admin, worker, monkeypatch):
    from worker import jobs
    c = conta()
    docs = [admin.execute(
        """insert into public.documentos (usuario_id, idempotency_key, nome_arquivo, tamanho_bytes, status)
           values (%s, %s, 'x.pdf', 10, 'triagem') returning id""", (c.id, str(uuid.uuid4()))).fetchone()["id"]
        for _ in range(20)]
    for d in docs:
        admin.execute("insert into public.jobs (documento_id, tipo) values (%s, 'triagem')", (d,))
    vistos, trava = [], threading.Lock()

    def falso(ctx):
        with trava:
            vistos.append((ctx.worker_id, ctx.job["id"]))
        threading.Event().wait(0.05)
        with ctx.conn.transaction():
            ctx.conn.execute("update public.jobs set status = 'concluido', concluido_em = now() where id = %s",
                             (ctx.job["id"],))
    monkeypatch.setitem(jobs.EXECUTORES, "triagem", falso)
    ws = [worker("a"), worker("b")]

    def rodar(w):
        while w.volta():
            pass
    ts = [threading.Thread(target=rodar, args=(w,)) for w in ws]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [j for _, j in vistos]
    assert len(ids) == 20 and len(set(ids)) == 20
    assert len({w for w, _ in vistos}) == 2


def test_coletor_devolve_e_falha_final_estorna(client, conta, admin, worker, monkeypatch):
    """AC 3/4: job com heartbeat parado volta à fila; esgotadas as tentativas → falhou + estorno de 100%."""
    from worker import falhas
    c, w = conta(), worker()
    motor_falso(monkeypatch)
    d = triar_e_confirmar(client, c, admin, w, exemplo("03"))
    assert c.saldo(admin) == 144  # ex. 03: 16 págs. → 6
    for tentativa in (1, 2, 3):
        # simula worker que pegou o job e morreu
        admin.execute("""update public.jobs set status = 'processando', worker_id = 'morto', tentativas = %s,
                                heartbeat_em = now() - interval '3 minutes', iniciado_em = now()
                          where documento_id = %s and tipo = 'processar' and status in ('na_fila', 'processando')""",
                      (tentativa, d))
        admin.execute("update public.documentos set status = 'processando' where id = %s", (d,))
        assert falhas.coletar(w.conn) >= 1
        j = admin.execute("""select status::text, disponivel_em > now() as adiado from public.jobs
                              where documento_id = %s and tipo = 'processar' order by id desc limit 1""",
                          (d,)).fetchone()
        if tentativa < 3:
            assert j == {"status": "na_fila", "adiado": True}
            assert doc(admin, d)["status"] == "na_fila"
    assert j["status"] == "falhou"
    assert doc(admin, d)["status"] == "falhou" and c.saldo(admin) == 150
    assert invariantes(admin) == []


def test_worker_morto_retoma_e_termina(client, conta, admin, worker, monkeypatch, email):
    """AC 3: matar no meio → coletor devolve → outro worker termina; tentativas = 2."""
    from worker import falhas
    c = conta()
    motor_falso(monkeypatch)
    w1 = worker("w1")
    d = triar_e_confirmar(client, c, admin, w1, exemplo("03"))
    from worker.laco import reivindicar
    job = reivindicar(w1.conn, w1.worker_id, ["processar"])  # w1 pega e "morre"
    assert job and doc(admin, d)["status"] == "processando"
    admin.execute("update public.jobs set heartbeat_em = now() - interval '3 minutes' where id = %s", (job["id"],))
    falhas.coletar(w1.conn)
    admin.execute("update public.jobs set disponivel_em = now() where id = %s", (job["id"],))
    w2 = worker("w2")
    assert w2.volta()
    j = admin.execute("select status::text, tentativas, worker_id from public.jobs where id = %s", (job["id"],)).fetchone()
    assert j["status"] == "concluido" and j["tentativas"] == 2 and j["worker_id"] == w2.worker_id
    assert doc(admin, d)["status"] == "pronto"


def test_heartbeat_perdido_mata_o_motor(client, conta, admin, worker, monkeypatch):
    from worker import motor
    c, w = conta(), worker()
    perdido = {"n": 0}

    def pulso():
        perdido["n"] += 1
        return False
    monkeypatch.setattr(motor, "PULSO_S", 0)
    with pytest.raises(motor.JobPerdido):
        motor.executar(["triar", str(exemplo("05"))], timeout_s=60, pulso=pulso, cwd=Path(w.cache).parent)
    assert perdido["n"] == 1
    with pytest.raises(motor.MotorTimeout):
        motor.executar(["triar", str(exemplo("05"))], timeout_s=1, cwd=Path(w.cache).parent)


# ---------------------------------------------------------------- processar
def test_processar_pronto_captura_juiz_aviso_metricas(client, conta, admin, worker, monkeypatch, email):
    c, w = conta(), worker()
    motor_falso(monkeypatch)
    d = triar_e_confirmar(client, c, admin, w, exemplo("03"), {"docx": True})
    assert w.volta()
    x = doc(admin, d)
    assert x["status"] == "pronto" and x["entrega_atual"] == 1 and x["entregue_em"]
    tipos = [r["tipo"] for r in admin.execute(
        "select tipo::text from public.creditos_ledger where documento_id = %s order by id", (d,)).fetchall()]
    assert tipos == ["reserva", "captura"]
    assert admin.execute("select status::text from public.jobs where documento_id = %s and tipo = 'juiz'",
                         (d,)).fetchone()["status"] == "na_fila"
    assert [e["chave"] for e in email.enviados] == [f"docredux/{d}/pronto"]
    m = admin.execute("select * from public.metricas_entrega where documento_id = %s", (d,)).fetchone()
    assert m["versao_motor"] == "teste" and m["paginas_depois"] == 9 and m["gate_primeira_passagem"] == "ok"
    assert m["versoes"] == 1 and m["tentativas"] == 1 and m["t_total_s"] is not None and m["docx"] is True
    e = admin.execute("select caminho_pdf, caminho_docx, tamanho_bytes from public.entregas where documento_id = %s",
                      (d,)).fetchone()
    assert e["caminho_pdf"] == f"{c.id}/{d}/v1/saida.pdf" and e["caminho_docx"].endswith("v1/saida.docx")
    assert e["tamanho_bytes"] > 0
    det = client.get(f"/documentos/{d}", headers=c.h).json()
    assert det["entrega"]["versao"] == 1
    url = client.get(f"/documentos/{d}/arquivo", headers=c.h).json()["url"]
    import httpx
    assert httpx.get(url).content.startswith(b"%PDF")
    assert client.get(f"/documentos/{d}/arquivo?formato=docx", headers=c.h).status_code == 200
    assert invariantes(admin) == []


def test_gate_bloqueado_em_revisao(client, conta, admin, worker, monkeypatch, email):
    c, w = conta(), worker()
    motor_falso(monkeypatch, gate="bloqueado", cobertura=0.98)
    d = triar_e_confirmar(client, c, admin, w, exemplo("03"))
    assert w.volta()
    assert doc(admin, d)["status"] == "em_revisao"
    assert admin.execute("select private.reserva_aberta(%s) as r", (d,)).fetchone()["r"] is not None  # sem captura
    det = client.get(f"/documentos/{d}", headers=c.h).json()
    assert det["entrega"] is None
    assert client.get(f"/documentos/{d}/arquivo", headers=c.h).status_code == 409
    assert [e["chave"] for e in email.enviados] == [f"docredux/{d}/em_revisao"]
    assert "revisão" in email.enviados[0]["texto"] and "2 meses" not in email.enviados[0]["texto"]


def test_excluir_durante_processar_nada_sobe_e_estorna(client, conta, admin, worker, monkeypatch, email):
    c, w = conta(), worker()
    d_box = {}

    def excluir_no_meio():
        r = client.delete(f"/documentos/{d_box['d']}", headers=c.h).json()
        assert r["exclusao_pendente"] is True
    motor_falso(monkeypatch, ao_rodar=excluir_no_meio)
    d = d_box["d"] = triar_e_confirmar(client, c, admin, w, exemplo("03"))
    assert w.volta()
    x = doc(admin, d)
    assert x["status"] == "excluido" and x["exclusao_pendente"] is False and x["entrega_atual"] is None
    assert c.saldo(admin) == 150
    from app import supabase
    assert supabase.listar(supabase.SAIDAS, f"{c.id}/{d}") == []
    assert supabase.listar(supabase.ORIGINAIS, f"{c.id}/{d}") == []
    assert email.enviados == []
    assert invariantes(admin) == []


def test_excluir_durante_triagem(client, conta, admin, worker, monkeypatch):
    from worker import motor
    c, w = conta(), worker()
    real = motor.executar

    def triar_e_excluir(args, **kw):
        out = real(args, **kw)
        client.delete(f"/documentos/{d}", headers=c.h)
        return out
    d = enviar(client, c, pdf_desconhecido())
    monkeypatch.setattr(motor, "executar", triar_e_excluir)
    assert w.volta()
    x = doc(admin, d)
    assert x["status"] == "excluido" and x["motivo"] is None and c.saldo(admin) == 150


def test_motor_falha_3_vezes_falhou_e_estorna(client, conta, admin, worker, monkeypatch):
    from worker import motor
    c, w = conta(), worker()
    motor_falso(monkeypatch, falhar=motor.MotorTimeout("motor passou de 900 s"))
    d = triar_e_confirmar(client, c, admin, w, exemplo("03"))
    for _ in range(3):
        admin.execute("update public.jobs set disponivel_em = now() where documento_id = %s and status = 'na_fila'", (d,))
        assert w.volta()
    j = admin.execute("select status::text, tentativas from public.jobs where documento_id = %s and tipo = 'processar'",
                      (d,)).fetchone()
    assert j == {"status": "falhou", "tentativas": 3}
    assert doc(admin, d)["status"] == "falhou" and c.saldo(admin) == 150
    assert admin.execute("select tentativas from public.metricas_entrega where documento_id = %s",
                         (d,)).fetchone()["tentativas"] == 3


def test_health_verde_com_worker_ocioso(client, worker):
    w = worker()
    assert w.volta() is False
    assert client.get("/health").json()["worker"] is True


def test_alvo_do_processo_importavel_no_spawn():
    """`python -m worker` usa spawn: o alvo precisa viver num módulo importável, não no __main__."""
    import pickle

    from worker.processo import processo
    assert processo.__module__ == "worker.processo"
    assert pickle.loads(pickle.dumps(processo)) is processo


def test_worker_id_unico_por_vida_do_processo():
    """Container reiniciado repete hostname e pid: o id do worker não pode repetir junto."""
    from worker.laco import Worker
    a, b = Worker(conn=object()), Worker(conn=object())
    assert a.worker_id != b.worker_id
