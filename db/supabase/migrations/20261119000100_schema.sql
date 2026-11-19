-- Story 3.1 — schema do piloto (spec v1.3 §6, §7, §8, §12, §14).
-- Toda tabela nasce com RLS ligada. As políticas de leitura do usuário ficam na 3.3;
-- até lá, authenticated/anon não leem nada.

-- --------------------------------------------------------------------------- enums
create type public.papel_usuario as enum ('usuario', 'admin');

-- §8: 13 estados; sem aguardando_perfil (Q3: template novo é recusado na triagem).
create type public.status_documento as enum (
  'aguardando_upload', 'triagem', 'recusado', 'upload_falhou', 'aguardando_confirmacao',
  'na_fila', 'processando', 'pronto', 'em_revisao', 'falhou', 'cancelado', 'excluido', 'expirado'
);

create type public.motivo_recusa as enum (
  'escaneado', 'protegido', 'corrompido', 'excede_paginas', 'excede_tamanho', 'template_novo'
);

-- Triagem.tipo do contrato do motor (§4).
create type public.tipo_documento as enum ('ficha', 'relatorio', 'desconhecido');

create type public.tipo_job as enum ('triagem', 'processar', 'juiz');
create type public.status_job as enum ('na_fila', 'processando', 'concluido', 'falhou', 'cancelado');
create type public.origem_job as enum ('usuario', 'reprocessar');
create type public.estado_origem_job as enum ('em_revisao', 'pronto', 'falhou');

-- §6: ajuste só na exclusão da conta (§14).
create type public.tipo_lancamento as enum ('concessao', 'recarga', 'reserva', 'captura', 'estorno', 'ajuste');

create type public.gate_cobertura as enum ('ok', 'bloqueado');
create type public.veredito_juiz as enum ('aprovado', 'reprovado', 'duvidoso');
create type public.entrada_juiz as enum ('folha', 'folha+trechos');
create type public.tipo_problema as enum ('erro', 'formato');
create type public.status_problema as enum ('aberto', 'resolvido');
create type public.veredito_revisao as enum ('aprovado', 'reprovado');
create type public.acao_revisao as enum ('nenhuma', 'reprocessado', 'cancelado_estornado', 'perfil_anexado');
create type public.modo_saida as enum ('original', 'sintese');

-- --------------------------------------------------------------------------- perfis_usuario
-- id = auth.users.id, SEM FK para auth.users: depois da exclusão da conta (§14 item 4) o usuário
-- sai do Auth e esta linha fica com o id pseudônimo. Uma FK, mesmo sem CASCADE, impediria a remoção.
create table public.perfis_usuario (
  id                uuid primary key,
  email             text,
  papel             public.papel_usuario not null default 'usuario',
  interno           boolean not null default false,
  saldo             integer not null default 0 check (saldo >= 0),
  termo_versao      text,
  termo_aceito_em   timestamptz,
  senha_provisoria  boolean not null default true,
  exclusao_conta_em timestamptz,
  criado_em         timestamptz not null default now()
);
alter table public.perfis_usuario enable row level security;
comment on column public.perfis_usuario.id is 'auth.users.id; sem FK (fica como pseudônimo depois da exclusão da conta, spec §14)';
comment on column public.perfis_usuario.saldo is 'Materializado (DT-7); só muda pelas funções private.credito_*';

-- --------------------------------------------------------------------------- perfis_template
create table public.perfis_template (
  id        text primary key,
  dono      uuid references public.perfis_usuario (id),
  tipo      public.tipo_documento not null check (tipo <> 'desconhecido'),
  json      jsonb not null,
  versao    integer not null default 1 check (versao >= 1),
  ativo     boolean not null default true,
  criado_em timestamptz not null default now()
);
alter table public.perfis_template enable row level security;
create index perfis_template_dono_idx on public.perfis_template (dono);
comment on column public.perfis_template.dono is 'null = perfil semente, visível a todas as contas (Q5); senão, perfil privado da conta (D2)';

