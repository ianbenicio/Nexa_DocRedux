-- Story 3.2 — ledger de créditos e funções do saldo (AC 1, 3–8). A concorrência (AC 2) está em
-- concorrencia.py, porque precisa de duas conexões.
begin;
\ir helpers.psql
select no_plan();

create temp table ids (nome text primary key, id uuid) on commit drop;
create temp table lanc (nome text primary key, id bigint) on commit drop;

-- --------------------------------------------------------------------------- AC 5: usuário novo
insert into ids values ('u', tests.novo_usuario('ledger@teste.local'));
select results_eq(
  $$ select saldo, senha_provisoria, email from public.perfis_usuario where id = (select id from ids where nome = 'u') $$,
  $$ values (150, true, 'ledger@teste.local') $$,
  'usuário novo: 150 créditos, senha provisória');
select results_eq(
  $$ select tipo::text, valor, saldo_apos from public.creditos_ledger where usuario_id = (select id from ids where nome = 'u') $$,
  $$ values ('concessao', 150, 150) $$,
  'um lançamento de concessão');

delete from public.config where chave = 'creditos_iniciais';
insert into ids values ('sem_cfg', tests.novo_usuario('semcfg@teste.local'));
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'sem_cfg')), 150,
          'sem a chave em config: 150');
insert into public.config (chave, valor) values ('creditos_iniciais', '"lixo"');
insert into ids values ('cfg_lixo', tests.novo_usuario('lixo@teste.local'));
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'cfg_lixo')), 150,
          'config inválido não derruba o convite: 150');
update public.config set valor = '20' where chave = 'creditos_iniciais';
insert into ids values ('cfg_20', tests.novo_usuario('vinte@teste.local'));
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'cfg_20')), 20,
          'com a chave em config: vale o valor dela');

-- --------------------------------------------------------------------------- AC 1: saldo insuficiente
insert into ids values ('d_caro', tests.novo_documento((select id from ids where nome = 'u'), 'aguardando_confirmacao', 151));
select throws_ok(
  $$ select private.credito_reservar((select id from ids where nome = 'd_caro')) $$,
  'P0001', 'saldo_insuficiente', 'reserva acima do saldo falha');
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'u')), 150, 'saldo intacto');
select is((select count(*)::int from public.creditos_ledger where usuario_id = (select id from ids where nome = 'u')), 1,
          'nada gravado no ledger');

insert into ids values ('d_sem_orc', tests.novo_documento((select id from ids where nome = 'u'), 'aguardando_confirmacao'));
select throws_ok($$ select private.credito_reservar((select id from ids where nome = 'd_sem_orc')) $$,
                 'P0001', 'orcamento_invalido', 'sem orçamento: falha');

-- --------------------------------------------------------------------------- reserva e AC 3: captura
insert into ids values ('d1', tests.novo_documento((select id from ids where nome = 'u'), 'aguardando_confirmacao', 100));
insert into lanc values ('r1', private.credito_reservar((select id from ids where nome = 'd1')));
select results_eq(
  $$ select u.saldo, d.creditos_reservados from public.perfis_usuario u, public.documentos d
      where u.id = (select id from ids where nome = 'u') and d.id = (select id from ids where nome = 'd1') $$,
  $$ values (50, 100) $$,
  'reserva: saldo cai na hora e o documento guarda o valor');
select throws_ok($$ select private.credito_reservar((select id from ids where nome = 'd1')) $$,
                 'P0001', 'reserva_aberta', 'segunda reserva no mesmo documento falha');

insert into lanc values ('c1', private.credito_capturar((select id from lanc where nome = 'r1')));
select is(private.credito_capturar((select id from lanc where nome = 'r1')), (select id from lanc where nome = 'c1'),
          'captura idempotente: devolve o mesmo lançamento');
select results_eq(
  $$ select count(*)::int, sum(valor)::int from public.creditos_ledger where reserva_id = (select id from lanc where nome = 'r1') $$,
  $$ values (1, 0) $$,
  'uma captura de valor 0');
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'u')), 50, 'captura não muda o saldo');
select throws_ok($$ select private.credito_estornar((select id from lanc where nome = 'r1')) $$,
                 'P0001', 'reserva_capturada', 'estorno depois de captura falha');

