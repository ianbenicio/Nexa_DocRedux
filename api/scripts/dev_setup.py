"""Ambiente local da API e do worker contra o Supabase local (db/, portas 553xx).

0. Corrige o índice do Storage local (ver CORRECAO_STORAGE_LOCAL).
1. Gera uma senha aleatória para `docredux_backend` e aplica com `alter role` (supabase_admin, só no
   container local; a senha de produção é da unidade 7).
2. Lê as chaves locais de `supabase status -o env` e grava `api/.env` (gitignored).
3. Grava `deploy/.env.dev` (gitignored): o mesmo, com o host visto de dentro do container do worker
   (`host.docker.internal`), para `deploy/docker-compose.dev.yml`.

Uso:  python scripts/dev_setup.py [--supabase <caminho do binário supabase>]
Nada aqui é segredo de produção, mas o .env nunca vai para o git.
"""
from __future__ import annotations

import argparse
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[1]
DB = API.parent / "db"
CONTAINER = "supabase_db_docredux"


def status_env(supabase: list[str]) -> dict[str, str]:
    r = subprocess.run([*supabase, "status", "-o", "env", "--workdir", str(DB)],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"supabase status falhou (o Supabase local está de pé?): código {r.returncode}")
    env = {}
    for linha in r.stdout.splitlines():
        if "=" in linha and not linha.startswith(" "):
            k, v = linha.split("=", 1)
            env[k.strip()] = v.strip().strip('"')
    return env


# Storage local (storage-api v1.67.20 do CLI 3.2.0) faz upsert com `ON CONFLICT (name, bucket_id)`, mas o schema
# `storage` do banco local já vem com os índices parciais do versionamento: todo upload dá 500 (42P10).
# Só no ambiente local, recria o índice único simples. Não vai para migration (o schema storage é do Supabase).
CORRECAO_STORAGE_LOCAL = "create unique index if not exists bucketid_objname on storage.objects (bucket_id, name);"


def definir_senha(senha: str) -> None:
    sql = f"alter role docredux_backend password '{senha}';\n{CORRECAO_STORAGE_LOCAL}"
    r = subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-U", "supabase_admin", "-d", "postgres",
                        "-v", "ON_ERROR_STOP=1", "-qtAX"], input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("alter role falhou: " + r.stderr.strip()[:300])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--supabase", help="binário do supabase CLI (padrão: supabase no PATH ou npx supabase@3.2.0)")
    p.add_argument("--web-origin", default="http://localhost:3000")
    a = p.parse_args()
    if a.supabase:
        cli = [a.supabase]
    elif shutil.which("supabase"):
        cli = ["supabase"]
    else:
        cli = ["npx", "--yes", "supabase@3.2.0"]
    env = status_env(cli)
    senha = secrets.token_urlsafe(24)
    definir_senha(senha)
    porta = env["DB_URL"].rsplit(":", 1)[1].split("/")[0]
    linhas = {
        "DATABASE_URL": f"postgresql://docredux_backend:{senha}@127.0.0.1:{porta}/postgres",
        "DATABASE_URL_ADMIN": env["DB_URL"],  # só testes (criar usuários, conferir invariantes)
        "SUPABASE_URL": env["API_URL"],
        "SUPABASE_SERVICE_ROLE_KEY": env.get("SECRET_KEY") or env["SERVICE_ROLE_KEY"],
        "SUPABASE_JWT_SECRET": env["JWT_SECRET"],
        "WEB_ORIGIN": a.web_origin,
        "WEB_URL": a.web_origin,
        "API_POOL_MAX": "5",
    }
    (API / ".env").write_text("".join(f"{k}={v}\n" for k, v in linhas.items()), encoding="utf-8")
    no_container = {k: (v.replace("127.0.0.1", "host.docker.internal") if k in ("DATABASE_URL", "SUPABASE_URL") else v)
                    for k, v in linhas.items() if k != "DATABASE_URL_ADMIN"}
    (API.parent / "deploy" / ".env.dev").write_text(
        "".join(f"{k}={v}\n" for k, v in no_container.items()), encoding="utf-8")
    print(f"api/.env e deploy/.env.dev gravados; docredux_backend com senha nova (porta {porta}).")


if __name__ == "__main__":
    main()