-- --------------------------------------------------------------------------- documentos
create table public.documentos (
  id                  uuid primary key default gen_random_uuid(),
  usuario_id          uuid not null references public.perfis_usuario (id),
  idempotency_key     text not null,
  nome_arquivo        text not null,
  tamanho_bytes       bigint not null check (tamanho_bytes > 0),
  status              public.status_documento not null default 'aguardando_upload',
  motivo_recusa       public.motivo_recusa,
  paginas             integer check (paginas >= 0),
  palavras            integer check (palavras >= 0),
  tipo                public.tipo_documento,
  perfil_id           text references public.perfis_template (id),
  template_novo       boolean not null default false,
  opcoes              jsonb,
  orcamento           jsonb,
  creditos_reservados integer check (creditos_reservados >= 0),
  partes              integer not null default 1 check (partes >= 1),
  entrega_atual       integer,
  reprocessando       boolean not null default false,
  exclusao_pendente   boolean not null default false,
  criado_em           timestamptz not null default now(),
  confirmado_em       timestamptz,
  entregue_em         timestamptz,
  -- = criado_em + 30 dias: os dois defaults usam o mesmo now() da transação.
  expira_em           timestamptz not null default (now() + interval '30 days'),
  unique (usuario_id, idempotency_key),
  check ((status = 'recusado') = (motivo_recusa is not null))
);
alter table public.documentos enable row level security;
create index documentos_usuario_idx on public.documentos (usuario_id, criado_em desc);
create index documentos_expira_idx on public.documentos (expira_em) where status not in ('excluido', 'expirado');

-- --------------------------------------------------------------------------- jobs (§7)
create table public.jobs (
  id             bigint generated always as identity primary key,
  documento_id   uuid not null references public.documentos (id),
  tipo           public.tipo_job not null,
  status         public.status_job not null default 'na_fila',
  prioridade     integer not null,
  tentativas     integer not null default 0 check (tentativas >= 0),
  max_tentativas integer not null default 3 check (max_tentativas >= 1),
  disponivel_em  timestamptz not null default now(),
  iniciado_em    timestamptz,
  heartbeat_em   timestamptz,
  worker_id      text,
  erro           text,
  origem         public.origem_job not null default 'usuario',
  estado_origem  public.estado_origem_job,
  criado_em      timestamptz not null default now(),
  concluido_em   timestamptz,
  check (estado_origem is null or (origem = 'reprocessar' and tipo = 'processar'))
);
alter table public.jobs enable row level security;
-- §7: índice parcial da fila e um job ativo por (documento, tipo).
create index jobs_fila_idx on public.jobs (prioridade, criado_em) where status = 'na_fila';
create unique index jobs_ativo_unico on public.jobs (documento_id, tipo) where status in ('na_fila', 'processando');
create index jobs_processando_idx on public.jobs (heartbeat_em) where status = 'processando';
create index jobs_documento_idx on public.jobs (documento_id);

-- Prioridade padrão por tipo (§7): triagem 0, processar 10, juiz 50.
create function public.jobs_prioridade_padrao() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.prioridade is null then
    new.prioridade := case new.tipo when 'triagem' then 0 when 'processar' then 10 else 50 end;
  end if;
  return new;
end $$;
create trigger jobs_prioridade_padrao before insert on public.jobs
  for each row execute function public.jobs_prioridade_padrao();

-- --------------------------------------------------------------------------- workers (DT-15)
create table public.workers (
  worker_id   text primary key,
  host        text,
  iniciado_em timestamptz not null default now(),
  visto_em    timestamptz not null default now()
);
alter table public.workers enable row level security;

