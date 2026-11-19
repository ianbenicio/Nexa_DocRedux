"""Autenticação (spec §9): JWT do Supabase no `Authorization: Bearer`.

- HS256 com `SUPABASE_JWT_SECRET`, ou chaves assimétricas (ES256/RS256) do JWKS do projeto.
- O `sub` é o usuario_id. Papel, termo e exclusão vêm do banco a cada requisição, nunca do token.
- Conta com `exclusao_conta_em` → 401 mesmo com JWT válido: o ban no Auth não invalida tokens já
  emitidos, e o §14 pede o login bloqueado na hora.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Header

from .config import config
from .db import config_valor, transacao
from .erros import ApiErro


@dataclass(frozen=True)
class Usuario:
    id: UUID
    email: str | None
    papel: str
    interno: bool
    saldo: int
    termo_versao: str | None
    termo_aceito_em: datetime | None
    senha_provisoria: bool
    termo_atual: str

    @property
    def termo_aceito(self) -> bool:
        return self.termo_versao is not None and self.termo_versao == self.termo_atual


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{config().supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def decodificar(token: str) -> dict:
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        opcoes = {"require": ["sub", "exp"]}
        if alg == "HS256":
            segredo = config().jwt_secret
            if not segredo:
                raise ApiErro("nao_autenticado")
            return jwt.decode(token, segredo, algorithms=["HS256"], audience="authenticated", options=opcoes)
        if alg in ("ES256", "RS256"):
            chave = _jwks().get_signing_key_from_jwt(token).key
            return jwt.decode(token, chave, algorithms=[alg], audience="authenticated", options=opcoes)
    except ApiErro:
        raise
    except Exception as e:  # noqa: BLE001 — token inválido, expirado, JWKS fora do ar
        raise ApiErro("nao_autenticado") from e
    raise ApiErro("nao_autenticado")


def usuario_atual(authorization: str | None = Header(default=None)) -> Usuario:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiErro("nao_autenticado")
    claims = decodificar(authorization.split(" ", 1)[1].strip())
    try:
        uid = UUID(str(claims["sub"]))
    except ValueError as e:
        raise ApiErro("nao_autenticado") from e
    with transacao() as conn:
        row = conn.execute(
            """select id, email, papel::text, interno, saldo, termo_versao, termo_aceito_em,
                      senha_provisoria, exclusao_conta_em
                 from public.perfis_usuario where id = %s""", (uid,)).fetchone()
        termo_atual = str(config_valor(conn, "termo_versao_atual", "1"))
    if row is None or row["exclusao_conta_em"] is not None:
        raise ApiErro("nao_autenticado")
    return Usuario(id=row["id"], email=row["email"], papel=row["papel"], interno=row["interno"],
                   saldo=row["saldo"], termo_versao=row["termo_versao"], termo_aceito_em=row["termo_aceito_em"],
                   senha_provisoria=row["senha_provisoria"], termo_atual=termo_atual)


def exigir_termo(u: Usuario) -> Usuario:
    if not u.termo_aceito:
        raise ApiErro("termo_pendente")
    return u


def exigir_admin(u: Usuario) -> Usuario:
    if u.papel != "admin":
        raise ApiErro("proibido")
    return u
