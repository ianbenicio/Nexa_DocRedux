"""Fixtures de integração contra o Supabase local (db/, portas 553xx).

Pré-requisito: `supabase start` em db/ e `python scripts/dev_setup.py` (grava api/.env).
Sem api/.env, os testes de integração são pulados; os puros (test_creditos) rodam sempre.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from app.config import carregar_env_local

carregar_env_local()

INTEGRACAO = bool(os.environ.get("DATABASE_URL") and os.environ.get("DATABASE_URL_ADMIN"))
requer_db = pytest.mark.skipif(not INTEGRACAO, reason="sem api/.env (rode scripts/dev_setup.py)")


@pytest.fixture(scope="session")
def admin():
    """Conexão `postgres` (autocommit): cria usuários e ajusta estado nos testes. Nunca usada pela API."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ["DATABASE_URL_ADMIN"], autocommit=True, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


def token(uid, exp_s: int = 3600, segredo: str | None = None) -> str:
    import jwt
    agora = int(time.time())
    return jwt.encode({"sub": str(uid), "aud": "authenticated", "role": "authenticated", "iat": agora,
                       "exp": agora + exp_s}, segredo or os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")


class Conta:
    def __init__(self, admin, papel="usuario", termo=True):
        # Pela API admin do Auth (como o convite da unidade 5): o ban e a remoção do §14 agem nele de verdade.
        import httpx
        chave = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        h = {"apikey": chave, **({} if chave.startswith("sb_") else {"Authorization": f"Bearer {chave}"})}
        self.email = f"teste-{uuid.uuid4().hex[:10]}@teste.local"
        r = httpx.post(f"{os.environ['SUPABASE_URL']}/auth/v1/admin/users", headers=h, timeout=30,
                       json={"email": self.email, "password": uuid.uuid4().hex, "email_confirm": True})
        r.raise_for_status()
        self.id = uuid.UUID(r.json()["id"])
        if papel != "usuario":
            admin.execute("update public.perfis_usuario set papel = %s where id = %s", (papel, self.id))
        if termo:
            admin.execute("update public.perfis_usuario set termo_versao = '1', termo_aceito_em = now() where id = %s",
                          (self.id,))
        self.h = {"Authorization": f"Bearer {token(self.id)}"}

    def saldo(self, admin) -> int:
        return admin.execute("select saldo from public.perfis_usuario where id = %s", (self.id,)).fetchone()["saldo"]


@pytest.fixture
def conta(admin):
    def _nova(**kw) -> Conta:
        return Conta(admin, **kw)
    return _nova


def doc_triado(admin, conta: Conta, *, paginas=10, palavras=2000, tipo="relatorio", template_novo=False,
               status="aguardando_confirmacao") -> uuid.UUID:
    """Documento já triado (sem passar pelo worker): base dos testes de orçamento e confirmação."""
    row = admin.execute(
        """insert into public.documentos (usuario_id, idempotency_key, nome_arquivo, tamanho_bytes, status, paginas,
                                           palavras, tipo, template_novo)
           values (%s, %s, 'teste.pdf', 1000, %s, %s, %s, %s, %s) returning id""",
        (conta.id, str(uuid.uuid4()), status, paginas, palavras, tipo, template_novo)).fetchone()
    return row["id"]


def invariantes(admin) -> list:
    return admin.execute("select * from private.invariantes_credito()").fetchall()
