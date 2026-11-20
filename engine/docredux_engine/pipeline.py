"""Pipeline completo de um documento: extrair -> render DOCX -> PDF (LibreOffice) -> verificar.

É o mesmo encadeamento que os scripts faziam à mão (extract.py, render_docx.py, soffice, verify.py),
agora numa função só, com tempos por etapa. `api.processar` monta o contrato público em cima disto.
"""
import json
import time
from collections import Counter
from pathlib import Path

import pymupdf

from . import soffice
from .ficha import encaixe
from .ficha import extract as ficha_extract
from .ficha import render as ficha_render
from .verify import ficha as verify_ficha


COMPLETUDE_PDF = 0.995  # fração mínima das palavras do DOCX presentes no PDF entregue
_TEXTO = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES  # "fi" ligado quebraria a palavra


def validar_pdf_renderizado(pdf_saida, docx=None):
    """Protecao do artefato entregue, sem confundir ausencia de texto com pagina vazia.

    Com `docx`, confere também se o PDF está completo: as palavras do DOCX precisam estar no texto do PDF
    (>= COMPLETUDE_PDF). A cobertura do gate é medida sobre o DOCX, então sem isto um PDF truncado pelo
    LibreOffice passaria (revisão 2026-11-20, A2). Medido nos ex. 01-05: 100% nas fichas, 99,997% no ex. 05
    (3 palavras: URL partida na quebra de linha e número de nota colado).
    """
    with pymupdf.open(pdf_saida) as doc:
        if not doc.is_pdf or doc.page_count == 0:
            raise RuntimeError("PDF gerado sem páginas válidas")
        textos = [p.get_text("text", flags=_TEXTO) for p in doc]
        if not any(t.strip() or ficha_extract.has_visual_content(p) for t, p in zip(textos, doc)):
            raise RuntimeError("PDF gerado sem conteúdo visível")
        if docx is not None:
            esperado = verify_ficha.docx_words(docx)
            total = sum(esperado.values())
            if total:
                achado = Counter()
                for t in textos:
                    achado.update(verify_ficha.words(t))
                completude = 1 - sum((esperado - achado).values()) / total
                if completude < COMPLETUDE_PDF:
                    raise RuntimeError(f"PDF incompleto: {completude * 100:.2f}% das palavras do DOCX "
                                       f"(mínimo {COMPLETUDE_PDF * 100:.1f}%)".replace(".", ","))
        return doc.page_count


def _encaixar(model, out, render, docx, pdf_saida, paginas_pdf):
    """R52: ate PASSADAS_MAX passadas extras, cada uma encolhendo a imagem do 1o buraco que ela resolve.

    Passada recusada (imagem nao subiu ou pagina a mais) volta o DOCX/PDF anterior; a proxima tenta outro buraco."""
    tentados = set()
    for _ in range(encaixe.PASSADAS_MAX):
        alvo = encaixe.candidato(pdf_saida, model, tentados)
        if alvo is None:
            break
        antes = docx.read_bytes(), pdf_saida.read_bytes()
        for i in alvo["idx"]:
            model["elementos"][i]["encaixe"] = alvo["k"]
        novo = ficha_render.renderizar(out, model)
        pdf_novo = soffice.para_pdf(docx, out)
        paginas_novo = validar_pdf_renderizado(pdf_novo, docx)
        if not encaixe.aceito(pdf_novo, model, alvo, paginas_pdf):
            for i in alvo["idx"]:
                del model["elementos"][i]["encaixe"]
            docx.write_bytes(antes[0]); pdf_saida.write_bytes(antes[1])
            tentados.update(alvo["idx"])
            continue
        render, pdf_saida, paginas_pdf = novo, pdf_novo, paginas_novo
    render["encaixes"] = sum(1 for e in model["elementos"] if e.get("encaixe"))
    return render, paginas_pdf


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
    paginas_pdf = validar_pdf_renderizado(pdf_saida, docx)
    tempos["pdf"] = time.perf_counter() - t

    if model["meta"].get("tipo") != "relatorio":  # R52: imagem que deixou buraco no fim da pagina
        t = time.perf_counter()
        render, paginas_pdf = _encaixar(model, out, render, docx, pdf_saida, paginas_pdf)
        tempos["encaixe"] = time.perf_counter() - t

    verificacao = None
    if verificar:
        t = time.perf_counter(); avisar("verify")
        verificacao = verify_ficha.verificar(pdf, out, model, perfil, docx_path=docx, pdf_path=pdf_saida)
        tempos["verify"] = time.perf_counter() - t

    return {"meta": meta, "modelo": model, "render": render, "verificacao": verificacao,
            "docx": docx, "pdf": pdf_saida, "paginas_pdf": paginas_pdf,
            "tempos_s": {k: round(v, 2) for k, v in tempos.items()}}