-- estorno
insert into ids values ('d2', tests.novo_documento((select id from ids where nome = 'u'), 'aguardando_confirmacao', 30));
insert into lanc values ('r2', private.credito_reservar((select id from ids where nome = 'd2')));
insert into lanc values ('e2', private.credito_estornar((select id from lanc where nome = 'r2'), 'falha final'));
select is(private.credito_estornar((select id from lanc where nome = 'r2'), 'de novo'), (select id from lanc where nome = 'e2'),
          'estorno idempotente');
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'u')), 50,
          'estorno devolve o valor uma vez só');
select throws_ok($$ select private.credito_capturar((select id from lanc where nome = 'r2')) $$,
                 'P0001', 'reserva_estornada', 'captura depois de estorno falha');
select throws_ok($$ select private.credito_capturar((select id from lanc where nome = 'c1')) $$,
                 'P0001', 'reserva_inexistente', 'captura de lançamento que não é reserva falha');

-- reprocessar de `falhou` (§5): nova reserva do mesmo orçamento, feita pelo admin
insert into lanc values ('r2b', private.credito_reservar((select id from ids where nome = 'd2'),
                                                         'reprocessamento de documento que falhou',
                                                         (select id from ids where nome = 'cfg_20')));
select results_eq(
  $$ select motivo, criado_por from public.creditos_ledger where id = (select id from lanc where nome = 'r2b') $$,
  $$ values ('reprocessamento de documento que falhou', (select id from ids where nome = 'cfg_20')) $$,
  'reserva do reprocessamento guarda motivo e admin');
select lives_ok($$ select private.credito_estornar((select id from lanc where nome = 'r2b')) $$, 'estorno da nova reserva');

-- recarga
select lives_ok($$ select private.credito_conceder((select id from ids where nome = 'u'), 25, 'recarga', 'teste', null) $$,
                'recarga');
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'u')), 75, 'recarga soma');
select throws_ok($$ select private.credito_conceder((select id from ids where nome = 'u'), 5, 'estorno', null, null) $$,
                 'P0001', 'tipo_invalido', 'conceder só concessão ou recarga');
select throws_ok($$ select private.credito_conceder((select id from ids where nome = 'u'), 0, 'recarga', null, null) $$,
                 'P0001', 'valor_invalido', 'valor positivo');

-- --------------------------------------------------------------------------- AC 4: invariantes
update public.documentos set status = 'pronto' where id = (select id from ids where nome = 'd1');
update public.documentos set status = 'falhou' where id = (select id from ids where nome = 'd2');
select is_empty($$ select * from private.invariantes_credito() $$, 'invariantes ok depois de captura e estornos');
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'u')),
          (select sum(valor)::int from public.creditos_ledger where usuario_id = (select id from ids where nome = 'u')),
          'saldo = soma do ledger');

insert into ids values ('d3', tests.novo_documento((select id from ids where nome = 'u'), 'aguardando_confirmacao', 10));
insert into lanc values ('r3', private.credito_reservar((select id from ids where nome = 'd3')));
update public.documentos set status = 'na_fila' where id = (select id from ids where nome = 'd3');
select is_empty($$ select * from private.invariantes_credito() $$, 'reserva aberta em na_fila é válida');
update public.documentos set status = 'cancelado' where id = (select id from ids where nome = 'd3');
select results_eq(
  $$ select documento_id, problema like 'reserva % aberta em documento cancelado' from private.invariantes_credito() $$,
  $$ values ((select id from ids where nome = 'd3'), true) $$,
  'cancelado com reserva aberta é violação');

-- --------------------------------------------------------------------------- AC 6: ajuste da exclusão
select throws_ok($$ select private.credito_ajuste_exclusao((select id from ids where nome = 'u')) $$,
                 'P0001', 'conta_nao_excluida', 'ajuste exige a conta marcada');
update public.perfis_usuario set exclusao_conta_em = now() where id = (select id from ids where nome = 'u');
select throws_ok($$ select private.credito_ajuste_exclusao((select id from ids where nome = 'u')) $$,
                 'P0001', 'reserva_aberta', 'ajuste com reserva aberta falha');
select lives_ok($$ select private.credito_estornar((select id from lanc where nome = 'r3'), 'cancelado') $$, 'estorna a aberta');
select set_config('tests.n_antes', (select count(*)::text from public.creditos_ledger where usuario_id = (select id from ids where nome = 'u')), true);
select isnt(private.credito_ajuste_exclusao((select id from ids where nome = 'u')), null, 'ajuste lançado');
select results_eq(
  $$ select saldo from public.perfis_usuario where id = (select id from ids where nome = 'u') $$,
  $$ values (0) $$, 'saldo zerado');
