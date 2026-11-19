-- Story 3.1 — schema, config, seed, views (AC 2–11).
begin;
\ir helpers.psql
set constraints all immediate;  -- a FK diferida de documentos.entrega_atual vale já no teste
select no_plan();

-- --------------------------------------------------------------------------- AC 2: tabelas e colunas
select has_table('public', t, 'tabela ' || t)
  from unnest(array['perfis_usuario', 'perfis_template', 'documentos', 'jobs', 'workers', 'creditos_ledger',
                    'entregas', 'avaliacoes_juiz', 'feedback_usuario', 'problemas_marcados',
                    'revisoes_humanas', 'metricas_entrega', 'config']) t;
select col_is_pk('public', 'entregas', array['documento_id', 'versao'], 'entregas: pk (documento_id, versao)');
select col_is_pk('public', 'feedback_usuario', array['documento_id', 'versao'], 'feedback: pk (documento_id, versao)');
select has_column('public', 'jobs', 'estado_origem', 'jobs.estado_origem');
select has_column('public', 'documentos', 'partes', 'documentos.partes');
select has_column('public', 'perfis_usuario', c, 'perfis_usuario.' || c)
  from unnest(array['interno', 'senha_provisoria', 'exclusao_conta_em']) c;
select col_default_is('public', 'perfis_usuario', 'senha_provisoria', 'true', 'senha_provisoria nasce true');
select has_column('public', 'metricas_entrega', c, 'metricas_entrega.' || c)
  from unnest(array['gate_primeira_passagem', 'cobertura_primeira_passagem', 'versoes', 'interno',
                    'juiz_custo_usd', 'motivos_intervencao', 'fracao_real']) c;
select is((select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity), 0,
          'RLS ligada em todas as tabelas de public');

-- --------------------------------------------------------------------------- AC 3: enums
select enum_has_labels('public', 'status_documento', array[
  'aguardando_upload', 'triagem', 'recusado', 'upload_falhou', 'aguardando_confirmacao', 'na_fila',
  'processando', 'pronto', 'em_revisao', 'falhou', 'cancelado', 'excluido', 'expirado'],
  'status do documento: os 13 estados do §8, sem aguardando_perfil');
select ok('template_novo' = any(enum_range(null::public.motivo_recusa)::text[]), 'motivo_recusa tem template_novo');
select ok('ajuste' = any(enum_range(null::public.tipo_lancamento)::text[]), 'tipo de lançamento tem ajuste');

-- --------------------------------------------------------------------------- AC 4: FKs e exclusão da conta
select is((select count(*)::int from pg_constraint
            where contype = 'f' and confrelid = 'auth.users'::regclass
              and connamespace = 'public'::regnamespace), 0,
          'nenhuma FK de public para auth.users');
select is((select count(*)::int from pg_constraint
            where contype = 'f' and connamespace = 'public'::regnamespace and confdeltype = 'c'), 0,
          'nenhuma FK de public com ON DELETE CASCADE');

create temp table ids (nome text primary key, id uuid) on commit drop;
insert into ids values ('saindo', tests.novo_usuario('saindo@teste.local'));
select lives_ok(
  $$ delete from auth.users where id = (select id from ids where nome = 'saindo') $$,
  'apagar o usuário do Auth não é bloqueado');
select is((select count(*)::int from public.perfis_usuario where id = (select id from ids where nome = 'saindo')), 1,
          'perfis_usuario fica com o id pseudônimo');
select is((select count(*)::int from public.creditos_ledger where usuario_id = (select id from ids where nome = 'saindo')), 1,
          'ledger fica');

-- --------------------------------------------------------------------------- AC 5: docredux_backend
select ok((select rolcanlogin and rolbypassrls and not rolsuper and not rolcreaterole and not rolcreatedb
             from pg_roles where rolname = 'docredux_backend'),
          'docredux_backend: LOGIN, BYPASSRLS, sem superusuário');
select ok(not has_table_privilege('docredux_backend', 'public.creditos_ledger', p), 'backend sem ' || p || ' no ledger')
  from unnest(array['INSERT', 'UPDATE', 'DELETE', 'TRUNCATE']) p;
select ok(not has_column_privilege('docredux_backend', 'public.perfis_usuario', c, 'UPDATE'),
          'backend sem UPDATE em perfis_usuario.' || c)
  from unnest(array['saldo', 'papel', 'interno', 'id']) c;
