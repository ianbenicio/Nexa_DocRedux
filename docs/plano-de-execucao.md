# Plano de execução — DocRedux / plataforma de testes do piloto

**Data:** 2026-11-18 · **Sessão:** 5cd134fd-7c71-4926-8dc3-5ea75406686b · **Projeto:** DocRedux · **Branch/commit:** sem git (a pasta não é repositório; a unidade 2 propõe `git init`)

## 0. Sessão concorrente

Nenhuma. `sessao.js --lista 5` não encontrou transcripts para a pasta do DocRedux; esta sessão roda a partir de `H:\Nexa`. `git status`: não se aplica (não é repositório).

## 1. Enquadramento

**Objetivo:** construir a plataforma web de testes e rodar o piloto (10 usuários, 4 semanas), avaliando 100% das entregas.

**Restrições:**
- Margem pela tabela de P9; no piloto, os créditos são grátis.
- `git push` e deploy são exclusivos do @devops.
- Não usar `mcp__claude-in-chrome__*`.
- Dados de alunos podem aparecer nos documentos (LGPD).
- O pipeline validado nos ex. 01–05 não pode regredir.

**Perfil do projeto:**
- Domínio: SaaS de documentos (educação).
- Stack: Python (PyMuPDF, python-docx, LibreOffice) + FastAPI + Supabase + Next.js.
- Integrações: Supabase (auth, Postgres, Storage), API Anthropic (juiz Opus).
- UI: sim. Dados sensíveis: documentos de usuários, possivelmente com dados de alunos.
- Fase AIOX: greenfield da plataforma sobre um motor validado. Complexidade: STANDARD (escopo 4, integração 3, infra 3, conhecimento 2, risco 3 = 15).

**Dúvidas:**

| Dúvida | Resolvida? | Resposta / quando resolver |
|---|---|---|
| Tamanho e duração do piloto | sim | 10 usuários, 4 semanas (PL1) |
| Quem revisa | sim | O responsável (PL2) |
| Pagamento no piloto | sim | Créditos grátis (PL3) |
| Stack | sim | FastAPI + Supabase + Next.js (PL4) |
| Onde fica o repositório (git init na pasta DocRedux ou repositório novo) | sim (Q1, 2026-11-18) | `https://github.com/ianbenicio/Nexa_DocRedux.git`; push só pelo @devops |
| VPS (Hetzner CPX31, como em `margem.md`) e domínio | sim (Q2, Q18, 2026-11-18) | Sem VPS nem domínio na fase de testes: infraestrutura gratuita (spec DT-17): Supabase Free em sa-east-1, web e API na Vercel Hobby, worker no PC do responsável ou na Oracle Always Free A1 em São Paulo. **Em 2026-11-19:** domínio `.com.br` + Resend grátis para os e-mails "pronto" e "em revisão" (Q8 = sim); o login segue por senha do admin (DT-19) |
| Recrutamento dos 10 participantes | aberta | Responsável, em paralelo à construção |
| Demais lacunas da spec (Q3–Q18) | sim (2026-11-18) | Respostas e efeitos no §18 de `docs/spec/spec.md` (v1.3); só restam pendências de execução da unidade 7 |

## 2. Precedentes (Engram)

**Consultas:**

| Texto da consulta | Resultados | Relevância (mín–máx) |
|---|---|---|
| piloto MVP SaaS web upload de documento processamento assíncrono fila Python FastAPI Supabase testes com usuários avaliação feedback | 7 | 0,509–0,533 |
| deploy Hetzner VPS fila de jobs worker LibreOffice PDF conversão autenticação créditos Pix Asaas | 7 | 0,493–0,500 |

**Usados:**

