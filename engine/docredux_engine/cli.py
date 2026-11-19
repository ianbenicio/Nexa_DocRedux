"""CLI do motor: `python -m docredux_engine <comando> ...`, sempre com UM objeto JSON no stdout.

É assim que o worker chama o motor em subprocesso (DT-5): timeout duro e falha isolada por job.
Avisos e progresso vão para o stderr. Código de saída: 0 ok, 1 erro (o JSON traz "erro").
"""
import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .api import MotorErro, Opcoes, PdfRecusado, PerfilInvalido, carregar_perfis, folha_contato, processar, triar, validar_perfil
from .config import PERFIL_DIR
from .ficha.extract import load_perfil


def _perfil(valor):
    """Caminho de um JSON ou nome de um perfil de PERFIL_DIR."""
    p = Path(valor)
    return load_perfil(p if p.suffix == ".json" else PERFIL_DIR / f"{valor}.json")


def _perfis(diretorio):
    return carregar_perfis(Path(diretorio) if diretorio else None)


def _paginas(valor):
    return [int(x) for x in valor.split(",")] if valor else None


def cmd_triar(a):
    return triar(a.pdf, _perfis(a.perfis_dir), max_paginas=a.max_paginas, max_bytes=a.max_mb * 2**20).to_dict()


def cmd_processar(a):
    if a.perfil:
        perfil = _perfil(a.perfil)
    else:  # sem --perfil: triagem decide (o worker sempre passa o perfil da triagem)
        t = triar(a.pdf, _perfis(a.perfis_dir))
        if t.recusa:
            raise PdfRecusado(t.recusa)
        if t.perfil_id is None:
            raise MotorErro("template desconhecido: nenhum perfil casa com as fontes do PDF")
        perfil = next(p for p in _perfis(a.perfis_dir) if p["nome"] == t.perfil_id)
    op = Opcoes(modo=a.modo, fracao=a.fracao, manter_capa_elementos=a.manter_capa_elementos, docx=a.docx)
    return processar(a.pdf, op, perfil, a.saida,
                     ao_progresso=lambda etapa: print(f"etapa: {etapa}", file=sys.stderr)).to_dict()


def cmd_folha(a):
    return {"arquivos": [str(p) for p in folha_contato(a.pdf, a.saida, dpi=a.dpi, paginas=_paginas(a.paginas))]}


def cmd_validar(a):
    erros = validar_perfil(json.loads(Path(a.perfil).read_text(encoding="utf-8")))
    return {"valido": not erros, "erros": erros}


def parser():
    ap = argparse.ArgumentParser(prog="docredux_engine", description=__doc__)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="comando", required=True)

    t = sub.add_parser("triar", help="recusa, tipo, perfil, páginas e palavras (Triagem)")
    t.add_argument("pdf")
    t.add_argument("--perfis-dir", help="diretório dos perfis (padrão: engine/perfis/ ou DOCREDUX_PERFIS)")
    t.add_argument("--max-paginas", type=int, default=300)
    t.add_argument("--max-mb", type=int, default=80)
    t.set_defaults(func=cmd_triar)

    p = sub.add_parser("processar", help="PDF -> PDF compacto (+ DOCX) + relatório (Resultado)")
    p.add_argument("pdf"); p.add_argument("saida")
    p.add_argument("--perfil", help="nome em perfis/ ou caminho .json (padrão: o da triagem)")
    p.add_argument("--perfis-dir")
    p.add_argument("--modo", choices=["original", "sintese"], default="original")
    p.add_argument("--fracao", type=float, choices=[0.2, 0.35, 0.5])
    p.add_argument("--manter-capa-elementos", action="store_true")
    p.add_argument("--docx", action="store_true", help="entregar também o DOCX")
    p.set_defaults(func=cmd_processar)

    f = sub.add_parser("folha", help="folha de contato (PNG)")
    f.add_argument("pdf"); f.add_argument("saida")
    f.add_argument("--dpi", type=int, default=60)
    f.add_argument("--paginas", help="1-based, separadas por vírgula")
    f.set_defaults(func=cmd_folha)

    v = sub.add_parser("validar-perfil", help="valida um perfil JSON contra o schema")
    v.add_argument("perfil")
    v.set_defaults(func=cmd_validar)
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    stdout, sys.stdout = sys.stdout, sys.stderr  # prints internos (PyMuPDF etc.) não sujam o JSON
    try:
        saida, codigo = a.func(a), 0
    except Exception as e:  # noqa: BLE001 — o worker lê o motivo no JSON
        saida, codigo = {"erro": type(e).__name__, "mensagem": str(e)}, 1
        if isinstance(e, PdfRecusado):
            saida["motivo"] = e.motivo
        if isinstance(e, PerfilInvalido):
            saida["erros"] = e.erros
    finally:
        sys.stdout = stdout
    # Protocolo ASCII (subconjunto de UTF-8), independente da pagina de codigo do host.
    sys.stdout.write(json.dumps(saida, ensure_ascii=True, default=str) + "\n")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
