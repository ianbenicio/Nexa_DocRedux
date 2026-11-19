-- Story 3.3 — RLS e Storage por SQL, com dois usuários A e B (spec §6 "Testes de RLS obrigatórios").
-- O mesmo roteiro pela API REST, RPC e Storage está em rest_rls.py.
begin;
\ir helpers.psql
select no_plan();

-- --------------------------------------------------------------------------- dados (como postgres)
create temp table ids (nome text primary key, id uuid) on commit drop;
insert into ids values ('A', tests.novo_usuario('a@teste.local')), ('B', tests.novo_usuario('b@teste.local'));
select set_config('t.a', (select id::text from ids where nome = 'A'), true);
select set_config('t.b', (select id::text from ids where nome = 'B'), true);

-- A: um pronto (entrega atual v2, v1 antiga), um em_revisao com entrega, um esperando upload
select set_config('t.a_pronto', tests.novo_documento(current_setting('t.a')::uuid, 'pronto')::text, true);
select tests.nova_entrega(current_setting('t.a_pronto')::uuid, 1, false);
select tests.nova_entrega(current_setting('t.a_pronto')::uuid, 2, true);
select set_config('t.a_rev', tests.novo_documento(current_setting('t.a')::uuid, 'em_revisao')::text, true);
select tests.nova_entrega(current_setting('t.a_rev')::uuid, 1, true, 'bloqueado');
select set_config('t.a_up', tests.novo_documento(current_setting('t.a')::uuid, 'aguardando_upload')::text, true);
insert into public.feedback_usuario (documento_id, versao, nota) values (current_setting('t.a_pronto')::uuid, 2, 5);

-- B: um pronto com entrega, feedback, problema, juiz, revisão, métrica, job; um esperando upload
select set_config('t.b_pronto', tests.novo_documento(current_setting('t.b')::uuid, 'pronto')::text, true);
select tests.nova_entrega(current_setting('t.b_pronto')::uuid);
select set_config('t.b_up', tests.novo_documento(current_setting('t.b')::uuid, 'aguardando_upload')::text, true);
insert into public.feedback_usuario (documento_id, versao, nota, comentario) values (current_setting('t.b_pronto')::uuid, 1, 2, 'segredo');
insert into public.problemas_marcados (documento_id, versao, pagina, tipo, descricao) values (current_setting('t.b_pronto')::uuid, 1, 1, 'erro', 'segredo');
insert into public.avaliacoes_juiz (documento_id, versao, modelo, veredito, nota_juiz, criterios, entrada)
values (current_setting('t.a_pronto')::uuid, 2, 'm', 'aprovado', 5, '{}', 'folha');
insert into public.revisoes_humanas (documento_id, revisor_id, motivo, veredito) values (current_setting('t.a_pronto')::uuid, current_setting('t.b')::uuid, 'amostra', 'aprovado');
insert into public.metricas_entrega (documento_id, usuario_id) values (current_setting('t.a_pronto')::uuid, current_setting('t.a')::uuid);
insert into public.jobs (documento_id, tipo) values (current_setting('t.a_pronto')::uuid, 'juiz');
insert into public.workers (worker_id, host) values ('w-teste', 'pc');
insert into public.perfis_template (id, dono, tipo, json) values
  ('privado-a', current_setting('t.a')::uuid, 'ficha', '{}'),
  ('privado-b', current_setting('t.b')::uuid, 'ficha', '{}');
insert into storage.objects (bucket_id, name, owner_id)
values ('originais', current_setting('t.b') || '/' || current_setting('t.b_up') || '/original.part00', current_setting('t.b')),
       ('saidas', current_setting('t.b') || '/' || current_setting('t.b_pronto') || '/v1/saida.pdf', null);

-- --------------------------------------------------------------------------- como A
select tests.como(current_setting('t.a')::uuid);

-- controle positivo (AC 5)
select is((select count(*)::int from public.documentos), 3, 'A lê os próprios 3 documentos');
select is((select saldo from public.perfis_usuario), 150, 'A lê o próprio saldo (só a própria linha)');
select is((select count(*)::int from public.creditos_ledger), 1, 'A lê o próprio extrato');
select results_eq($$ select documento_id::text, versao from public.entregas $$,
                  $$ values (current_setting('t.a_pronto'), 2) $$,
                  'A lê só a entrega atual do documento pronto');
