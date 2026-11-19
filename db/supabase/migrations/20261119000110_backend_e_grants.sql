-- Story 3.1 — papel de backend e grants (spec §6 "Papel de backend", NFR-3).
--
-- O Supabase dá ALL nas tabelas de public a anon e authenticated (default privileges).
-- Aqui tudo é explícito: anon/authenticated ficam sem nada; a 3.3 devolve só o SELECT
-- das tabelas que o usuário lê, junto com as políticas de RLS.

revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from public, anon, authenticated;

alter default privileges for role postgres in schema public revoke all on tables    from anon, authenticated;
alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public revoke execute on functions from public, anon, authenticated;

-- docredux_backend: API e worker no caminho SQL (DT-14). LOGIN, BYPASSRLS, sem superusuário.
-- Papéis são do cluster: o IF NOT EXISTS deixa o `supabase db reset` repetível.
-- A senha NÃO é versionada: a unidade 7 roda `alter role docredux_backend password ...` no projeto.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'docredux_backend') then
    create role docredux_backend login bypassrls nosuperuser nocreatedb nocreaterole noinherit;
  end if;
end
$$;

grant usage on schema public to docredux_backend;
grant select on all tables in schema public to docredux_backend;
grant usage, select on all sequences in schema public to docredux_backend;

-- Escrita da API e do worker. Ledger e saldo NÃO: só pelas funções private.credito_* (3.2).
grant insert, update on public.documentos, public.jobs, public.entregas, public.avaliacoes_juiz,
  public.feedback_usuario, public.problemas_marcados, public.metricas_entrega, public.config,
  public.perfis_template
  to docredux_backend;
grant insert, update, delete on public.workers to docredux_backend;
grant insert on public.revisoes_humanas to docredux_backend;
-- perfis_usuario: a linha nasce pelo gatilho de auth.users (3.2). A API só grava termo, senha
-- provisória e a exclusão da conta (§9, §14). Sem UPDATE em saldo, papel e interno: o primeiro
-- admin e os internos são definidos por SQL na unidade 7.
grant update (email, termo_versao, termo_aceito_em, senha_provisoria, exclusao_conta_em)
  on public.perfis_usuario to docredux_backend;
