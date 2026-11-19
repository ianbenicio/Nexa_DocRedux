-- Story 3.3 — RLS e Storage (spec §6 tabela "Leitura pelo usuário", Storage; §13; NFR-3, NFR-4).
--
-- authenticated: só SELECT das próprias linhas, e só nas tabelas que o §6 deixa o usuário ler.
-- Nenhuma política de INSERT/UPDATE/DELETE para authenticated ou anon: toda escrita passa pela API
-- ou pelo worker (docredux_backend, BYPASSRLS). anon não tem grant em nada.
-- Precedente (Engram classmate-connect-13-fix-de-performance-rls-...): (select auth.uid()) e uma
-- política de SELECT por tabela.

grant usage on schema public to authenticated;

grant select on public.perfis_usuario, public.perfis_template, public.documentos,
  public.creditos_ledger, public.entregas, public.feedback_usuario, public.problemas_marcados
  to authenticated;

create policy perfis_usuario_propria on public.perfis_usuario
  for select to authenticated
  using (id = (select auth.uid()));

-- Q5: perfis semente (dono nulo) para todas as contas; perfil privado só para o dono (D2).
create policy perfis_template_semente_ou_proprio on public.perfis_template
  for select to authenticated
  using (dono is null or dono = (select auth.uid()));

create policy documentos_proprios on public.documentos
  for select to authenticated
  using (usuario_id = (select auth.uid()));

create policy creditos_ledger_proprio on public.creditos_ledger
  for select to authenticated
  using (usuario_id = (select auth.uid()));

-- Só a entrega_atual de documento `pronto`: nunca a de `em_revisao` (§6, §9 GET /documentos/{id}).
create policy entregas_atual_de_pronto on public.entregas
  for select to authenticated
  using (exists (
    select 1 from public.documentos d
     where d.id = entregas.documento_id
       and d.usuario_id = (select auth.uid())
       and d.status = 'pronto'
       and d.entrega_atual = entregas.versao));

create policy feedback_usuario_proprio on public.feedback_usuario
  for select to authenticated
  using (exists (
    select 1 from public.documentos d
     where d.id = feedback_usuario.documento_id and d.usuario_id = (select auth.uid())));

create policy problemas_marcados_proprios on public.problemas_marcados
  for select to authenticated
  using (exists (
    select 1 from public.documentos d
     where d.id = problemas_marcados.documento_id and d.usuario_id = (select auth.uid())));

-- Sem política (e sem grant) para authenticated: jobs, workers, avaliacoes_juiz, revisoes_humanas,
-- metricas_entrega, config. As views do backoffice também ficam fora (3.1).

-- --------------------------------------------------------------------------- Storage
-- Buckets privados. originais: partes de até 42 MB (DT-18), limite de objeto 50 MB (máximo do Free).
insert into storage.buckets (id, name, public, file_size_limit)
values ('originais', 'originais', false, 52428800),
       ('saidas',    'saidas',    false, 52428800)
on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit;

-- INSERT só na pasta do próprio usuário (§6): originais/{usuario_id}/{documento_id}/original.partNN.
-- Além do §6, a política confere o nome da parte e que o documento é do usuário e espera upload.
-- Sem SELECT/UPDATE/DELETE para o usuário; download só por URL assinada da API (60 s).
create policy originais_insert_na_propria_pasta on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'originais'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and name ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}/original\.part[0-9]{2}$'
    and exists (
      select 1 from public.documentos d
       where d.id::text = (storage.foldername(name))[2]
         and d.usuario_id = (select auth.uid())
         and d.status = 'aguardando_upload'));

-- saidas: só o backend escreve (chave service_role na API de Storage); nenhuma política para usuário.