select results_eq($$ select id from public.perfis_template order by id $$,
                  $$ values ('mathema-saeb'), ('mec-sase-relatorio'), ('privado-a') $$,
                  'A lê os perfis semente e o próprio, não o de B');
select is((select count(*)::int from public.feedback_usuario), 1, 'A lê o próprio feedback');

-- A não lê documento, entrega, extrato, feedback nem arquivo de B
select is_empty($$ select 1 from public.documentos where usuario_id = current_setting('t.b')::uuid $$, 'A não lê documento de B');
select is_empty($$ select 1 from public.entregas where documento_id = current_setting('t.b_pronto')::uuid $$, 'A não lê entrega de B');
select is_empty($$ select 1 from public.creditos_ledger where usuario_id = current_setting('t.b')::uuid $$, 'A não lê extrato de B');
select is_empty($$ select 1 from public.perfis_usuario where id = current_setting('t.b')::uuid $$, 'A não lê o perfil de B');
select is_empty($$ select 1 from public.feedback_usuario where documento_id = current_setting('t.b_pronto')::uuid $$, 'A não lê feedback de B');
select is_empty($$ select 1 from public.problemas_marcados where documento_id = current_setting('t.b_pronto')::uuid $$, 'A não lê problemas de B');
select is_empty($$ select 1 from storage.objects where bucket_id in ('originais', 'saidas') $$, 'A não lê arquivo de B (nem os próprios: só URL assinada)');

-- A não lê entrega de documento seu em em_revisao
select is_empty($$ select 1 from public.entregas where documento_id = current_setting('t.a_rev')::uuid $$,
                'A não lê a entrega do próprio documento em_revisao');

-- A não insere, altera nem apaga ledger
select throws_ok($$ insert into public.creditos_ledger (usuario_id, tipo, valor, saldo_apos)
                    values (current_setting('t.a')::uuid, 'recarga', 1000, 1150) $$, '42501', null, 'A não insere no ledger');
select throws_ok($$ update public.creditos_ledger set valor = 1000 $$, '42501', null, 'A não altera o ledger');
select throws_ok($$ delete from public.creditos_ledger $$, '42501', null, 'A não apaga o ledger');

-- A não altera o próprio saldo, papel, interno nem senha_provisoria (AC 4)
select throws_ok(format($$ update public.perfis_usuario set %I = %s where id = current_setting('t.a')::uuid $$, c, v),
                 '42501', null, 'A não altera o próprio ' || c)
  from (values ('saldo', '9999'), ('papel', '''admin'''), ('interno', 'true'), ('senha_provisoria', 'false')) x(c, v);
select throws_ok($$ insert into public.documentos (usuario_id, idempotency_key, nome_arquivo, tamanho_bytes)
                    values (current_setting('t.a')::uuid, 'k', 'x.pdf', 1) $$, '42501', null, 'A não cria documento direto');
select throws_ok($$ update public.documentos set status = 'pronto' $$, '42501', null, 'A não muda status de documento');
select throws_ok($$ insert into public.feedback_usuario (documento_id, versao, nota) values (current_setting('t.a_pronto')::uuid, 1, 5) $$,
                 '42501', null, 'A não grava feedback direto (só pela API)');

-- A não executa private.credito_*
select throws_ok(format('select private.%s', f), '42501', null, 'A não executa private.' || split_part(f, '(', 1))
  from unnest(array[
    format('credito_conceder(%L::uuid, 1000, ''recarga'', null, null)', current_setting('t.a')),
    format('credito_reservar(%L::uuid)', current_setting('t.a_up')),
    'credito_capturar(1)', 'credito_estornar(1, null, null)',
    format('credito_ajuste_exclusao(%L::uuid, null)', current_setting('t.a')),
    'invariantes_credito()']) f;

-- A não lê avaliacoes_juiz, revisoes_humanas, metricas_entrega, jobs, workers nem config (nem as views)
select throws_ok(format('select 1 from public.%I', t), '42501', null, 'A não lê ' || t)
  from unnest(array['avaliacoes_juiz', 'revisoes_humanas', 'metricas_entrega', 'jobs', 'workers', 'config',
                    'v_fila_backoffice', 'v_criterios_piloto']) t;

