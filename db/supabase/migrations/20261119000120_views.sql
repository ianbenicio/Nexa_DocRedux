-- Story 3.1 — views do backoffice (spec §9 "Motivos da fila") e dos critérios do piloto (§12).
-- security_invoker: quem lê precisa de acesso às tabelas. Só backend e service_role têm grant.

-- Leitura de um número de config com padrão, para os limiares de Q6/Q11 (DT-9).
create function public.config_num(p_chave text, p_padrao numeric) returns numeric
language sql stable set search_path = '' as $$
  select coalesce(
    (select case when jsonb_typeof(c.valor) = 'number' then (c.valor #>> '{}')::numeric end
       from public.config c where c.chave = p_chave),
    p_padrao)
$$;
revoke execute on function public.config_num(text, numeric) from public, anon, authenticated;
grant execute on function public.config_num(text, numeric) to docredux_backend, service_role;

-- --------------------------------------------------------------------------- fila do backoffice
-- Uma linha por (documento, motivo). Documentos excluido/expirado não geram motivo.
-- O item sai quando há revisão humana posterior ao motivo (`desde`), ou quando o problema é
-- resolvido (o motivo problema_marcado só existe com problema aberto).
-- Versão vigente = entrega_atual, ou a última versão gerada (em_revisao não tem entrega_atual).
create view public.v_fila_backoffice with (security_invoker = true) as
with doc as (
  select d.*,
         coalesce(d.entrega_atual,
                  (select max(e.versao) from public.entregas e where e.documento_id = d.id)) as versao_vigente
    from public.documentos d
   where d.status not in ('excluido', 'expirado')
),
juiz as (
  select distinct on (a.documento_id, a.versao) a.documento_id, a.versao, a.veredito, a.criado_em
    from public.avaliacoes_juiz a
   order by a.documento_id, a.versao, a.criado_em desc, a.id desc
),
base as (
  select doc.id as documento_id, doc.status, doc.motivo_recusa, doc.criado_em, doc.confirmado_em,
         doc.versao_vigente, ent.criado_em as entrega_em,
         j.veredito as juiz_veredito, j.criado_em as juiz_em,
         f.nota, f.atualizado_em as nota_em
    from doc
    left join public.entregas ent on ent.documento_id = doc.id and ent.versao = doc.versao_vigente
    left join juiz j on j.documento_id = doc.id and j.versao = doc.versao_vigente
    left join public.feedback_usuario f on f.documento_id = doc.id and f.versao = doc.versao_vigente
),
motivos as (
  select documento_id, 'template_novo'::text as motivo, criado_em as desde
    from base where status = 'recusado' and motivo_recusa = 'template_novo'
  union all
  select documento_id, 'gate_cobertura', coalesce(entrega_em, confirmado_em, criado_em)
    from base where status = 'em_revisao'
  union all
  select documento_id, 'nota_baixa', nota_em
    from base where nota <= public.config_num('nota_baixa_max', 3)
  union all
  select documento_id, 'divergencia', greatest(juiz_em, nota_em)
    from base
   where (juiz_veredito in ('reprovado', 'duvidoso') and nota >= public.config_num('divergencia_nota_alta_min', 4))
      or (juiz_veredito = 'aprovado' and nota <= public.config_num('divergencia_nota_baixa_max', 2))
  union all
  select documento_id, 'juiz_reprovou_sem_nota', juiz_em
    from base
   where juiz_veredito = 'reprovado' and nota is null
     and juiz_em <= now() - make_interval(hours => public.config_num('juiz_sem_nota_horas', 48)::int)
  union all
  select p.documento_id, 'problema_marcado', min(p.criado_em)
    from public.problemas_marcados p
    join doc on doc.id = p.documento_id
   where p.status = 'aberto'
   group by p.documento_id
  union all
  select j.documento_id, 'juiz_falhou', coalesce(j.concluido_em, j.criado_em)
    from (select distinct on (jb.documento_id) jb.*
            from public.jobs jb
           where jb.tipo = 'juiz'
           order by jb.documento_id, jb.criado_em desc, jb.id desc) j
    join doc on doc.id = j.documento_id
   where j.status = 'falhou'
),
amostra as (
  -- Q11: 10% das entregas `pronto` sem outro motivo. Sorteio determinístico pelo hash do id,
  -- para o item não entrar e sair da fila a cada leitura.
  select b.documento_id, 'amostra'::text as motivo, b.entrega_em as desde
    from base b
   where b.status = 'pronto' and b.versao_vigente is not null
     and not exists (select 1 from motivos m where m.documento_id = b.documento_id)
     and (hashtextextended(b.documento_id::text, 0) & 9223372036854775807) % 10000
         < round(public.config_num('amostra_revisao', 0.10) * 10000)
),
todos as (
  select * from motivos union all select * from amostra
)
select t.documento_id,
       d.usuario_id,
       u.email,
       t.motivo,
       t.desde,
       now() - t.desde as idade,
       d.status,
       d.criado_em,
       d.expira_em,
       greatest(0, ceil(extract(epoch from (d.expira_em - now())) / 86400))::integer as dias_para_expirar,
       b.versao_vigente,
       b.juiz_veredito,
       b.nota as nota_usuario
  from todos t
  join public.documentos d on d.id = t.documento_id
  join base b on b.documento_id = t.documento_id
  left join public.perfis_usuario u on u.id = d.usuario_id
 where not exists (
         select 1 from public.revisoes_humanas r
          where r.documento_id = t.documento_id and r.criado_em >= t.desde);

comment on view public.v_fila_backoffice is 'Fila do backoffice (spec §9): uma linha por (documento, motivo)';

-- --------------------------------------------------------------------------- critérios do piloto
-- §12 "Agregados do piloto" (piloto §4). Só participantes: fora quem é interno na métrica ou no
-- perfil (Q15). Entrega = documento que chegou a `pronto` (documentos.entregue_em).
create view public.v_criterios_piloto with (security_invoker = true) as
with m as (
  select me.*, d.entregue_em is not null as entregue
    from public.metricas_entrega me
    join public.documentos d on d.id = me.documento_id
    left join public.perfis_usuario u on u.id = me.usuario_id
   where not me.interno and not coalesce(u.interno, false)
),
rev as (
  select r.concorda_juiz
    from public.revisoes_humanas r
    join m on m.documento_id = r.documento_id
   where r.concorda_juiz is not null
)
select
  (select count(*) from m where entregue)                                           as entregas,
  (select round(avg(nota_usuario), 2) from m where entregue)                        as nota_media,
  4.5                                                                               as nota_media_meta,
  (select round(count(*) filter (where problemas_erro > 0)::numeric
                / nullif(count(*), 0), 4) from m where entregue)                    as erros_reportados,
  0.03                                                                              as erros_reportados_meta,
  (select round(count(*) filter (where concorda_juiz)::numeric
                / nullif(count(*), 0), 4) from rev)                                 as concordancia_juiz_humano,
  0.90                                                                              as concordancia_meta,
  (select round(count(*) filter (where cobertura >= 0.995)::numeric
                / nullif(count(*), 0), 4) from m where entregue and modo = 'original') as cobertura_original,
  1.0                                                                               as cobertura_original_meta,
  (select round(count(*) filter (where gate_primeira_passagem = 'bloqueado')::numeric
                / nullif(count(*), 0), 4)
     from m where modo = 'original' and gate_primeira_passagem is not null)         as gate_primeira_passagem,
  (select round(count(*) filter (where not intervencao_humana)::numeric
                / nullif(count(*), 0), 4) from m where entregue)                    as sem_intervencao_humana,
  (select round(avg(juiz_custo_usd), 6) from m where entregue)                      as juiz_custo_medio_usd,
  (select round(avg(minutos_humanos), 2) from m where entregue)                     as minutos_humanos_medio;

comment on view public.v_criterios_piloto is 'Critérios do piloto (spec §12), só participantes (Q15)';

revoke all on public.v_fila_backoffice, public.v_criterios_piloto from anon, authenticated;
grant select on public.v_fila_backoffice, public.v_criterios_piloto to docredux_backend, service_role;
