"""Formato de erro do spec §9: {"erro": {"codigo": "...", "mensagem": "..."}}."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("docredux.api")

STATUS = {
    "nao_autenticado": 401,
    "proibido": 403,
    "termo_pendente": 403,
    "nao_encontrado": 404,
    "saldo_insuficiente": 402,
    "orcamento_mudou": 409,
    "estado_invalido": 409,
    "limite_excedido": 413,
    "validacao": 422,
    "capacidade_piloto": 503,  # DT-20
    "indisponivel": 503,
    "erro_interno": 500,
}

MENSAGENS = {
    "nao_autenticado": "Faça login de novo.",
    "proibido": "Você não tem acesso a esta área.",
    "termo_pendente": "Aceite o termo do piloto antes de enviar documentos.",
    "nao_encontrado": "Não encontrado.",
    "saldo_insuficiente": "Créditos insuficientes.",
    "orcamento_mudou": "O orçamento mudou. Confira o valor de novo.",
    "estado_invalido": "Esta ação não está disponível no estado atual do documento.",
    "limite_excedido": "O arquivo passa do limite de 80 MB.",
    "validacao": "Dados inválidos.",
    "capacidade_piloto": "O piloto atingiu o espaço disponível. Tente de novo mais tarde.",
    "indisponivel": "Serviço indisponível. Tente de novo em instantes.",
    "erro_interno": "Erro interno.",
}


class ApiErro(Exception):
    def __init__(self, codigo: str, mensagem: str | None = None, status: int | None = None):
        super().__init__(codigo)
        self.codigo = codigo
        self.mensagem = mensagem or MENSAGENS.get(codigo, codigo)
        self.status = status or STATUS.get(codigo, 400)


def resposta(codigo: str, mensagem: str | None = None, status: int | None = None) -> JSONResponse:
    e = ApiErro(codigo, mensagem, status)
    return JSONResponse({"erro": {"codigo": e.codigo, "mensagem": e.mensagem}}, status_code=e.status)


def registrar(app: FastAPI) -> None:
    @app.exception_handler(ApiErro)
    async def _api(_: Request, e: ApiErro):
        return resposta(e.codigo, e.mensagem, e.status)

    @app.exception_handler(RequestValidationError)
    async def _validacao(_: Request, e: RequestValidationError):
        campos = "; ".join(".".join(str(p) for p in err.get("loc", [])[1:]) or "corpo" for err in e.errors())
        return resposta("validacao", f"Dados inválidos: {campos}." if campos else None)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        codigo = {401: "nao_autenticado", 403: "proibido", 404: "nao_encontrado", 405: "nao_encontrado",
                  413: "limite_excedido", 422: "validacao"}.get(e.status_code, "erro_interno")
        return resposta(codigo, status=e.status_code if codigo != "erro_interno" else None)

    @app.exception_handler(Exception)
    async def _interno(request: Request, e: Exception):
        # Só o tipo e a rota: nunca conteúdo de documento nem dados do usuário (§13).
        log.error("erro interno em %s %s: %s", request.method, request.url.path, type(e).__name__)
        return resposta("erro_interno")
