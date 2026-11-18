# Nexa DocRedux

Plataforma de testes do piloto do **DocRedux**: reduz e reestrutura documentos (fichas de estudante, relatórios) sem perder informação. O motor é determinístico (sem LLM); o LLM só entra no juiz de qualidade (unidade 6).

| Pasta | Conteúdo | Unidade |
|---|---|---|
| [`engine/`](engine/) | pacote Python `docredux_engine` (contrato do spec §4) | 2 |
| [`api/`](api/) | FastAPI + worker com fila no Postgres | 4, 6 |
| [`web/`](web/) | Next.js (usuário e `/admin`) | 5, 6 |
| [`db/`](db/) | migrations Supabase, seed, testes de RLS | 3 |
| [`deploy/`](deploy/) | Docker do worker, `vercel.json`, `.env.example` | 7 |
| [`docs/`](docs/) | spec, plano de execução, stories, gates de QA | — |

- Spec: [`docs/spec/spec.md`](docs/spec/spec.md) (v1.3) · Plano: [`docs/plano-de-execucao.md`](docs/plano-de-execucao.md) · Stories: [`docs/stories/`](docs/stories/).
- O acervo do método (`LOG.md`, `REGRAS.md`, `exemplos/`, `saas/`, `scripts/`) fica fora deste repositório, na pasta DocRedux do responsável.

## Regras do repositório

- **Público:** nenhum PDF, DOCX de cliente, `.env` ou chave entra no git (ver `.gitignore`). Os PDFs dos ex. 01–05 ficam num diretório apontado por `DOCREDUX_FIXTURES`.
- Commits locais em conventional commits com o id da story (`feat: ... [Story 2.2]`). `git push` e deploy só pelo @devops.
- Toda mudança em `engine/` roda a regressão dos ex. 01–05 antes do merge (NFR-6): `engine/tests/regressao.py`.
