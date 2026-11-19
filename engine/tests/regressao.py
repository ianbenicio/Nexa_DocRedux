"""Regressão do motor (NFR-6, DT-11): ex. 01–05 contra o baseline do LOG.md.

Uso (de engine/):
    python tests/regressao.py [--so 01,05] [--saida DIR] [--manter-capa-elementos] [--json ARQ]

Lê os PDFs de $DOCREDUX_FIXTURES/<NN-slug>/original/*.pdf (fora do git, Q1). Para cada exemplo roda
o pipeline completo (extração → DOCX → PDF pelo LibreOffice → verificação) e compara:
  - páginas do PDF de saída == baseline (tests/baseline.json);
  - cobertura >= baseline (nunca pode cair, Q13);
  - gate ok (inclui a integridade numérica M09, story 2.7).
Sai com código 1 se algum exemplo falhar; 2 se não houver fixtures.
"""
import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

from docredux_engine import __version__, soffice  # noqa: E402
from docredux_engine.api import Opcoes, carregar_perfis, processar  # noqa: E402


def fixtures_dir():
    d = os.environ.get("DOCREDUX_FIXTURES")
    if not d or not Path(d).is_dir():
        sys.exit(f"DOCREDUX_FIXTURES não aponta um diretório ({d!r}); ver engine/README.md")
    return Path(d)


def pdf_do_exemplo(base, slug):
    achados = sorted((base / slug / "original").glob("*.pdf"))
    return achados[0] if achados else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--so", help="prefixos dos exemplos, ex.: 01,05")
    ap.add_argument("--saida", default=str(AQUI / "_saida"))
    ap.add_argument("--manter-capa-elementos", action="store_true")
    ap.add_argument("--json", help="grava o resultado neste arquivo")
    a = ap.parse_args(argv)

    base = fixtures_dir()
    baseline = {k: v for k, v in json.loads((AQUI / "baseline.json").read_text(encoding="utf-8")).items()
                if not k.startswith("_")}
    if a.so:
        pref = tuple(x.strip() for x in a.so.split(","))
        baseline = {k: v for k, v in baseline.items() if k.startswith(pref)}
    perfis = {p["nome"]: p for p in carregar_perfis()}
    opcoes = Opcoes(manter_capa_elementos=a.manter_capa_elementos)

    ambiente = {"motor": __version__, "python": platform.python_version(), "so": platform.platform(),
                "maquina": platform.machine(), "libreoffice": soffice.versao(),
                "manter_capa_elementos": a.manter_capa_elementos}
    print(json.dumps(ambiente, ensure_ascii=False))
    linhas, falhas, encontrados = [], 0, 0
    for slug, esp in baseline.items():
        pdf = pdf_do_exemplo(base, slug)
        if pdf is None:
            linhas.append({"exemplo": slug, "ok": False, "erro": "fixture ausente"}); falhas += 1
            continue
        encontrados += 1
        out = Path(a.saida) / slug
        t0 = time.perf_counter()
        try:
            res = processar(pdf, opcoes, perfis[esp["perfil"]], out)
        except Exception as e:  # noqa: BLE001 — a regressão registra e segue para o próximo exemplo
            linhas.append({"exemplo": slug, "ok": False, "erro": f"{type(e).__name__}: {e}"}); falhas += 1
            continue
        r = res.relatorio
        antes, depois = r.paginas_antes, r.paginas_depois
        cob = round(r.cobertura * 100, 2)
        # com manter_capa_elementos a saída cresce (capa, fotos, decorativos): basta não encolher; a cobertura vale igual
        ok_pag = depois >= esp["paginas"] if a.manter_capa_elementos else depois == esp["paginas"]
        ok_cob = cob is not None and cob >= esp["cobertura_min"]
        ok = ok_pag and ok_cob and antes == esp["paginas_original"] and r.gate == "ok"  # gate inclui M09 (2.7)
        falhas += 0 if ok else 1
        linhas.append({"exemplo": slug, "ok": ok,
                       "paginas": f"{antes}→{depois}",
                       "paginas_baseline": f"{esp['paginas_original']}→{esp['paginas']}",
                       "cobertura_pct": cob, "cobertura_min": esp["cobertura_min"],
                       "gate": r.gate, "motivo_gate": r.motivo_gate, "tempo_s": round(time.perf_counter() - t0, 1), "tempos_s": r.tempos_s})
    for ln in linhas:
        if "erro" in ln:
            print(f"FALHA {ln['exemplo']}: {ln['erro']}")
        else:
            st = "OK   " if ln["ok"] else "FALHA"
            print(f"{st} {ln['exemplo']:<36} {ln['paginas']:>9} (baseline {ln['paginas_baseline']})  "
                  f"cobertura {ln['cobertura_pct']}% (mín. {ln['cobertura_min']}%)  {ln['tempo_s']} s"
                  + (f"  gate: {ln['motivo_gate']}" if ln["gate"] != "ok" else ""))
    resumo = {"ambiente": ambiente, "exemplos": linhas, "falhas": falhas}
    if a.json:
        Path(a.json).write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(linhas) - falhas}/{len(linhas)} OK")
    if not encontrados:
        return 2
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
