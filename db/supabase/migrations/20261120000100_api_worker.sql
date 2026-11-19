-- Unidade 4 — API e worker (spec v1.5 §6, §7, §9, DT-18, DT-20, DT-22).

-- --------------------------------------------------------------------------- Story 4.3
-- DT-18: a triagem "confere tamanho e SHA-256" do original remontado. O hash vem do
-- navegador no POST /documentos (opcional); nulo = só o tamanho é conferido.
alter table public.documentos add column sha256 text
  check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$');

-- DT-20: uso do Storage sem ler storage.objects = originais + saídas de documentos vivos.
alter table public.entregas add column tamanho_bytes bigint not null default 0
  check (tamanho_bytes >= 0);

-- --------------------------------------------------------------------------- Story 4.7 (DT-22)
create type public.evento_notificacao as enum ('pronto', 'em_revisao');
create type public.status_notificacao as enum ('pendente', 'enviado', 'falhou', 'desligado', 'sem_destinatario');

create table public.notificacoes (
  id           bigint generated always as identity primary key,
  documento_id uuid not null references public.documentos (id),
  usuario_id   uuid not null references public.perfis_usuario (id),
  evento       public.evento_notificacao not null,
  status       public.status_notificacao not null default 'pendente',
  tentativas   integer not null default 0 check (tentativas >= 0),
  erro         text,
  provedor_id  text,
  criado_em    timestamptz not null default now(),
  enviado_em   timestamptz,
  unique (documento_id, evento)
);
alter table public.notificacoes enable row level security;
create index notificacoes_reenvio_idx on public.notificacoes (criado_em) where status in ('pendente', 'falhou');
comment on table public.notificacoes is
  'Avisos por e-mail (DT-22). Um por (documento, evento). Não guarda o e-mail nem o texto. Sem leitura pelo usuário.';

grant select, insert, update on public.notificacoes to docredux_backend;
grant usage, select on all sequences in schema public to docredux_backend;

-- --------------------------------------------------------------------------- config
insert into public.config (chave, valor) values
  ('termo_versao_atual',         '"1"'),   -- §14: termo v1 (texto final na unidade 7)
  ('notificacao_prazo_revisao',  'null'),  -- DT-22, Q19: nulo = o aviso do gate não promete prazo
  ('limpeza_executada_em',       'null')   -- §7: última tarefa diária de retenção
on conflict (chave) do nothing;
