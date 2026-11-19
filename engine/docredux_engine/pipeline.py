"""Pipeline completo de um documento: extrair -> render DOCX -> PDF (LibreOffice) -> verificar.

É o mesmo encadeamento que os scripts faziam à mão (extract.py, render_docx.py, soffice, verify.py),
agora numa função só, com tempos por etapa. `api.processar` monta o contrato público em cima disto.
"""
import json
import time
from pathlib import Path

import pymupdf

from . import soffice
from .ficha import extract as ficha_extract
from .ficha import render as ficha_render
from .verify import ficha as verify_ficha


def rodar(pdf, out, perfil=None, *, paginas=None, transformar=None, verificar=True, ao_progresso=None,
          manter_capa_elementos=False):
    """Roda o pipeline e devolve um dict com meta, render, verificacao, caminhos, páginas e tempos.

    perfil: dict (load_perfil); None = escolher pela assinatura (R33).
    transformar: função opcional modelo -> modelo aplicada entre a extração e o render (síntese, 2.4).
    verificar: False pula a verificação de cobertura (síntese não tem gate de cobertura, FR-13).
    """
    pdf, out = Path(pdf), Path(out)
    avisar = ao_progresso or (lambda etapa: None)
    tempos = {}

    t = time.perf_counter(); avisar("extracao")
    meta = ficha_extract.extrair(pdf, out, perfil, paginas=paginas, manter_capa_elementos=manter_capa_elementos)
    tempos["extracao"] = time.perf_counter() - t

    model = json.loads((out / "modelo.json").read_text(encoding="utf-8"))
    if transformar is not None:
        t = time.perf_counter(); avisar("sintese")
        model = transformar(model)
        tempos["sintese"] = time.perf_counter() - t

    t = time.perf_counter(); avisar("render_docx")
    render = ficha_render.renderizar(out, model)
    tempos["render_docx"] = time.perf_counter() - t
    docx = out / render["arquivo"]

    t = time.perf_counter(); avisar("pdf")
    pdf_saida = soffice.para_pdf(docx, out)
    tempos["pdf"] = time.perf_counter() - t
    with pymupdf.open(pdf_saida) as d:
        paginas_pdf = d.page_count

    verificacao = None
    if verificar:
        t = time.perf_counter(); avisar("verify")
        verificacao = verify_ficha.verificar(pdf, out, model, perfil)
        tempos["verify"] = time.perf_counter() - t

    return {"meta": meta, "modelo": model, "render": render, "verificacao": verificacao,
            "docx": docx, "pdf": pdf_saida, "paginas_pdf": paginas_pdf,
            "tempos_s": {k: round(v, 2) for k, v in tempos.items()}}