select ok(has_column_privilege('docredux_backend', 'public.perfis_usuario', c, 'UPDATE'),
          'backend com UPDATE em perfis_usuario.' || c)
  from unnest(array['termo_versao', 'termo_aceito_em', 'senha_provisoria', 'exclusao_conta_em', 'email']) c;
select ok(has_table_privilege('docredux_backend', 'public.' || t, 'INSERT'), 'backend insere em ' || t)
  from unnest(array['documentos', 'jobs', 'entregas', 'avaliacoes_juiz', 'feedback_usuario',
                    'problemas_marcados', 'revisoes_humanas', 'metricas_entrega', 'workers']) t;
select ok(has_table_privilege('docredux_backend', 'public.' || v, 'SELECT'), 'backend lê ' || v)
  from unnest(array['v_fila_backoffice', 'v_criterios_piloto', 'creditos_ledger']) v;

-- --------------------------------------------------------------------------- AC 6: índices da fila
select ok((select indexdef like '%(prioridade, criado_em)%WHERE (status = ''na_fila''%'
             from pg_indexes where indexname = 'jobs_fila_idx'), 'índice parcial da fila');
select ok((select indexdef like 'CREATE UNIQUE INDEX%(documento_id, tipo)%WHERE%'
             from pg_indexes where indexname = 'jobs_ativo_unico'), 'único parcial de job ativo');

insert into ids values ('u1', tests.novo_usuario('u1@teste.local'));
insert into ids values ('d_job', tests.novo_documento((select id from ids where nome = 'u1'), 'triagem'));
insert into public.jobs (documento_id, tipo) values ((select id from ids where nome = 'd_job'), 'triagem');
select is((select prioridade from public.jobs where documento_id = (select id from ids where nome = 'd_job')), 0,
          'prioridade padrão da triagem = 0');
select throws_ok(
  $$ insert into public.jobs (documento_id, tipo) values ((select id from ids where nome = 'd_job'), 'triagem') $$,
  '23505', null, 'segundo job ativo do mesmo tipo falha');
update public.jobs set status = 'concluido' where documento_id = (select id from ids where nome = 'd_job');
select lives_ok(
  $$ insert into public.jobs (documento_id, tipo) values ((select id from ids where nome = 'd_job'), 'triagem') $$,
  'com o anterior concluído, novo job do mesmo tipo entra');
insert into public.jobs (documento_id, tipo)
values ((select id from ids where nome = 'd_job'), 'processar'), ((select id from ids where nome = 'd_job'), 'juiz');
select results_eq(
  $$ select tipo::text, prioridade from public.jobs
      where documento_id = (select id from ids where nome = 'd_job') and tipo <> 'triagem' order by prioridade $$,
  $$ values ('processar', 10), ('juiz', 50) $$,
  'prioridades padrão: processar 10, juiz 50');

-- --------------------------------------------------------------------------- AC 7: config
select results_eq(
  $$ select chave, valor from public.config
      where chave in ('max_paginas', 'max_mb', 'creditos_iniciais', 'juiz_fracao', 'nota_baixa_max',
                      'divergencia_nota_alta_min', 'divergencia_nota_baixa_max', 'juiz_sem_nota_horas',
                      'amostra_revisao', 'storage_teto_mb', 'juiz_modelo')
      order by chave $$,
  $$ values ('amostra_revisao', '0.10'::jsonb), ('creditos_iniciais', '150'), ('divergencia_nota_alta_min', '4'),
            ('divergencia_nota_baixa_max', '2'), ('juiz_fracao', '1.0'), ('juiz_modelo', '"claude-opus-5-5"'),
            ('juiz_sem_nota_horas', '48'), ('max_mb', '80'), ('max_paginas', '300'),
            ('nota_baixa_max', '3'), ('storage_teto_mb', '900') $$,
  'config com os padrões do piloto');
select ok(exists (select 1 from public.config where chave = 'juiz_preco_entrada_usd_mtok')
          and exists (select 1 from public.config where chave = 'juiz_preco_saida_usd_mtok'),
          'chaves de preço do juiz presentes');

-- --------------------------------------------------------------------------- AC 8: seed
select results_eq(
  $$ select id, tipo::text from public.perfis_template where dono is null order by id $$,
  $$ values ('mathema-saeb', 'ficha'), ('mec-sase-relatorio', 'relatorio') $$,
  'perfis semente de engine/perfis com dono nulo');
