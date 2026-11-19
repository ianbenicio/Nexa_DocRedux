# Validação @po (Pax): stories 3.1–3.3 — 2026-11-19

Checklist de 10 pontos (`story-lifecycle`): 1 título claro · 2 descrição completa · 3 AC testáveis · 4 escopo (dentro/fora) · 5 dependências · 6 estimativa/complexidade · 7 valor de negócio · 8 riscos · 9 critério de pronto · 10 alinhamento com spec/epic.

| Story | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | Nota | Decisão |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 3.1 Schema | ✓ | ✓ | ✓ | ✓ | ✓ nenhuma | ~ (story grande: 13 tabelas + 2 views) | ✓ | ~ (preço do juiz nulo até a unidade 6) | ✓ | ✓ §6, §9, §12, §14 | 9 | GO |
| 3.2 Ledger | ✓ | ✓ | ✓ | ✓ | ✓ 3.1 | ✓ | ✓ | ✓ (concorrência testada com duas conexões reais) | ✓ | ✓ §5, §6, §14, DT-7 | 10 | GO |
| 3.3 RLS e Storage | ✓ | ✓ | ✓ | ✓ | ✓ 3.2 | ✓ | ✓ | ~ (Postgres local ≠ projeto Free; a unidade 7 repete os testes no banco de produção vazio) | ✓ | ✓ §6, §13, NFR-3/4 | 9 | GO |

**Observações:**
- 3.1 AC 4 interpreta o §14 itens 4–5: com uma FK para `auth.users`, mesmo sem `CASCADE`, o Auth não conseguiria remover o usuário. A story troca a FK por referências a `perfis_usuario(id)`, que vira o id pseudônimo. Não é feature nova: é a única forma de cumprir os dois itens juntos. O @data-engineer deve registrar isso no spec (§6).
- 3.1 AC 9: o §9 diz que o item sai da fila "quando há uma revisão humana posterior ao motivo, ou quando o problema é resolvido". A view aplica as duas regras como escritas.
- 3.2: os parâmetros opcionais de `credito_reservar` (motivo, admin) vêm da tabela de reprocessamento do §5 (`criado_por` = admin), não são invenção.
- 3.3 AC 2 aperta a política de upload (documento do usuário, `aguardando_upload`, nome `original.partNN`). Fica dentro do §6 ("INSERT só na pasta auth.uid()"), mais restrito. Aceito.
- Pendência de produto que esta unidade carrega: a contagem de palavras do preço da síntese (gate 2.4). Ela não muda o schema (`documentos.palavras int`), mas precisa de uma decisão registrada no spec antes da unidade 4.
- Nenhuma story faz push ou deploy.

Status das 3 stories: Draft → **Ready**.