-- Storage: A não envia para a pasta de B; envia para a própria pasta só a parte de um documento seu em upload
select throws_ok(
  format($$ insert into storage.objects (bucket_id, name, owner_id) values ('originais', '%s/%s/original.part01', %L) $$,
         current_setting('t.b'), current_setting('t.b_up'), current_setting('t.a')),
  '42501', null, 'A não envia arquivo para a pasta de B');
select throws_ok(
  format($$ insert into storage.objects (bucket_id, name, owner_id) values ('originais', '%s/%s/original.part00', %L) $$,
         current_setting('t.a'), current_setting('t.b_up'), current_setting('t.a')),
  '42501', null, 'A não envia na própria pasta para documento de B');
select throws_ok(
  format($$ insert into storage.objects (bucket_id, name, owner_id) values ('originais', '%s/%s/original.part00', %L) $$,
         current_setting('t.a'), current_setting('t.a_pronto'), current_setting('t.a')),
  '42501', null, 'A não envia para documento que não espera upload');
select throws_ok(
  format($$ insert into storage.objects (bucket_id, name, owner_id) values ('originais', '%s/%s/outro.pdf', %L) $$,
         current_setting('t.a'), current_setting('t.a_up'), current_setting('t.a')),
  '42501', null, 'A não envia nome fora de original.partNN');
select throws_ok(
  format($$ insert into storage.objects (bucket_id, name, owner_id) values ('saidas', '%s/%s/v1/saida.pdf', %L) $$,
         current_setting('t.a'), current_setting('t.a_pronto'), current_setting('t.a')),
  '42501', null, 'A não escreve em saidas');
select lives_ok(
  format($$ insert into storage.objects (bucket_id, name, owner_id) values ('originais', '%s/%s/original.part00', %L) $$,
         current_setting('t.a'), current_setting('t.a_up'), current_setting('t.a')),
  'A envia a parte do próprio documento para a própria pasta');
select is((select count(*)::int from (select 1 from storage.objects) s), 0, 'A não vê nem o objeto que acabou de enviar');
-- DELETE direto: o gatilho storage.protect_delete do Supabase recusa para qualquer papel.
-- UPDATE sem política: afeta 0 linhas em vez de erro; conferido abaixo.
select throws_ok($$ delete from storage.objects $$, '42501', null, 'A não apaga arquivo');
update storage.objects set name = 'x';
reset role;
select is((select count(*)::int from storage.objects where bucket_id in ('originais', 'saidas') and name <> 'x'
                                                       and split_part(name, '/', 1) in (current_setting('t.a'), current_setting('t.b'))), 3,
          'UPDATE de A em storage.objects não alcança nenhuma linha');
select is((select saldo from public.perfis_usuario where id = current_setting('t.a')::uuid), 150, 'saldo de A intacto');

-- --------------------------------------------------------------------------- anon
select tests.como(null);
select throws_ok(format('select 1 from public.%I', t), '42501', null, 'anon não lê ' || t)
  from unnest(array['documentos', 'perfis_usuario', 'creditos_ledger', 'entregas', 'perfis_template']) t;
reset role;

-- Nenhuma política de escrita para authenticated/anon em public
select is_empty($$ select tablename, policyname from pg_policies
                    where schemaname = 'public' and cmd <> 'SELECT' $$,
                'nenhuma política de INSERT/UPDATE/DELETE em public');
select is_empty($$ select table_name, privilege_type from information_schema.role_table_grants
                    where table_schema = 'public' and grantee in ('anon', 'authenticated') and privilege_type <> 'SELECT' $$,
                'anon/authenticated sem grant de escrita em public');
select is_empty($$ select table_name from information_schema.role_table_grants
                    where table_schema = 'public' and grantee = 'anon' $$,
                'anon sem grant nenhum em public');
select results_eq($$ select id, public, file_size_limit from storage.buckets where id in ('originais', 'saidas') order by id $$,
                  $$ values ('originais', false, 52428800::bigint), ('saidas', false, 52428800::bigint) $$,
                  'buckets privados com limite de 50 MB');

select * from finish();
rollback;