select is((select json ->> 'nome' from public.perfis_template where id = 'mec-sase-relatorio'), 'mec-sase-relatorio',
          'json do perfil gravado inteiro');

-- --------------------------------------------------------------------------- AC 11: nota por versão
insert into ids values ('d_v', tests.novo_documento((select id from ids where nome = 'u1'), 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'd_v'), 1, false);
select tests.nova_entrega((select id from ids where nome = 'd_v'), 2, true);
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'd_v'), 1, 2);
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'd_v'), 2, 5)
  on conflict (documento_id, versao) do update set nota = excluded.nota, atualizado_em = now();
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'd_v'), 2, 4)
  on conflict (documento_id, versao) do update set nota = excluded.nota, atualizado_em = now();
select results_eq(
  $$ select versao, nota from public.feedback_usuario where documento_id = (select id from ids where nome = 'd_v') order by versao $$,
  $$ values (1, 2), (2, 4) $$,
  'nota da versão 2 não sobrescreve a da versão 1');
select throws_ok(
  $$ insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'd_v'), 3, 4) $$,
  '23503', null, 'feedback só de versão existente');
select throws_ok(
  $$ update public.documentos set entrega_atual = 9 where id = (select id from ids where nome = 'd_v') $$,
  '23503', null, 'entrega_atual aponta versão existente');

-- --------------------------------------------------------------------------- AC 9: fila do backoffice
update public.config set valor = '0' where chave = 'amostra_revisao';  -- sem sorteio nesta parte
select set_config('tests.u1', (select id::text from ids where nome = 'u1'), true);

