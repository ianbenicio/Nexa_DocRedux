"""Postgres como docredux_backend (DT-14).

A API usa o pooler em modo transação: sem prepared statements (`prepare_threshold=None`) e pool ≤ 5.
O worker abre uma conexão por processo (`conectar`).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import config

_pool: ConnectionPool | None = None


def conectar(autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(config().database_url, row_factory=dict_row, prepare_threshold=None,
                           autocommit=autocommit, application_name="docredux")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config().database_url, min_size=0, max_size=config().pool_max, open=True,
                               kwargs={"row_factory": dict_row, "prepare_threshold": None,
                                       "application_name": "docredux-api"})
    return _pool


def fechar_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def transacao() -> Iterator[psycopg.Connection]:
    """Uma transação do pool: commit no fim, rollback em exceção."""
    with pool().connection() as conn:
        yield conn


def config_valor(conn: psycopg.Connection, chave: str, padrao: Any = None) -> Any:
    row = conn.execute("select valor from public.config where chave = %s", (chave,)).fetchone()
    if row is None or row["valor"] is None:
        return padrao
    return row["valor"]
