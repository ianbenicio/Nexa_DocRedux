"""Story 3.2 AC 2 — reservas concorrentes não deixam o saldo negativo nem reservam duas vezes.

Duas sessões psql de verdade (docker exec no container do banco local), como docredux_backend:
  1. documentos diferentes, saldo 150, cada um custa 100: A reserva e segura a transação 3 s;
     B tenta reservar 1 s depois, espera o lock do perfil e falha com saldo_insuficiente.
  2. o mesmo documento: B espera o lock do documento e falha com reserva_aberta.
Depois: saldo 50 e private.invariantes_credito() vazio para os usuários do teste.

Uso (de db/, com `npx supabase start` no ar):  py -3 tests/concorrencia.py
Deixa usuários de teste no banco local (o ledger é append-only); `npx supabase db reset` limpa.
Só stdlib.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid

CONTAINER = "supabase_db_docredux"
# postgres do Supabase não pode `set role docredux_backend`; supabase_admin (só no container local) pode.
ADMIN = "supabase_admin"


def psql(sql: str, usuario: str = "postgres") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "psql", "-U", usuario, "-d", "postgres",
         "-v", "ON_ERROR_STOP=1", "-qtAX"],
        input=sql, capture_output=True, text=True, encoding="utf-8",
    )


def preparar(docs_total: list[int]) -> tuple[str, list[str]]:
    usuario = str(uuid.uuid4())
    docs = [str(uuid.uuid4()) for _ in docs_total]
    sql = [f"insert into auth.users (id, email, aud, role) values ('{usuario}', "
           f"'concorrencia-{usuario[:8]}@teste.local', 'authenticated', 'authenticated');"]
    for d, total in zip(docs, docs_total):
        sql.append(
            "insert into public.documentos (id, usuario_id, idempotency_key, nome_arquivo, tamanho_bytes, "
            f"status, orcamento) values ('{d}', '{usuario}', '{d}', 'teste.pdf', 1000, 'na_fila', "
            f"jsonb_build_object('total', {total}));")
    r = psql("\n".join(sql))
    if r.returncode:
        sys.exit(f"falha ao preparar: {r.stderr}")
    return usuario, docs


def corrida(doc_a: str, doc_b: str) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str], float]:
    res: dict[str, object] = {}

    def sessao_a() -> None:
        res["a"] = psql("set role docredux_backend; begin;\n"
                        f"select private.credito_reservar('{doc_a}');\n"
                        "select pg_sleep(3);\ncommit;", ADMIN)

    t = threading.Thread(target=sessao_a)
    t.start()
    time.sleep(1)
    inicio = time.monotonic()
    res["b"] = psql(f"set role docredux_backend; select private.credito_reservar('{doc_b}');", ADMIN)
    espera = time.monotonic() - inicio
    t.join()
    return res["a"], res["b"], espera  # type: ignore[return-value]


def conferir(nome: str, ok: bool, detalhe: str = "") -> bool:
    print(f"{'ok' if ok else 'FALHOU'} - {nome}{(': ' + detalhe) if detalhe and not ok else ''}")
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tudo = True

    # 1. documentos diferentes: o lock do perfil serializa; o segundo vê saldo 50 < 100.
    u1, (d1, d2) = preparar([100, 100])
    a, b, espera = corrida(d1, d2)
    tudo &= conferir("1. A reserva 100", a.returncode == 0, a.stderr)
    tudo &= conferir("1. B falha com saldo_insuficiente", b.returncode != 0 and "saldo_insuficiente" in b.stderr, b.stderr)
    tudo &= conferir("1. B esperou o lock de A (>= 1.5 s)", espera >= 1.5, f"{espera:.2f} s")

    # 2. o mesmo documento: o lock do documento serializa; o segundo vê a reserva aberta.
    u2, (d3,) = preparar([100])
    a, b, espera = corrida(d3, d3)
    tudo &= conferir("2. A reserva o documento", a.returncode == 0, a.stderr)
    tudo &= conferir("2. B falha com reserva_aberta", b.returncode != 0 and "reserva_aberta" in b.stderr, b.stderr)
    tudo &= conferir("2. B esperou o lock de A (>= 1.5 s)", espera >= 1.5, f"{espera:.2f} s")

    r = psql(f"select id, saldo from public.perfis_usuario where id in ('{u1}', '{u2}') order by saldo;\n"
             f"select count(*) from private.invariantes_credito() where usuario_id in ('{u1}', '{u2}');\n"
             "select count(*) from public.creditos_ledger where tipo = 'reserva' "
             f"and usuario_id in ('{u1}', '{u2}');")
    linhas = r.stdout.split()
    saldos = sorted(int(x.split("|")[1]) for x in linhas[:2])
    tudo &= conferir("saldo final 50 nos dois usuários", saldos == [50, 50], str(saldos))
    tudo &= conferir("invariantes_credito vazio", linhas[2] == "0", linhas[2])
    tudo &= conferir("exatamente duas reservas gravadas", linhas[3] == "2", linhas[3])

    print("PASS" if tudo else "FAIL")
    return 0 if tudo else 1


if __name__ == "__main__":
    sys.exit(main())
