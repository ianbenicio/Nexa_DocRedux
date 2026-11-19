"""App FastAPI (spec §9). Roda com uvicorn (`uvicorn app.main:app`) e como função Python da Vercel (`index.py`)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import VERSAO, erros
from .config import config
from .db import fechar_pool
from .routers import conta, documentos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def _vida(_: FastAPI):
    yield
    fechar_pool()


def criar_app() -> FastAPI:
    app = FastAPI(title="DocRedux API", version=VERSAO, lifespan=_vida, docs_url=None, redoc_url=None)
    # CORS só para a origem do web/ (AC 4.1-8).
    app.add_middleware(CORSMiddleware, allow_origins=[config().web_origin], allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])
    erros.registrar(app)
    app.include_router(conta.router)
    app.include_router(documentos.router)
    return app


app = criar_app()
