# Validação @po (Pax): stories 2.1–2.5 — 2026-11-18

Checklist de 10 pontos (`story-lifecycle`): 1 título claro · 2 descrição completa · 3 AC testáveis · 4 escopo (dentro/fora) · 5 dependências · 6 estimativa/complexidade · 7 valor de negócio · 8 riscos · 9 critério de pronto · 10 alinhamento com spec/epic.

| Story | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | Nota | Decisão |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2.1 Repositório | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (repo público) | ✓ | ✓ §3, Q1 | 10 | GO |
| 2.2 Pacote | ✓ | ✓ | ✓ | ✓ | ✓ 2.1 | ✓ | ✓ | ✓ (Carlito, LO 26.8 no Debian) | ✓ | ✓ §4, DT-11, Q13 | 10 | GO |
| 2.3 Contrato | ✓ | ✓ | ✓ | ✓ | ✓ 2.2 | ✓ | ✓ | ~ (jsonschema ausente: decisão do @dev) | ✓ | ✓ §4, Q3, Q4 | 9 | GO |
| 2.4 Síntese | ✓ | ✓ | ✓ | ✓ | ✓ 2.3 | ✓ | ✓ | ~ (fração ±1 pt no documento inteiro ainda não medida) | ✓ | ✓ §4, Q4 | 9 | GO |
| 2.5 Capa/elementos | ✓ | ✓ | ✓ | ✓ | ✓ 2.3 | ✓ | ✓ | ✓ | ✓ | ✓ §4 U-C | 10 | GO |

**Observações:**
- 2.3 AC 2 amplia o yaml com `excede_paginas`/`excede_tamanho` e `tipo="desconhecido"`: está no contrato do spec §4 (tipo `Triagem`), não é invenção.
- 2.4: a página de origem por trecho exige que o modelo do relatório guarde a página de cada elemento. Isso não pode mudar a saída da regressão (o render ignora o campo).
- Nenhuma story faz push ou deploy.

Status das 5 stories: Draft → **Ready**.