| Nota (id) | O que diz | Força (D10) | Pré-condição vale aqui? | Efeito no plano |
|---|---|---|---|---|
| `fezinha-stack-e-deploy` | Monorepo `engine/ api/ web/ db/` com FastAPI + Supabase + Next; backend no Railway e front na Vercel | relatada | parcial: a Fezinha não precisa de LibreOffice nem de processamento pesado (conferido pelo tldr e pelo trecho) | Mesmo monorepo e stack; o backend e o worker vão para VPS (Hetzner), não Railway |
| `adr-001-async-processing-fastapi-backgroundtasks` | Upload + Haiku em 3–8 s com BackgroundTasks | registrada | não: aqui os jobs levam 15–60 s+ (ex. 05: extração 39 s), usam LibreOffice e precisam sobreviver a reinício | Fila no Postgres (`FOR UPDATE SKIP LOCKED`) + worker separado, em vez de BackgroundTasks |
| `01JVSDD4M3P5Q6R8STUVWXYZB1` (FU-001) | Falhas de upload no Supabase Storage sem tratamento na spec | registrada (hipótese) | sim: mesmo Storage, com PDFs de até 80 MB | Upload idempotente, status `upload_falhou`, nova tentativa; critério na unidade 4 |
| `despensa-auditoria-de-c-digo-riscos-e-usabilidade-2026-07-31` | RLS permitia ao usuário se autoverificar (crítico) | relatada | sim: RLS por conta + créditos são o mesmo risco | Saldo e ledger escritos só pela service role; teste de RLS obrigatório na unidade 3 |
| `python-docx-para-documento-compacto-truques-de-render-e-veri` | DOCX → PDF via LibreOffice headless; truques do render | verificável (ex. 01–04) | sim: mesmo motor | O worker precisa de LibreOffice na imagem; regressão contra ex. 01–05 |

**Fase 0 da unidade 3 (Banco e RLS), 2026-11-19:**

| Texto da consulta | Resultados | Relevância (mín–máx) |
|---|---|---|
| Supabase Postgres migrations RLS pgTAP testes dois usuários ledger de créditos saldo SECURITY DEFINER schema private fila SKIP LOCKED buckets políticas | 7 | 0,521–0,533 |
| supabase db reset CLI docker migrations seed trigger auth.users "Database error saving new user" saldo negativo concorrência gotcha | 7 | 0,508–0,525 |

| Nota (id) | O que diz | Força (D10) | Pré-condição vale aqui? | Efeito no plano |
|---|---|---|---|---|
| `classmate-connect-13-fix-de-performance-rls-initplan-multipl` | `auth.uid()` cru na política é reavaliado por linha; políticas permissivas duplicadas pesam | verificável (commit e advisors do Supabase) | sim: mesmo Postgres do Supabase com RLS por dono | Toda política usa `(select auth.uid())`; uma política de SELECT por tabela |
| `despensa-disable-trigger-na-exclus-o-de-conta-abre-janela-gl` | Exclusão de conta que desliga o gatilho append-only abre janela global de escrita | relatada | sim: o ledger do DocRedux é append-only e a exclusão da conta mexe nele | O `ajuste` da exclusão é um INSERT; nenhuma função desliga gatilho; teste prova que UPDATE/DELETE no ledger falha até para o backend |
| `despensa-onda-de-corre-es-da-auditoria-migrations-0005-0009` | 42 testes de RLS/schema verificados contra Postgres real | verificável | sim | Testes rodam no Postgres do Supabase local (`supabase start`), não em mock |
| `despensa-auditoria-*` (já usado na unidade 1) | RLS deixou o usuário alterar coluna de autoridade | relatada | sim | `saldo`, `papel`, `interno` e `senha_provisoria` sem UPDATE para `authenticated`: sem política e sem grant |

Descartados nesta consulta: `classmate-connect-13-reparo-de-historico-*` e `runbook-de-deploy-*` (servem à unidade 7, push de migrations); `fetch-resiliente` (front); `FU-001` (upload, unidade 4/5); `docredux-spec-*` (é a própria spec).

**Descartados:**
- `classmate-connect-13-*`: TanStack/SPA na Vercel, stack diferente.
- `spec-v1-notas-whatsapp` e `spec-v1-critique-qa-review-qa`: domínio diferente; servem só como exemplo de formato do Spec Pipeline.
- `fezinha-handoff-*` e `fezinha-entregas-codex-*`: estado de outro projeto.
- `re-verificacao-*` e `auditoria-est-tica-de-pacote-npm-*`: sem relação.

## 3. Plano preliminar

**Workflow AIOX:** Spec Pipeline (STANDARD) → SDC por story. Motivo: plataforma nova com integrações e infraestrutura; o motor já está validado, então a spec foca a plataforma.

**Unidades de sessão (D9):**

