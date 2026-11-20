"""Proveniencia do texto do DOCX: o que o motor cria nao entra na comparacao com o original (A5).

Revisao 2026-11-20, A5: a caixa de identificacao ("Versao compacta gerada a partir de: 2.pdf") e o
sumario (numeros de pagina da saida) trazem palavras e numeros que nao vem do corpo do original. Na
comparacao global eles podiam "achar" um numero perdido no corpo e esconder o erro.

O render marca cada run gerado com o estilo de caractere ESTILO (sem formatacao propria; a aparencia
continua a do run). Os verificadores pulam runs com esse estilo. A linha de identificacao tambem e
reconhecida pelo texto (LINHA_ID), para DOCX sem marca.
"""
import re

from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn

ESTILO = "DocRedux Gerado"
ESTILO_ID = "DocReduxGerado"
LINHA_ID = re.compile(r"^\s*Vers[aã]o compacta gerada a partir de:", re.IGNORECASE)


def marcar(doc, *runs):
    """Marca os runs como texto criado pelo motor. Devolve o primeiro run (encadeamento)."""
    estilos = doc.styles
    st = next((s for s in estilos if s.type == WD_STYLE_TYPE.CHARACTER and s.name == ESTILO), None)
    if st is None:
        st = estilos.add_style(ESTILO, WD_STYLE_TYPE.CHARACTER)
        st.hidden = True  # nao aparece na galeria de estilos do Word
    for r in runs:
        r.style = st
    return runs[0] if runs else None


def eh_gerado(r):
    """r: elemento w:r."""
    rs = r.find(qn("w:rPr") + "/" + qn("w:rStyle"))
    return rs is not None and rs.get(qn("w:val")) == ESTILO_ID
