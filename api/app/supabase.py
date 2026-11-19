"""Storage e Auth admin do Supabase pela API HTTP, com a chave service_role/secret (só no backend, §13)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx

from .config import config

ORIGINAIS = "originais"
SAIDAS = "saidas"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class SupabaseErro(Exception):
    pass


def _cabecalhos() -> dict[str, str]:
    chave = config().service_key
    if not chave:
        raise SupabaseErro("SUPABASE_SERVICE_ROLE_KEY ausente")
    h = {"apikey": chave}
    if not chave.startswith("sb_"):  # chave JWT legada; as novas (sb_secret_) vão só no apikey
        h["Authorization"] = f"Bearer {chave}"
    return h


def _url(caminho: str) -> str:
    return f"{config().supabase_url}{caminho}"


def _q(caminho: str) -> str:
    return quote(caminho, safe="/")


def _checar(r: httpx.Response, oque: str) -> httpx.Response:
    if r.status_code >= 400:
        # Sem corpo da resposta no erro: pode citar caminhos; o código basta para o log.
        raise SupabaseErro(f"{oque}: HTTP {r.status_code}")
    return r


# ---------------------------------------------------------------- Storage
def listar(bucket: str, prefixo: str) -> list[dict]:
    """Objetos e pastas diretamente sob `prefixo` (sem barra final). Pastas têm `id` nulo."""
    itens, offset = [], 0
    while True:
        r = httpx.post(_url(f"/storage/v1/object/list/{bucket}"), headers=_cabecalhos(), timeout=TIMEOUT,
                       json={"prefix": prefixo, "limit": 1000, "offset": offset,
                             "sortBy": {"column": "name", "order": "asc"}})
        lote = _checar(r, "listar").json()
        itens.extend(lote)
        if len(lote) < 1000:
            return itens
        offset += 1000


def tamanhos(bucket: str, prefixo: str) -> dict[str, int]:
    """{nome: bytes} dos arquivos sob `prefixo`."""
    return {i["name"]: int((i.get("metadata") or {}).get("size") or 0)
            for i in listar(bucket, prefixo) if i.get("id") is not None}


def baixar(bucket: str, caminho: str, destino: Path) -> int:
    with httpx.stream("GET", _url(f"/storage/v1/object/{bucket}/{_q(caminho)}"), headers=_cabecalhos(),
                      timeout=httpx.Timeout(300.0, connect=10.0)) as r:
        _checar(r, "baixar")
        n = 0
        with open(destino, "wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
                n += len(bloco)
    return n


def subir(bucket: str, caminho: str, arquivo: Path, content_type: str) -> None:
    with open(arquivo, "rb") as f:
        r = httpx.post(_url(f"/storage/v1/object/{bucket}/{_q(caminho)}"), content=f,
                       headers={**_cabecalhos(), "Content-Type": content_type, "x-upsert": "true"},
                       timeout=httpx.Timeout(300.0, connect=10.0))
    _checar(r, "subir")


def apagar(bucket: str, caminhos: list[str]) -> None:
    for i in range(0, len(caminhos), 500):
        r = httpx.request("DELETE", _url(f"/storage/v1/object/{bucket}"), headers=_cabecalhos(),
                          json={"prefixes": caminhos[i:i + 500]}, timeout=TIMEOUT)
        _checar(r, "apagar")


def apagar_prefixo(bucket: str, prefixo: str) -> int:
    """Apaga tudo sob `prefixo` (recursivo). Devolve quantos objetos foram apagados."""
    arquivos: list[str] = []
    pendentes = [prefixo.rstrip("/")]
    while pendentes:
        p = pendentes.pop()
        for i in listar(bucket, p):
            nome = f"{p}/{i['name']}"
            (arquivos if i.get("id") is not None else pendentes).append(nome)
    if arquivos:
        apagar(bucket, arquivos)
    return len(arquivos)


def assinar(bucket: str, caminho: str, segundos: int = 60, baixar_como: str | None = None) -> str:
    r = httpx.post(_url(f"/storage/v1/object/sign/{bucket}/{_q(caminho)}"), headers=_cabecalhos(),
                   json={"expiresIn": segundos}, timeout=TIMEOUT)
    assinado = _checar(r, "assinar").json()["signedURL"]
    url = _url(f"/storage/v1{assinado}" if not assinado.startswith("/storage/") else assinado)
    if baixar_como:
        url += ("&" if "?" in url else "?") + "download=" + quote(baixar_como)
    return url


# ---------------------------------------------------------------- Auth admin (§14 exclusão da conta)
def banir_usuario(usuario_id: str) -> None:
    r = httpx.put(_url(f"/auth/v1/admin/users/{usuario_id}"), headers=_cabecalhos(),
                  json={"ban_duration": "876000h"}, timeout=TIMEOUT)
    if r.status_code != 404:  # já removido do Auth: nada a bloquear
        _checar(r, "banir")


def remover_usuario(usuario_id: str) -> bool:
    """Remove do Auth. True se removeu ou já não existia."""
    r = httpx.delete(_url(f"/auth/v1/admin/users/{usuario_id}"), headers=_cabecalhos(), timeout=TIMEOUT)
    if r.status_code == 404:
        return True
    _checar(r, "remover usuário")
    return True


def usuario_existe(usuario_id: str) -> bool:
    r = httpx.get(_url(f"/auth/v1/admin/users/{usuario_id}"), headers=_cabecalhos(), timeout=TIMEOUT)
    if r.status_code == 404:
        return False
    _checar(r, "consultar usuário")
    return True
