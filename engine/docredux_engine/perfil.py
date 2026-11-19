"""Validação de perfil contra `perfil.schema.json` (JSON Schema 2020-12).

Decisão da story 2.3: o motor não depende do pacote `jsonschema`. Este validador cobre o subconjunto que o
schema usa (type, enum, const, required, properties, additionalProperties, items, minItems, maxItems,
minLength, pattern, minimum, maximum, exclusiveMinimum, allOf, if/then, $ref local e format "regex").
Os testes conferem que o schema também é JSON Schema válido para o `jsonschema`, quando ele existir.
"""
import json
import re
from pathlib import Path

SCHEMA = json.loads((Path(__file__).with_name("perfil.schema.json")).read_text(encoding="utf-8"))

_TIPOS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
}


def _ref(no):
    while "$ref" in no:
        alvo = SCHEMA
        for parte in no["$ref"].lstrip("#/").split("/"):
            alvo = alvo[parte]
        no = {**alvo, **{k: v for k, v in no.items() if k != "$ref"}}
    return no


def _valida(v, no, caminho, erros):
    no = _ref(no)
    onde = caminho or "perfil"
    if "type" in no:
        tipos = no["type"] if isinstance(no["type"], list) else [no["type"]]
        if not any(_TIPOS[t](v) for t in tipos):
            erros.append(f"{onde}: esperado {' ou '.join(tipos)}")
            return
    if "const" in no and v != no["const"]:
        erros.append(f"{onde}: deve ser {no['const']!r}")
    if "enum" in no and v not in no["enum"]:
        erros.append(f"{onde}: deve ser um de {no['enum']}")
    if isinstance(v, str):
        if len(v) < no.get("minLength", 0):
            erros.append(f"{onde}: texto curto demais")
        if "pattern" in no and not re.search(no["pattern"], v):
            erros.append(f"{onde}: formato inválido ({v!r})")
        if no.get("format") == "regex":
            try:
                re.compile(v)
            except re.error as e:
                erros.append(f"{onde}: expressão regular inválida ({e})")
    if _TIPOS["number"](v):
        if "minimum" in no and v < no["minimum"]:
            erros.append(f"{onde}: menor que {no['minimum']}")
        if "maximum" in no and v > no["maximum"]:
            erros.append(f"{onde}: maior que {no['maximum']}")
        if "exclusiveMinimum" in no and v <= no["exclusiveMinimum"]:
            erros.append(f"{onde}: deve ser maior que {no['exclusiveMinimum']}")
    if isinstance(v, list):
        if len(v) < no.get("minItems", 0):
            erros.append(f"{onde}: precisa de ao menos {no['minItems']} item(ns)")
        if "maxItems" in no and len(v) > no["maxItems"]:
            erros.append(f"{onde}: no máximo {no['maxItems']} item(ns)")
        if "items" in no:
            for i, x in enumerate(v):
                _valida(x, no["items"], f"{onde}[{i}]", erros)
    if isinstance(v, dict):
        for k in no.get("required", []):
            if k not in v:
                erros.append(f"{onde}: falta o campo obrigatório {k!r}")
        props = no.get("properties", {})
        for k, x in v.items():
            if k in props:
                _valida(x, props[k], f"{caminho}.{k}" if caminho else k, erros)
            elif isinstance(no.get("additionalProperties"), dict):
                _valida(x, no["additionalProperties"], f"{caminho}.{k}" if caminho else k, erros)
    for sub in no.get("allOf", []):
        _valida(v, sub, caminho, erros)
    if "if" in no:
        teste = []
        _valida(v, no["if"], caminho, teste)
        if not teste and "then" in no:
            _valida(v, no["then"], caminho, erros)


def validar(perfil) -> list[str]:
    """Lista de erros (texto em português); [] = válido."""
    erros = []
    _valida(perfil, SCHEMA, "", erros)
    return erros
