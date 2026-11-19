"""Configuração por variável de ambiente (spec §13). Nenhum segredo no código nem no repositório.

DATABASE_URL               Postgres como docredux_backend (API: pooler modo transação 6543; worker: sessão 5432)
SUPABASE_URL               URL do projeto (Auth e Storage)
SUPABASE_SERVICE_ROLE_KEY  chave service_role/secret: Storage e Auth admin, só no backend
SUPABASE_JWT_SECRET        segredo HS256 do projeto (opcional; sem ele, as chaves públicas do JWKS)
WEB_ORIGIN                 origem do web/ para o CORS (ex.: https://docredux.com.br)
WEB_URL                    base dos links dos e-mails (padrão: WEB_ORIGIN)
RESEND_API_KEY             chave do Resend (DT-22); vazia = avisos desligados
RESEND_FROM                remetente dos avisos (ex.: DocRedux <avisos@docredux.com.br>)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENV_LOCAL = Path(__file__).resolve().parents[1] / ".env"


def carregar_env_local(arquivo: Path = ENV_LOCAL) -> None:
    """Desenvolvimento: lê api/.env (gitignored, gerado por scripts/dev_setup.py) sem sobrescrever o ambiente.
    Em produção o arquivo não existe (Vercel e container recebem as variáveis de fora)."""
    if arquivo.exists():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, v = linha.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class Config:
    database_url: str
    supabase_url: str
    service_key: str
    jwt_secret: str | None
    web_origin: str
    web_url: str
    resend_api_key: str | None
    resend_from: str | None
    pool_max: int


def _env(nome: str, padrao: str | None = None) -> str | None:
    v = os.environ.get(nome, "").strip()
    return v or padrao


@lru_cache
def config() -> Config:
    carregar_env_local()
    web_origin = _env("WEB_ORIGIN", "http://localhost:3000")
    return Config(
        database_url=_env("DATABASE_URL", ""),
        supabase_url=(_env("SUPABASE_URL", "") or "").rstrip("/"),
        service_key=_env("SUPABASE_SERVICE_ROLE_KEY", ""),
        jwt_secret=_env("SUPABASE_JWT_SECRET"),
        web_origin=web_origin,
        web_url=(_env("WEB_URL", web_origin) or "").rstrip("/"),
        resend_api_key=_env("RESEND_API_KEY"),
        resend_from=_env("RESEND_FROM"),
        pool_max=int(_env("API_POOL_MAX", "5")),
    )
