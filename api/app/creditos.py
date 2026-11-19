"""Preço em créditos (spec §5, DT-8, DT-10). Função pura, só inteiros."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# Síntese: taxa × 10 por fração (0,6 / 0,8 / 1,0 crédito por mil palavras), DT-10.
TAXA_SINTESE_X10 = {0.2: 6, 0.35: 8, 0.5: 10}


class Opcoes(BaseModel):
    """Opções do envio (U-B, U-C, U-E). Mesmo formato do `Opcoes` do motor (§4)."""
    model_config = ConfigDict(extra="forbid")

    modo: Literal["original", "sintese"] = "original"
    fracao: Literal[0.2, 0.35, 0.5] | None = None
    manter_capa_elementos: bool = False
    docx: bool = False

    @model_validator(mode="after")
    def _fracao(self):
        if self.modo == "sintese" and self.fracao is None:
            raise ValueError("fracao é obrigatória no modo sintese")
        if self.modo == "original" and self.fracao is not None:
            raise ValueError("fracao só vale no modo sintese")
        return self


def _teto(a: int, b: int) -> int:
    return -(-a // b)


def servico_padrao(paginas: int) -> int:
    if paginas <= 5:
        return 2
    if paginas <= 15:
        return 4
    if paginas <= 20:
        return 6
    return 6 + 2 * _teto(paginas - 20, 20)


def sintese(palavras: int, fracao: float) -> int:
    return max(1, _teto(TAXA_SINTESE_X10[fracao] * palavras, 10000))


def template_novo_sobretaxa(paginas: int) -> int:
    if paginas <= 15:
        return 8
    if paginas <= 60:
        return 12
    return 16


def orcar(paginas: int, palavras: int, opcoes: Opcoes, template_novo: bool) -> dict:
    """Itens (`rotulo`, `creditos`) e total. `palavras` = contagem do verify gravada na triagem (DT-21)."""
    if paginas < 1 or palavras < 0:
        raise ValueError("páginas e palavras inválidas")
    itens = [{"rotulo": "Serviço padrão", "creditos": servico_padrao(paginas)}]
    if opcoes.docx:
        itens.append({"rotulo": "DOCX editável", "creditos": 1 if paginas <= 15 else 3})
    if opcoes.manter_capa_elementos:
        itens.append({"rotulo": "Manter capa e elementos", "creditos": 1 if paginas <= 15 else 2})
    if opcoes.modo == "sintese":
        pct = round(opcoes.fracao * 100)
        itens.append({"rotulo": f"Síntese {pct}%", "creditos": sintese(palavras, opcoes.fracao)})
    if template_novo:
        itens.append({"rotulo": "Template novo", "creditos": template_novo_sobretaxa(paginas)})
    return {"itens": itens, "total": sum(i["creditos"] for i in itens)}
