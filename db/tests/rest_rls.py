"""Story 3.3 AC 2/3 — RLS pela API real (PostgREST, RPC, Storage, Auth), com dois usuários A e B.

Complementa tests/rls.sql (pgTAP): aqui as requisições passam pelo mesmo caminho do front.
  - usuários criados pela API de admin do Auth local, login por senha (JWT de verdade);
  - A não lê documento, entrega, extrato nem arquivo de B; não altera o próprio saldo;
  - private.* não é exposto (RPC 404, Accept-Profile: private recusado);
  - Storage: A não envia para a pasta de B; envia a parte do próprio documento em upload;
  - remover o usuário no Auth deixa a linha pseudônima (perfis_usuario, ledger) — sem FK.

Uso (de db/, com `npx supabase start` no ar):  py -3 tests/rest_rls.py
Chaves lidas de `npx supabase status -o env` em tempo de execução; nada é gravado em disco.
Deixa dados de teste no banco local (o ledger é append-only); `npx supabase db reset` limpa.
Só stdlib.
"""
from __future__ import annotations

import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

CONTAINER = "supabase_db_docredux"


def status() -> dict[str, str]:
    r = subprocess.run("npx --yes supabase@3.2.0 status -o env", shell=True, capture_output=True,
                       text=True, encoding="utf-8")
    env = {}
    for linha in r.stdout.splitlines():
        if "=" in linha:
            k, v = linha.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    if "API_URL" not in env:
        sys.exit(f"supabase status falhou: {r.stderr}")
    return env


def psql(sql: str) -> str:
    r = subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d", "postgres",
                        "-v", "ON_ERROR_STOP=1", "-qtAX"], input=sql, capture_output=True, text=True,
                       encoding="utf-8")
    if r.returncode:
        sys.exit(f"psql falhou: {r.stderr}")
    return r.stdout.strip()