| # | Unidade | Fase / executor | Entrega | Critério de aceitação preliminar | Limite de contexto |
|---|---|---|---|---|---|
| 1 ✅ | Spec da plataforma de testes — **concluída em 2026-11-18** | Spec Pipeline / @pm + @architect + @qa | `docs/spec/spec.md` + critique | Critique ≥ 4,0; cada requisito rastreia para `saas/piloto.md` ou `processos.md` | ~180k |
| 2 ✅ | Motor como biblioteca — **concluída em 2026-11-18** | SDC / @dev | `engine/` com `processar(pdf, opcoes) → saida + relatorio`; `git init` + monorepo | Ex. 01–05 reproduzem as saídas aprovadas (cobertura e páginas iguais) | ~180k |
| 3 ✅ | Banco e RLS — **concluída em 2026-11-19** | SDC / @data-engineer | `db/` com migrations: perfis, documentos, jobs, ledger de créditos, avaliações, feedback; buckets | Teste de RLS: usuário não lê documento alheio nem altera saldo | ~180k |
| 4 | API + worker | SDC / @dev | `api/` FastAPI + worker com fila (SKIP LOCKED), estimativa de créditos, reserva e estorno | Upload → job → PDF/DOCX ponta a ponta em dev; falha estorna crédito | ~180k |
| 5 | Front do usuário | SDC / @dev (+ impeccable sob demanda) | `web/` Next.js: login, upload + opções + custo, status, visualizador/impressão, nota e "marcar problema", histórico | Fluxo completo no browser embutido, no desktop e no celular | ~180k |
| 6 | Juiz Opus + backoffice | SDC / @dev | Juiz automático por entrega; telas de fila, revisão, recarga e reprocessamento | Juiz roda em 100% das entregas; custo por entrega registrado | ~180k |
| 7 | Deploy | @devops | Infraestrutura gratuita (DT-17): Supabase Free sa-east-1; web e API na Vercel Hobby; worker (LibreOffice) no PC ou na Oracle A1 São Paulo; termo do piloto | Health check verde; segredos fora do repositório; sem backups (Q16) | ~120k |
| 8 | QA + dry-run | SDC / @qa | Gate de QA; 2 usuários internos processam 10 documentos | Gate PASS; nenhum bloqueador aberto | ~150k |

**Unidade 1 — resultado (2026-11-18):**
- Entregas em `docs/spec/`:
  - [`requirements.json`](spec/requirements.json): FR-1..21, NFR-1..9, CON-1..9, cada um com rastreio para `piloto.md`/`processos.md`.
  - [`complexity.json`](spec/complexity.json): STANDARD, 15; research pulada, sem lacuna que bloqueie.
  - [`spec.md`](spec/spec.md): v1.3 (respostas Q1–Q18 aplicadas); v1.4 na unidade 3 (DT-21 e §6 sem FK para `auth.users`).
  - [`critique.json`](spec/critique.json): rodada 1 deu 3,8 (NEEDS_REVISION); rodada 2 deu **4,4, APPROVED**. As condições C-21 a C-25 foram tratadas pelo autor na v1.2, e as respostas entraram na v1.3, ambas sem nova critique; o @qa confere no gate das stories.
  - [`implementation.yaml`](spec/implementation.yaml): 26 stories para as unidades 2 a 8 (specVersion 1.3), prontas para o `@sm *draft`, sem bloqueio por pergunta.
- Decisões: Q1–Q18 respondidas em 2026-11-18 (spec §18). As que mudaram o desenho:
  - Infraestrutura sem custo fixo (Q2, Q10, Q14, Q18 → DT-17, CON-10): Supabase Free sa-east-1, Vercel Hobby, worker no PC ou na Oracle A1. O Free limita objetos a 50 MB, então o original sobe em partes de 42 MB (DT-18); o teto de Storage é 900 MB (DT-20).
  - Login com e-mail e senha criada pelo admin, troca obrigatória no primeiro acesso, sem SMTP (Q8, Q12 → DT-19).
  - Template novo é recusado na triagem e vira pedido de perfil no backoffice (Q3); o prazo de 2 meses não cabia na retenção de 30 dias.
  - Exclusão da conta, além dos documentos (Q7 → U7, `DELETE /me`).
  - Tabela P9 de `saas/processos.md` corrigida para a v3; síntese arredondada para cima (Q9).
- Pendências só de execução (unidade 7): capacidade da Oracle A1 em São Paulo e verificação por cartão no cadastro; limites do pooler no Free; limites das funções Python na Vercel Hobby.

- Desvio do plano: a fila no Postgres também leva a triagem, além de processar e juiz. O papel do banco é `docredux_backend`, e o worker roda com Carlito no lugar da Calibri (DT-11).

