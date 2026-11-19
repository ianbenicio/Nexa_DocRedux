"""Gera db/seed.sql com os perfis semente de engine/perfis/*.json (Story 3.1, spec §6 seed, Q5).

Uso:
    python db/scripts/gerar_seed.py            # reescreve db/seed.sql
    python db/scripts/gerar_seed.py --conferir # falha (exit 1) se o seed versionado estiver velho

Perfil semente = dono nulo, visível a todas as contas. id = campo "nome" do JSON.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PERFIS = RAIZ / "engine" / "perfis"
SEED = RAIZ / "db" / "seed.sql"
TIPOS = {"ficha", "relatorio"}


def _literal(texto: str) -> str:
    """Literal SQL com dollar quoting; o marcador não pode aparecer no texto."""
    marca = "$perfil$"
    if marca in texto:
        raise ValueError("marcador $perfil$ dentro do JSON")
    return f"{marca}{texto}{marca}"


def gerar() -> str:
    linhas = [
        "-- GERADO por db/scripts/gerar_seed.py a partir de engine/perfis/*.json. Não editar à mão.",
        "-- Perfis semente (dono nulo, visíveis a todas as contas; spec §6, Q5).",
        "",
    ]
    arquivos = sorted(PERFIS.glob("*.json"))
    if not arquivos:
        raise SystemExit(f"nenhum perfil em {PERFIS}")
    for arq in arquivos:
        perfil = json.loads(arq.read_text(encoding="utf-8"))
        nome, tipo = perfil.get("nome"), perfil.get("tipo")
        if not nome or tipo not in TIPOS:
            raise SystemExit(f"{arq.name}: 'nome' ausente ou 'tipo' fora de {sorted(TIPOS)}")
        corpo = json.dumps(perfil, ensure_ascii=False, sort_keys=True)
        linhas += [
            f"-- {arq.name}",
            "insert into public.perfis_template (id, dono, tipo, json, versao, ativo)",
            f"values ('{nome}', null, '{tipo}', {_literal(corpo)}::jsonb, 1, true)",
            "on conflict (id) do update set tipo = excluded.tipo, json = excluded.json;",
            "",
        ]
    return "\n".join(linhas)


def main() -> int:
    novo = gerar()
    if "--conferir" in sys.argv[1:]:
        atual = SEED.read_text(encoding="utf-8") if SEED.exists() else ""
        if atual != novo:
            print(f"{SEED} desatualizado: rode python db/scripts/gerar_seed.py", file=sys.stderr)
            return 1
        print("seed.sql em dia")
        return 0
    SEED.write_text(novo, encoding="utf-8", newline="\n")
    print(f"escrito {SEED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