class Api:
    def __init__(self, url: str, apikey: str, token: str | None = None):
        self.url, self.apikey, self.token = url, apikey, token or apikey

    def req(self, metodo: str, caminho: str, corpo: object = None, cabecalhos: dict[str, str] | None = None,
            bruto: bytes | None = None) -> tuple[int, object]:
        h = {"apikey": self.apikey, "Authorization": f"Bearer {self.token}"}
        dados = None
        if bruto is not None:
            dados = bruto
            h["Content-Type"] = "application/octet-stream"
        elif corpo is not None:
            dados = json.dumps(corpo).encode()
            h["Content-Type"] = "application/json"
        h.update(cabecalhos or {})
        r = urllib.request.Request(self.url + caminho, data=dados, method=metodo, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                texto = resp.read().decode() or "null"
                codigo = resp.status
        except urllib.error.HTTPError as e:
            texto, codigo = e.read().decode() or "null", e.code
        try:
            return codigo, json.loads(texto)
        except json.JSONDecodeError:
            return codigo, texto


resultados: list[bool] = []


def conferir(nome: str, ok: bool, detalhe: object = "") -> None:
    resultados.append(ok)
    print(f"{'ok' if ok else 'FALHOU'} - {nome}" + ("" if ok else f": {detalhe}"))


def negado(codigo: int) -> bool:
    return 400 <= codigo < 500


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    env = status()
    url, anon, service = env["API_URL"], env["ANON_KEY"], env["SERVICE_ROLE_KEY"]
    admin = Api(url, service)
    sufixo = uuid.uuid4().hex[:8]

    # usuários A e B pelo Auth (o gatilho cria perfil + 150 créditos)
    ids, tokens = {}, {}
    for nome in ("a", "b"):
        email, senha = f"rest-{nome}-{sufixo}@teste.local", secrets.token_urlsafe(16)
        c, u = admin.req("POST", "/auth/v1/admin/users", {"email": email, "password": senha, "email_confirm": True})
        if c != 200:
            sys.exit(f"criar usuário: {c} {u}")
        ids[nome] = u["id"]  # type: ignore[index]
        c, t = Api(url, anon).req("POST", "/auth/v1/token?grant_type=password", {"email": email, "password": senha})
        if c != 200:
            sys.exit(f"login: {c} {t}")
        tokens[nome] = t["access_token"]  # type: ignore[index]

    # documentos: pronto com entrega e aguardando_upload, para cada um
    docs = {k: str(uuid.uuid4()) for k in ("a_pronto", "a_up", "b_pronto", "b_up")}
    sql = []
    for k, d in docs.items():
        dono, st = ids[k[0]], ("pronto" if k.endswith("pronto") else "aguardando_upload")
        sql.append("insert into public.documentos (id, usuario_id, idempotency_key, nome_arquivo, tamanho_bytes, status) "
                   f"values ('{d}', '{dono}', '{d}', 'teste.pdf', 1000, '{st}');")
        if st == "pronto":
            sql.append("insert into public.entregas (documento_id, versao, caminho_pdf, relatorio, cobertura, "
                       f"paginas_depois, gate) values ('{d}', 1, '{dono}/{d}/v1/saida.pdf', '{{}}', 0.999, 3, 'ok');"
                       f"update public.documentos set entrega_atual = 1 where id = '{d}';")
    psql("\n".join(sql))
    parte_b = f"originais/{ids['b']}/{docs['b_up']}/original.part00"
    c, r = admin.req("POST", f"/storage/v1/object/{parte_b}", bruto=b"%PDF-1.4 teste B")
    if c != 200:
        sys.exit(f"upload de B pelo service_role: {c} {r}")

    a = Api(url, anon, tokens["a"])

    # leitura
    c, r = a.req("GET", "/rest/v1/documentos?select=id")
    conferir("A lê só os próprios documentos", c == 200 and sorted(x["id"] for x in r) == sorted([docs["a_pronto"], docs["a_up"]]), (c, r))  # type: ignore[union-attr]
    c, r = a.req("GET", f"/rest/v1/documentos?id=eq.{docs['b_pronto']}")
    conferir("A não lê documento de B", c == 200 and r == [], (c, r))
    c, r = a.req("GET", f"/rest/v1/entregas?documento_id=eq.{docs['b_pronto']}")
    conferir("A não lê entrega de B", c == 200 and r == [], (c, r))
    c, r = a.req("GET", f"/rest/v1/entregas?documento_id=eq.{docs['a_pronto']}&select=versao")
    conferir("A lê a própria entrega", c == 200 and r == [{"versao": 1}], (c, r))
    c, r = a.req("GET", f"/rest/v1/creditos_ledger?usuario_id=eq.{ids['b']}")
    conferir("A não lê extrato de B", c == 200 and r == [], (c, r))
    c, r = a.req("GET", "/rest/v1/perfis_usuario?select=id,saldo")
    conferir("A lê só o próprio perfil, saldo 150", c == 200 and r == [{"id": ids["a"], "saldo": 150}], (c, r))

    # escrita
    c, r = a.req("PATCH", f"/rest/v1/perfis_usuario?id=eq.{ids['a']}", {"saldo": 9999})
    conferir("A não altera o próprio saldo (PATCH)", negado(c), (c, r))
    c, r = a.req("POST", "/rest/v1/creditos_ledger",
                 {"usuario_id": ids["a"], "tipo": "recarga", "valor": 1000, "saldo_apos": 1150})
    conferir("A não insere no ledger", negado(c), (c, r))
    c, r = a.req("DELETE", f"/rest/v1/creditos_ledger?usuario_id=eq.{ids['a']}")
    conferir("A não apaga o ledger", negado(c), (c, r))
    c, r = a.req("PATCH", f"/rest/v1/documentos?id=eq.{docs['a_up']}", {"status": "pronto"})
    conferir("A não muda status de documento", negado(c), (c, r))
    for t in ("jobs", "avaliacoes_juiz", "config", "v_fila_backoffice"):
        c, r = a.req("GET", f"/rest/v1/{t}")
        conferir(f"A não lê {t}", negado(c), (c, r))

    # private não é exposto
    c, r = a.req("POST", "/rest/v1/rpc/credito_conceder",
                 {"p_usuario": ids["a"], "p_valor": 1000, "p_tipo": "recarga"})
    conferir("RPC credito_conceder não existe na API", negado(c), (c, r))
    c, r = a.req("POST", "/rest/v1/rpc/credito_conceder",
                 {"p_usuario": ids["a"], "p_valor": 1000, "p_tipo": "recarga"}, {"Content-Profile": "private"})
    conferir("Content-Profile: private recusado", negado(c), (c, r))
    c, r = a.req("GET", "/rest/v1/creditos_ledger", cabecalhos={"Accept-Profile": "private"})
    conferir("Accept-Profile: private recusado", negado(c), (c, r))

    # Storage
    c, r = a.req("POST", f"/storage/v1/object/originais/{ids['b']}/{docs['b_up']}/original.part01", bruto=b"x")
    conferir("A não envia arquivo para a pasta de B", negado(c), (c, r))
    c, r = a.req("GET", f"/storage/v1/object/{parte_b}")
    conferir("A não baixa arquivo de B", negado(c), (c, r))
    c, r = a.req("POST", f"/storage/v1/object/originais/{ids['a']}/{docs['a_pronto']}/original.part00", bruto=b"x")
    conferir("A não envia para documento que não espera upload", negado(c), (c, r))
    c, r = a.req("POST", f"/storage/v1/object/originais/{ids['a']}/{docs['a_up']}/original.part00", bruto=b"%PDF-1.4 A")
    conferir("A envia a parte do próprio documento em upload", c == 200, (c, r))

    # anon
    c, r = Api(url, anon).req("GET", "/rest/v1/documentos")
    conferir("anon não lê documentos", negado(c), (c, r))

    # saldo intacto
    conferir("saldo de A intacto (150)",
             psql(f"select saldo from public.perfis_usuario where id = '{ids['a']}'") == "150")

    # remover A no Auth: a linha pseudônima fica (sem FK para auth.users, §6/§14)
    c, r = admin.req("DELETE", f"/auth/v1/admin/users/{ids['a']}")
    conferir("Auth remove o usuário A", c == 200, (c, r))
    linhas = psql(f"select count(*) from public.perfis_usuario where id = '{ids['a']}';"
                  f"select count(*) from public.creditos_ledger where usuario_id = '{ids['a']}';").split()
    conferir("perfil e ledger de A ficam com o id pseudônimo", linhas == ["1", "1"], linhas)

    ok = all(resultados)
    print(f"{sum(resultados)}/{len(resultados)} - {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