**Unidade 2 — resultado (2026-11-18):**
- Repositório `Nexa_DocRedux/` com os commits 22f7449 (2.1), 1025114 + cea982d (2.2/2.3), 8ea95d1 (2.4) e 838c490 (2.5), mais os gates QA. Tudo local; push só pelo @devops.
- Motor: `engine/docredux_engine` (contrato `triar`/`processar`/`folha_contato`/`validar_perfil`, CLI JSON). Regressão ex. 01–05 com Carlito e LibreOffice 26.8.3.1: 52→19, 34→14, 16→5, 26→12, 294→142, cobertura 99,96/100/100/100/99,97, igual ao baseline do `LOG.md`.
- Síntese extrativa (2.4): fração real a ≤ 0,15 ponto do alvo, ~1,2 s de CPU. Opção `manter_capa_elementos` (2.5) mapeada em `REGRAS.md`.
- `pytest`: 34 passed, 1 skipped. Gates em `docs/qa/gates/`: 2.1, 2.3 e 2.5 PASS; 2.2 PASS (container fechado em 2026-11-19); 2.4 CONCERNS (contagem de palavras do preço).
- Container Linux (`docredux-engine`, Python 3.12.16, LibreOffice 26.8.3.1): regressão 5/5 idêntica ao Windows (2026-11-19). Se o Docker não subir com `ERROR_NO_SYSTEM_RESOURCES`, é falta de RAM para a VM do WSL; liberar memória ou limitar com `.wslconfig`.
- ~~Ponto aberto para a unidade 3/4:** qual contagem de palavras vale para o preço da síntese. A triagem conta 93.166 e o verify 91.037; isso dá 56/75/94 contra 55/73/92.~~ **Decidido em 2026-11-19:** o preço da síntese usa a **contagem do verify** (91.037 no ex. 05 → 55/73/92). A triagem precisa usar a mesma função de contagem do verify, ou chamá-la direto.

**Unidade 3 — plano (Fase 0, 2026-11-19):**
- Sessão concorrente: nenhuma escrevendo no repositório. As sessões ligadas (spec, ex02–ex05) estão ociosas; `git status` limpo em `f787a54`.
- Workflow: SDC por story (3.1 → 3.2 → 3.3, em sequência, porque cada uma depende da anterior). @sm *draft → @po *validate → @data-engineer → @qa *qa-gate.
- Ambiente de teste: Supabase CLI 3.2 via `npx supabase` + Docker Desktop (imagens do Supabase já em cache). `supabase db reset` aplica migrations e seed; pgTAP por `supabase test db`; o teste REST usa o PostgREST e o GoTrue locais com o JWT de dois usuários criados pela API de admin local.
- Verificações: `supabase db reset` sem erro; `supabase test db` verde (schema/views, ledger, RLS); script REST/RPC verde.
- Riscos: Docker sem RAM para a pilha do Supabase (liberar memória ou subir só o necessário com `-x`); diferença entre o Postgres local e o do projeto Free (conferida na unidade 7, que roda os testes de RLS no banco de produção vazio).
- Decisão a registrar: contagem de palavras do preço da síntese (spec, DT nova).

**Unidade 3 — resultado (2026-11-19):**
- Commits d0ba4b6 (3.1), 3522ba2 (3.2) e 6e1568a (3.3), mais o commit de docs com spec, stories e gates. Tudo local; push só pelo @devops.
- `db/`: 5 migrations (schema com 13 tabelas e RLS em todas; papel `docredux_backend` e grants; views do backoffice e dos critérios; ledger e funções do saldo em `private`; RLS do usuário e buckets `originais`/`saidas`), `seed.sql` gerado dos perfis do motor, README com como rodar.
- Verificação: `supabase db reset` 3× sem erro; `supabase test db` 207 asserções PASS (schema 81, ledger 69, RLS 57); `concorrencia.py` 9/9 (a segunda reserva espera o lock e falha; saldo 50); `rest_rls.py` 25/25 pela API real: A não lê documento, entrega, extrato nem arquivo de B, não altera o próprio saldo e não alcança `private.*` nem por RPC. Auditoria de privilégios no banco limpa.
- Gates em `docs/qa/gates/`: 3.1, 3.2 e 3.3 PASS. CodeRabbit não executado; revisão manual mais auditoria.
- Decisões no spec v1.4: **DT-21** registra a decisão do responsável de 2026-11-19 (preço da síntese = contagem do verify; ex. 05 = 91.037 → 55/73/92, totais 89/107/126). **Sem FK para `auth.users`**: o id do perfil vira pseudônimo quando o usuário sai do Auth (§14).
- Ambiente: Supabase local nas portas 553xx, ao lado de outras pilhas locais nas 543xx; `npx supabase stop` em `db/` libera a RAM. O `postgres` do Supabase não é superusuário: o teste de concorrência assume `docredux_backend` via `supabase_admin`, só no container local.
- Para a unidade 4: a triagem do motor passa a contar palavras com a função do verify (DT-21; hoje usa `get_text("words")` em `api.py`), e o teste do ex. 05 confere 91.037; a API chama `private.credito_*` como `docredux_backend` (senha definida na unidade 7); os testes de script deixam usuários no banco local (ledger append-only), e `db reset` limpa.