-- --------------------------------------------------------------------------- creditos_ledger (§5, §6)
create table public.creditos_ledger (
  id           bigint generated always as identity primary key,
  usuario_id   uuid not null references public.perfis_usuario (id),
  tipo         public.tipo_lancamento not null,
  valor        integer not null,
  saldo_apos   integer not null check (saldo_apos >= 0),
  documento_id uuid references public.documentos (id),
  reserva_id   bigint references public.creditos_ledger (id),
  motivo       text,
  criado_por   uuid references public.perfis_usuario (id),
  criado_em    timestamptz not null default now(),
  check (case tipo
    when 'concessao' then valor > 0 and reserva_id is null
    when 'recarga'   then valor > 0 and reserva_id is null
    when 'reserva'   then valor < 0 and reserva_id is null and documento_id is not null
    when 'captura'   then valor = 0 and reserva_id is not null
    when 'estorno'   then valor > 0 and reserva_id is not null
    when 'ajuste'    then valor <= 0 and reserva_id is null
  end)
);
alter table public.creditos_ledger enable row level security;
-- Captura e estorno apontam a reserva; no máximo um dos dois por reserva (idempotência, §6).
create unique index creditos_ledger_reserva_unica on public.creditos_ledger (reserva_id) where reserva_id is not null;
create index creditos_ledger_usuario_idx on public.creditos_ledger (usuario_id, id desc);
create index creditos_ledger_documento_idx on public.creditos_ledger (documento_id) where documento_id is not null;

-- --------------------------------------------------------------------------- entregas (DT-16)
create table public.entregas (
  documento_id   uuid not null references public.documentos (id),
  versao         integer not null check (versao >= 1),
  job_id         bigint references public.jobs (id),
  caminho_pdf    text not null,
  caminho_docx   text,
  relatorio      jsonb not null,
  cobertura      numeric(6, 5) check (cobertura between 0 and 1),
  paginas_depois integer not null check (paginas_depois >= 0),
  gate           public.gate_cobertura not null,
  criado_em      timestamptz not null default now(),
  primary key (documento_id, versao)
);
alter table public.entregas enable row level security;

alter table public.documentos
  add constraint documentos_entrega_atual_fk
  foreign key (id, entrega_atual) references public.entregas (documento_id, versao)
  deferrable initially deferred;

-- --------------------------------------------------------------------------- avaliacoes_juiz (§11)
create table public.avaliacoes_juiz (
  id             bigint generated always as identity primary key,
  documento_id   uuid not null,
  versao         integer not null,
  modelo         text not null,
  veredito       public.veredito_juiz not null,
  nota_juiz      integer not null check (nota_juiz between 1 and 5),
  criterios      jsonb not null,
  resumo         text,
  entrada        public.entrada_juiz not null,
  tokens_entrada integer check (tokens_entrada >= 0),
  tokens_saida   integer check (tokens_saida >= 0),
  custo_usd      numeric(12, 6) check (custo_usd >= 0),
  duracao_s      numeric(10, 3),
  criado_em      timestamptz not null default now(),
  foreign key (documento_id, versao) references public.entregas (documento_id, versao)
);
alter table public.avaliacoes_juiz enable row level security;
create index avaliacoes_juiz_entrega_idx on public.avaliacoes_juiz (documento_id, versao, criado_em desc);

-- --------------------------------------------------------------------------- feedback_usuario
-- pk (documento_id, versao): a nota de uma versão nova não sobrescreve a da primeira entrega.
create table public.feedback_usuario (
  documento_id  uuid not null,
  versao        integer not null,
  nota          integer not null check (nota between 1 and 5),
  comentario    text,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  primary key (documento_id, versao),
  foreign key (documento_id, versao) references public.entregas (documento_id, versao)
);
alter table public.feedback_usuario enable row level security;

-- --------------------------------------------------------------------------- problemas_marcados
create table public.problemas_marcados (
  id           bigint generated always as identity primary key,
  documento_id uuid not null,
  versao       integer not null,
  pagina       integer not null check (pagina >= 1),
  tipo         public.tipo_problema not null,
  descricao    text,
  status       public.status_problema not null default 'aberto',
  criado_em    timestamptz not null default now(),
  foreign key (documento_id, versao) references public.entregas (documento_id, versao)
);
alter table public.problemas_marcados enable row level security;
create index problemas_marcados_documento_idx on public.problemas_marcados (documento_id);
create index problemas_marcados_abertos_idx on public.problemas_marcados (documento_id) where status = 'aberto';

