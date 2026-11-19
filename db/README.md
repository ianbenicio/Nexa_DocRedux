# db/

Banco do DocRedux (Supabase/Postgres 17): schema, ledger de créditos, RLS e Storage. Unidade 3 (@data-engineer), spec §5, §6, §9, §14.

| Caminho | O quê |
|---|---|
| `supabase/migrations/20261119000100_schema.sql` | enums, 13 tabelas (RLS ligada em todas), índices, `config` com padrões |
| `supabase/migrations/20261119000110_backend_e_grants.sql` | revoga anon/authenticated; papel `docredux_backend` (BYPASSRLS, sem senha versionada) |
| `supabase/migrations/20261119000120_views.sql` | `config_num`, `v_fila_backoffice` (§9), `v_criterios_piloto` (§16) |
| `supabase/migrations/20261119000200_creditos.sql` | schema `private`: ledger append-only, reservar/capturar/estornar/ajuste, invariantes, gatilho de usuário novo |
| `supabase/migrations/20261119000300_rls_storage.sql` | políticas de SELECT do usuário; buckets `originais`/`saidas`; INSERT só na própria pasta |
| `seed.sql` | perfis semente, gerado por `scripts/gerar_seed.py` a partir de `engine/perfis/*.json` |
| `tests/*.sql` | pgTAP: `schema.sql` (3.1), `ledger.sql` (3.2), `rls.sql` (3.3); `helpers.psql` é incluído por eles |
| `tests/concorrencia.py` | duas sessões reais reservando ao mesmo tempo (3.2 AC 2) |
| `tests/rest_rls.py` | RLS pela API real: PostgREST, RPC, Storage, Auth (3.3) |

## Rodar local

Pré-requisitos: Docker Desktop, Node (para `npx`), Python 3 (só stdlib nos scripts).

```powershell
cd db
npx --yes supabase@3.2.0 start -x realtime,studio,edge-runtime,logflare,vector,imgproxy,mailpit,postgres-meta,supavisor
npx --yes supabase@3.2.0 db reset          # aplica migrations + seed
npx --yes supabase@3.2.0 test db tests     # pgTAP (207 testes)
py -3 tests/concorrencia.py
py -3 tests/rest_rls.py
py -3 scripts/gerar_seed.py --conferir     # seed.sql em dia com engine/perfis
npx --yes supabase@3.2.0 stop              # libera RAM
```

- Portas 553xx (API 55321, db 55322, shadow 55320): coexistem com outras pilhas Supabase locais nas portas padrão 543xx.
- `concorrencia.py` e `rest_rls.py` deixam usuários de teste (o ledger é append-only e não aceita DELETE). `db reset` limpa. Os testes pgTAP rodam em transação com rollback e passam com ou sem esse resíduo.
- As chaves locais vêm de `npx supabase status -o env` em tempo de execução. Nunca versionar chave, `.env` nem senha.

## Decisões que o código supõe

- **Sem FK para `auth.users`.** As tabelas referenciam `perfis_usuario(id)`. Remover o usuário no Auth (§14 item 4) deixa a linha pseudônima e o ledger intactos. O gatilho `private.novo_usuario` cria o perfil e concede `config.creditos_iniciais` (padrão 150).
- **Escrita só pelo backend.** `authenticated` tem apenas SELECT nas 7 tabelas que o §6 deixa o usuário ler, filtrado por `(select auth.uid())`. Nenhuma política de INSERT/UPDATE/DELETE em `public`. A API e o worker conectam como `docredux_backend` (a senha é definida no deploy, unidade 7).
- **Saldo só muda pelas funções de `private`**, na mesma transação do lançamento, com a linha do perfil travada (`FOR UPDATE`). Nem o `docredux_backend` tem UPDATE em `saldo` ou escrita no ledger.
- **Storage:** o usuário só faz INSERT em `originais/{seu uid}/{documento seu em aguardando_upload}/original.partNN`. Leitura de arquivos só por URL assinada emitida pela API. O DELETE direto em `storage.objects` é recusado pelo próprio Supabase (`storage.protect_delete`).
