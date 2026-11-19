import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXEMPLOS = {
    "01": ("01-mathema-saeb-5ano-lp-p3", "ficha", "mathema-saeb"),
    "02": ("02-mathema-saeb-4ano-lp-p3", "ficha", "mathema-saeb"),
    "03": ("03-mathema-saeb-4ano-mat-p3", "ficha", "mathema-saeb"),
    "04": ("04-mathema-saeb-5ano-mat-p3", "ficha", "mathema-saeb"),
    "05": ("05-diagnostico-educacao-nacional", "relatorio", "mec-sase-relatorio"),
}


def fixture_pdf(n):
    """PDF do exemplo `n` em DOCREDUX_FIXTURES, ou pula o teste (fixtures ficam fora do git, Q1)."""
    base = os.environ.get("DOCREDUX_FIXTURES")
    if not base:
        pytest.skip("DOCREDUX_FIXTURES não definido")
    achados = sorted((Path(base) / EXEMPLOS[n][0] / "original").glob("*.pdf"))
    if not achados:
        pytest.skip(f"fixture {EXEMPLOS[n][0]} ausente")
    return achados[0]


@pytest.fixture(scope="session")
def perfis():
    from docredux_engine.api import carregar_perfis
    return carregar_perfis()


@pytest.fixture(scope="session")
def perfil_por_nome(perfis):
    return {p["nome"]: p for p in perfis}