-- template_novo
insert into ids values ('f_tpl', tests.novo_documento(current_setting('tests.u1')::uuid, 'recusado', null, 'template_novo'));
-- recusado por outro motivo: fora da fila
insert into ids values ('f_esc', tests.novo_documento(current_setting('tests.u1')::uuid, 'recusado', null, 'escaneado'));
-- gate_cobertura (em_revisao, sem entrega_atual)
insert into ids values ('f_gate', tests.novo_documento(current_setting('tests.u1')::uuid, 'em_revisao'));
select tests.nova_entrega((select id from ids where nome = 'f_gate'), 1, false, 'bloqueado');
-- nota_baixa
insert into ids values ('f_nota', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_nota'));
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'f_nota'), 1, 3);
-- divergencia: juiz reprovou, nota 4
insert into ids values ('f_div', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_div'));
insert into public.avaliacoes_juiz (documento_id, versao, modelo, veredito, nota_juiz, criterios, entrada)
values ((select id from ids where nome = 'f_div'), 1, 'm', 'reprovado', 2, '{}', 'folha');
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'f_div'), 1, 4);
-- divergencia: juiz aprovou, nota 2 (também é nota_baixa)
insert into ids values ('f_div2', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_div2'));
insert into public.avaliacoes_juiz (documento_id, versao, modelo, veredito, nota_juiz, criterios, entrada)
values ((select id from ids where nome = 'f_div2'), 1, 'm', 'aprovado', 5, '{}', 'folha');
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'f_div2'), 1, 2);
-- juiz aprovou e nota 5: fora
insert into ids values ('f_ok', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_ok'));
insert into public.avaliacoes_juiz (documento_id, versao, modelo, veredito, nota_juiz, criterios, entrada)
values ((select id from ids where nome = 'f_ok'), 1, 'm', 'aprovado', 5, '{}', 'folha');
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'f_ok'), 1, 5);
-- juiz_reprovou_sem_nota: há 49 h
insert into ids values ('f_semnota', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_semnota'));
insert into public.avaliacoes_juiz (documento_id, versao, modelo, veredito, nota_juiz, criterios, entrada, criado_em)
values ((select id from ids where nome = 'f_semnota'), 1, 'm', 'reprovado', 2, '{}', 'folha', now() - interval '49 hours');
-- juiz reprovou há 10 h, sem nota: ainda fora
insert into ids values ('f_semnota_cedo', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_semnota_cedo'));
insert into public.avaliacoes_juiz (documento_id, versao, modelo, veredito, nota_juiz, criterios, entrada, criado_em)
values ((select id from ids where nome = 'f_semnota_cedo'), 1, 'm', 'reprovado', 2, '{}', 'folha', now() - interval '10 hours');
-- problema_marcado (aberto) e um resolvido (fora)
insert into ids values ('f_prob', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_prob'));
insert into public.problemas_marcados (documento_id, versao, pagina, tipo, descricao)
values ((select id from ids where nome = 'f_prob'), 1, 2, 'erro', 'x');
insert into ids values ('f_prob_ok', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_prob_ok'));
insert into public.problemas_marcados (documento_id, versao, pagina, tipo, descricao, status)
values ((select id from ids where nome = 'f_prob_ok'), 1, 2, 'formato', 'x', 'resolvido');
-- juiz_falhou
insert into ids values ('f_jf', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_jf'));
insert into public.jobs (documento_id, tipo, status, concluido_em) values ((select id from ids where nome = 'f_jf'), 'juiz', 'falhou', now());
-- excluido e expirado com tudo que geraria motivo: fora
insert into ids values ('f_exc', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
insert into ids values ('f_exp', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega(id) from ids where nome in ('f_exc', 'f_exp');
insert into public.feedback_usuario (documento_id, versao, nota) select id, 1, 1 from ids where nome in ('f_exc', 'f_exp');
insert into public.problemas_marcados (documento_id, versao, pagina, tipo) select id, 1, 1, 'erro' from ids where nome in ('f_exc', 'f_exp');
insert into public.jobs (documento_id, tipo, status) select id, 'juiz', 'falhou' from ids where nome in ('f_exc', 'f_exp');
update public.documentos set status = 'excluido' where id = (select id from ids where nome = 'f_exc');
update public.documentos set status = 'expirado' where id = (select id from ids where nome = 'f_exp');
-- revisão humana posterior ao motivo: fora
insert into ids values ('f_rev', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_rev'));
insert into public.feedback_usuario (documento_id, versao, nota, atualizado_em)
values ((select id from ids where nome = 'f_rev'), 1, 1, now() - interval '1 hour');
insert into public.revisoes_humanas (documento_id, versao, revisor_id, motivo, veredito)
values ((select id from ids where nome = 'f_rev'), 1, current_setting('tests.u1')::uuid, 'nota_baixa', 'aprovado');
-- revisão ANTERIOR ao motivo: continua
insert into ids values ('f_rev_velha', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
select tests.nova_entrega((select id from ids where nome = 'f_rev_velha'));
insert into public.revisoes_humanas (documento_id, versao, revisor_id, motivo, veredito, criado_em)
values ((select id from ids where nome = 'f_rev_velha'), 1, current_setting('tests.u1')::uuid, 'amostra', 'aprovado', now() - interval '2 hours');
insert into public.feedback_usuario (documento_id, versao, nota) values ((select id from ids where nome = 'f_rev_velha'), 1, 1);

select results_eq(
  $$ select i.nome, f.motivo from public.v_fila_backoffice f join ids i on i.id = f.documento_id order by 1, 2 $$,
  $$ values ('f_div', 'divergencia'), ('f_div2', 'divergencia'), ('f_div2', 'nota_baixa'),
            ('f_gate', 'gate_cobertura'), ('f_jf', 'juiz_falhou'), ('f_nota', 'nota_baixa'),
            ('f_prob', 'problema_marcado'), ('f_rev_velha', 'nota_baixa'),
            ('f_semnota', 'juiz_reprovou_sem_nota'), ('f_tpl', 'template_novo') $$,
  'fila do backoffice: um motivo por caso; excluido/expirado/revisado/resolvido fora');
select ok((select bool_and(dias_para_expirar between 29 and 30) from public.v_fila_backoffice), 'dias até expirar');

-- amostra (Q11): com 100%, as entregas `pronto` sem outro motivo entram; as que têm motivo não repetem
update public.config set valor = '1.0' where chave = 'amostra_revisao';
select results_eq(
  $$ select i.nome from public.v_fila_backoffice f join ids i on i.id = f.documento_id
      where f.motivo = 'amostra' and i.nome like 'f\_%' order by 1 $$,
  $$ values ('f_ok'), ('f_prob_ok'), ('f_semnota_cedo') $$,
  'amostra: só entregas pronto sem outro motivo (revisadas saem)');
update public.config set valor = '0.10' where chave = 'amostra_revisao';
select ok((select count(*) from public.v_fila_backoffice where motivo = 'amostra') <= 3, 'amostra de 10% é subconjunto');

-- --------------------------------------------------------------------------- AC 10: critérios do piloto
insert into ids values ('interno', tests.novo_usuario('interno@teste.local'));
update public.perfis_usuario set interno = true where id = (select id from ids where nome = 'interno');
insert into ids values ('m_a', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
insert into ids values ('m_b', tests.novo_documento(current_setting('tests.u1')::uuid, 'pronto'));
insert into ids values ('m_int', tests.novo_documento((select id from ids where nome = 'interno'), 'pronto'));
update public.documentos set entregue_em = now() where id in (select id from ids where nome in ('m_a', 'm_b', 'm_int'));
insert into public.metricas_entrega (documento_id, usuario_id, interno, modo, cobertura, gate_primeira_passagem,
                                     nota_usuario, problemas_erro, intervencao_humana, juiz_custo_usd, minutos_humanos)
values ((select id from ids where nome = 'm_a'), current_setting('tests.u1')::uuid, false, 'original', 0.999, 'ok', 5, 0, false, 0.10, 0),
       ((select id from ids where nome = 'm_b'), current_setting('tests.u1')::uuid, false, 'original', 0.990, 'bloqueado', 4, 1, true, 0.20, 10),
       ((select id from ids where nome = 'm_int'), (select id from ids where nome = 'interno'), true, 'original', 0.5, 'bloqueado', 1, 3, true, 9, 99);
insert into public.revisoes_humanas (documento_id, revisor_id, motivo, veredito, concorda_juiz)
values ((select id from ids where nome = 'm_a'), current_setting('tests.u1')::uuid, 'amostra', 'aprovado', true),
       ((select id from ids where nome = 'm_b'), current_setting('tests.u1')::uuid, 'gate', 'reprovado', false),
       ((select id from ids where nome = 'm_int'), current_setting('tests.u1')::uuid, 'gate', 'reprovado', false);
select results_eq(
  $$ select entregas::int, nota_media, erros_reportados, concordancia_juiz_humano, cobertura_original,
            gate_primeira_passagem, sem_intervencao_humana, juiz_custo_medio_usd, minutos_humanos_medio
       from public.v_criterios_piloto $$,
  $$ values (2, 4.50, 0.5000, 0.5000, 0.5000, 0.5000, 0.5000, 0.150000, 5.00) $$,
  'critérios do piloto só com participantes');

-- --------------------------------------------------------------------------- Unidade 4 (stories 4.3, 4.7)
select has_column('public', 'documentos', 'sha256', 'documentos.sha256 (DT-18)');
select has_column('public', 'entregas', 'tamanho_bytes', 'entregas.tamanho_bytes (DT-20)');
select has_table('public', 'notificacoes', 'tabela notificacoes (DT-22)');
select col_is_unique('public', 'notificacoes', array['documento_id', 'evento'], 'um aviso por (documento, evento)');
select ok((select relrowsecurity from pg_class where oid = 'public.notificacoes'::regclass), 'notificacoes com RLS');
select ok(not has_table_privilege('authenticated', 'public.notificacoes', 'SELECT'), 'usuário não lê notificacoes');
select ok(not has_table_privilege('anon', 'public.notificacoes', 'SELECT'), 'anon não lê notificacoes');
select ok(has_table_privilege('docredux_backend', 'public.notificacoes', p), 'backend: ' || p || ' em notificacoes')
  from unnest(array['SELECT', 'INSERT', 'UPDATE']) p;
select ok(not has_table_privilege('docredux_backend', 'public.notificacoes', 'DELETE'), 'backend não apaga notificacoes');
select hasnt_column('public', 'notificacoes', c, 'notificacoes não guarda ' || c)
  from unnest(array['email', 'texto', 'assunto']) c;
select is((select valor from public.config where chave = 'termo_versao_atual'), '"1"'::jsonb, 'termo v1');
select ok(exists (select 1 from public.config where chave = c), 'config ' || c)
  from unnest(array['notificacao_prazo_revisao', 'limpeza_executada_em']) c;
select throws_ok($$insert into public.documentos (usuario_id, idempotency_key, nome_arquivo, tamanho_bytes, sha256)
                   values (gen_random_uuid(), 'x', 'x', 1, 'ABC')$$, '23514', null, 'sha256 só hex minúsculo de 64');

select * from finish();
rollback;