select is((select count(*)::int from public.creditos_ledger where usuario_id = (select id from ids where nome = 'u')),
          current_setting('tests.n_antes')::int + 1, 'nenhum lançamento apagado; um ajuste a mais');
select is((select valor from public.creditos_ledger where usuario_id = (select id from ids where nome = 'u') and tipo = 'ajuste'),
          -75, 'ajuste = −saldo');
select is(private.credito_ajuste_exclusao((select id from ids where nome = 'u')), null, 'ajuste com saldo zero não lança nada');
select throws_ok($$ select private.credito_conceder((select id from ids where nome = 'u'), 10, 'recarga', null, null) $$,
                 'P0001', 'conta_excluida', 'conta excluída não recebe crédito');
select is_empty($$ select * from private.invariantes_credito() $$, 'invariantes ok depois do ajuste');

-- --------------------------------------------------------------------------- AC 8: append-only
select throws_ok($$ update public.creditos_ledger set valor = 999 $$, 'P0001', 'ledger_append_only', 'UPDATE no ledger falha');
select throws_ok($$ delete from public.creditos_ledger $$, 'P0001', 'ledger_append_only', 'DELETE no ledger falha');
select throws_ok($$ truncate public.creditos_ledger cascade $$, 'P0001', 'ledger_append_only', 'TRUNCATE no ledger falha');
select is((select count(*)::int from pg_trigger where tgrelid = 'public.creditos_ledger'::regclass
             and tgname like 'creditos_ledger_%' and tgenabled = 'O'), 2, 'gatilhos append-only ligados');
select is_empty(
  $$ select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
      where n.nspname in ('public', 'private') and p.prosrc ~* 'disable\s+trigger' $$,
  'nenhuma função desliga gatilho');

-- --------------------------------------------------------------------------- AC 7: quem executa
select ok(not has_function_privilege(r, f, 'EXECUTE'), r || ' não executa ' || f)
  from unnest(array['anon', 'authenticated']) r,
       unnest(array['private.credito_conceder(uuid,integer,public.tipo_lancamento,text,uuid)',
                    'private.credito_reservar(uuid,text,uuid)', 'private.credito_capturar(bigint)',
                    'private.credito_estornar(bigint,text,uuid)', 'private.credito_ajuste_exclusao(uuid,uuid)',
                    'private._lancar(uuid,public.tipo_lancamento,integer,uuid,bigint,text,uuid)']) f;
select ok(not has_schema_privilege(r, 'private', 'USAGE'), r || ' sem USAGE em private')
  from unnest(array['anon', 'authenticated']) r;
select ok(has_function_privilege('docredux_backend', f, 'EXECUTE'), 'docredux_backend executa ' || f)
  from unnest(array['private.credito_conceder(uuid,integer,public.tipo_lancamento,text,uuid)',
                    'private.credito_reservar(uuid,text,uuid)', 'private.credito_capturar(bigint)',
                    'private.credito_estornar(bigint,text,uuid)', 'private.credito_ajuste_exclusao(uuid,uuid)',
                    'private.invariantes_credito()']) f;
select ok(not has_function_privilege('docredux_backend', 'private._lancar(uuid,public.tipo_lancamento,integer,uuid,bigint,text,uuid)', 'EXECUTE'),
          'docredux_backend não chama _lancar direto');

-- Execução real como docredux_backend (com o papel do backend, o UPDATE no ledger é negado por grant).
select set_config('tests.cfg20', (select id::text from ids where nome = 'cfg_20'), true);
grant docredux_backend to current_user;  -- só nesta transação de teste
set local role docredux_backend;
select lives_ok($$ select private.credito_conceder(current_setting('tests.cfg20')::uuid, 5, 'recarga', 'backend', null) $$,
                'docredux_backend executa a recarga');
select throws_ok($$ update public.creditos_ledger set motivo = 'x' $$, '42501', null, 'backend: UPDATE no ledger negado');
select throws_ok($$ update public.perfis_usuario set saldo = 999 $$, '42501', null, 'backend: UPDATE em saldo negado');
reset role;
select is((select saldo from public.perfis_usuario where id = (select id from ids where nome = 'cfg_20')), 25, 'recarga do backend gravada');

select * from finish();
rollback;
