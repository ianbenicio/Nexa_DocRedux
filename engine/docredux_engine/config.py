"""Configuração por ambiente do motor.

DOCREDUX_PERFIS   diretório dos perfis JSON (padrão: engine/perfis/)
DOCREDUX_SOFFICE  executável do LibreOffice (ver soffice.py)
DOCREDUX_LO_POOL  pasta do pool de perfis do LibreOffice (ver soffice.py)
DOCREDUX_FIXTURES diretório dos fixtures NN-slug/original/*.pdf (só testes)
"""
import os
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
PERFIL_DIR = Path(os.environ.get("DOCREDUX_PERFIS") or ENGINE_DIR / "perfis")