-- --------------------------------------------------------------------------- revisoes_humanas
create table public.revisoes_humanas (
  id            bigint generated always as identity primary key,
  documento_id  uuid not null references public.documentos (id),
  versao        integer,
  revisor_id    uuid not null references public.perfis_usuario (id),
  motivo        text not null,
  veredito      public.veredito_revisao not null,
  concorda_juiz boolean,
  minutos       integer not null default 0 check (minutos >= 0),
  acao          public.acao_revisao not null default 'nenhuma',
  notas         text,
  criado_em     timestamptz not null default now()
);
alter table public.revisoes_humanas enable row level security;
create index revisoes_humanas_documento_idx on public.revisoes_humanas (documento_id, criado_em desc);

-- --------------------------------------------------------------------------- metricas_entrega (§12)
create table public.metricas_entrega (
  -- Identificação
  documento_id                uuid primary key references public.documentos (id),
  usuario_id                  uuid not null references public.perfis_usuario (id),
  interno                     boolean not null default false,
  tipo                        public.tipo_documento,
  perfil_id                   text,
  template_novo               boolean not null default false,
  versao_motor                text,
  -- Opções e preço
  modo                        public.modo_saida,
  fracao_pedida               numeric(4, 3),
  docx                        boolean not null default false,
  manter_capa_elementos       boolean not null default false,
  creditos                    integer,
  -- Tamanho
  paginas_antes               integer,
  paginas_depois              integer,
  palavras_original           integer,
  palavras_saida              integer,
  fracao_real                 numeric(6, 5),
  -- Qualidade
  cobertura                   numeric(6, 5),
  gate_primeira_passagem      public.gate_cobertura,
  cobertura_primeira_passagem numeric(6, 5),
  versoes                     integer not null default 0 check (versoes >= 0),
  -- Tempo
  t_fila_s                    numeric(12, 3),
  t_processamento_s           numeric(12, 3),
  t_total_s                   numeric(12, 3),
  tempos_etapas               jsonb,
  tentativas                  integer not null default 0,
  -- Juiz (soma de todas as chamadas do documento)
  juiz_modelo                 text,
  juiz_veredito               public.veredito_juiz,
  juiz_tokens_entrada         integer not null default 0,
  juiz_tokens_saida           integer not null default 0,
  juiz_custo_usd              numeric(12, 6) not null default 0,
  -- Usuário
  nota_usuario                integer check (nota_usuario between 1 and 5),
  problemas_erro              integer not null default 0,
  problemas_formato           integer not null default 0,
  -- Humano
  intervencao_humana          boolean not null default false,
  minutos_humanos             integer not null default 0,
  motivos_intervencao         text[] not null default '{}',
  criado_em                   timestamptz not null default now(),
  atualizado_em               timestamptz not null default now()
);
alter table public.metricas_entrega enable row level security;
create index metricas_entrega_usuario_idx on public.metricas_entrega (usuario_id);

-- --------------------------------------------------------------------------- config (DT-9)
create table public.config (
  chave         text primary key,
  valor         jsonb not null,
  atualizado_em timestamptz not null default now()
);
alter table public.config enable row level security;

insert into public.config (chave, valor) values
  ('max_paginas',                  '300'),
  ('max_mb',                       '80'),
  ('parte_mb',                     '42'),     -- DT-18
  ('creditos_iniciais',            '150'),    -- FR-3
  ('juiz_fracao',                  '1.0'),    -- D17, PL5
  ('nota_baixa_max',               '3'),      -- Q6
  ('divergencia_nota_alta_min',    '4'),      -- Q6: juiz reprovado/duvidoso e nota >= 4
  ('divergencia_nota_baixa_max',   '2'),      -- Q6: juiz aprovado e nota <= 2
  ('juiz_sem_nota_horas',          '48'),     -- Q6
  ('amostra_revisao',              '0.10'),   -- Q11
  ('storage_teto_mb',              '900'),    -- DT-20
  ('juiz_modelo',                  '"claude-opus-5-5"'),  -- §11
  -- §11: preço por milhão de tokens; confirmado na unidade 6 com a skill claude-api.
  ('juiz_preco_entrada_usd_mtok',  'null'),
  ('juiz_preco_saida_usd_mtok',    'null'),
  ('juiz_max_imagens',             '12');     -- §11: teto de imagens da entrada mínima (valor inicial; a unidade 6 calibra)