**Capacidades por fase** (fonte: `H:\King\Harness\docs\catalogo\mapa-fases.md`):

| Fase | Ativo | Sob demanda | Não carregar | Como ligar o que falta |
|---|---|---|---|---|
| Spec (1) | agentes AIOX, Engram, AskUserQuestion | WebSearch (docs Supabase/FastAPI) | gstack de planejamento, GSD, superpowers (D6: AIOX é a espinha) | nada |
| Implementar (2–6) | nativas, Supabase MCP, browser embutido + preview | impeccable (unidade 5), `claude-api` (unidade 6, juiz) | plugins de outras stacks | `/impeccable` quando a UI começar |
| QA (8) | browser embutido, `/code-review` | gstack `review` | — | — |
| Deploy (7) | aiox-devops, `gh` | — | — | só o @devops |
| Registro | Engram (save), handoff, `sessao.js` | — | — | — |

**Verificações:**

| Entrega | Como provar |
|---|---|
| Motor (2) | Script de regressão: ex. 01–05 → mesmas páginas e cobertura ≥ a registrada no `LOG.md` |
| RLS (3) | Testes SQL com dois usuários (leitura cruzada e escrita de saldo devem falhar) |
| API/worker (4) | Teste ponta a ponta com um PDF de ficha e o relatório do ex. 05; queda do worker no meio → job retomado |
| Front (5) | Roteiro no browser embutido + screenshots em 375 px e desktop |
| Juiz (6) | 10 entregas com defeito plantado → juiz aponta ≥ 9 |
| Deploy (7) | `curl /health`; upload real em produção |
| Dry-run (8) | 10 documentos, 0 bloqueadores, métricas de P12 gravadas |

**Riscos:**
- Jobs longos derrubando a API — origem: `adr-001-*` (pré-condição não vale) — mitigação: worker separado com fila no Postgres e timeout por job.
- Escalada de privilégio via RLS (saldo, papel de admin) — origem: `despensa-auditoria-*` — mitigação: ledger só via service role; teste de RLS na unidade 3.
- Upload grande falhando sem estado — origem: FU-001 — mitigação: upload idempotente, status de erro e nova tentativa.
- Template novo travando o piloto (fila manual) — origem: enquadramento — mitigação: SLA avisado ao usuário; semear perfis antes com documentos dos participantes.
- Regressão do motor ao virar biblioteca — origem: enquadramento — mitigação: regressão dos ex. 01–05 antes de cada merge.
- Dados de alunos em documentos enviados à API Anthropic — origem: P11 — mitigação: termo do piloto; retenção de 30 dias; o juiz recebe só o necessário.

## 4. Custo da Fase 0

Saída de `node ~/.claude/skills/skill-budget/scripts/sessao.js` (sessão inteira, que também incluiu o desenho do SaaS e o teste da síntese):

| Chamadas | Contexto atual | Chamadas Engram | Resultados Engram no contexto |
|---|---|---|---|
| 261 na sessão (Fase 0: ~10) | médio 182k, máx. 267k, 4 compactações | Fase 0: 2 deep (+1 query e 2 saves antes) | 14 resultados (tldr + trecho), ~4k |

Unidade 3 (sessão 46209756, 2026-11-19, Fase 0 e implementação na mesma sessão): 80 chamadas registradas, contexto médio 169k, máx. 259k (23% acima de 200k), 1 compactação; Engram: 2 deep, ~3k de resultados no contexto; skill `fase-0` invocada 2× (a primeira barrada pelo guard de orçamento).

## 5. Aprovação

**Aprovado:** sim · **Por:** responsável · **Em:** 2026-11-18 · **Ajustes pedidos:** nenhum. Unidade 1 iniciada em sessão nova.

Unidade 3: plano da Fase 0 aprovado por delegação da sessão de origem (que abriu esta sessão com o escopo e os critérios de pronto), em 2026-11-19. A DT-21 só registra no spec a decisão que o responsável já tinha tomado.
