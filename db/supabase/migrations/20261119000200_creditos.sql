-- Story 3.2 — ledger de créditos e funções do saldo (spec §5 ciclo do crédito, §6 DT-7, §14).
--
-- O saldo (perfis_usuario.saldo) só muda aqui, na mesma transação do lançamento no ledger.
-- Schema private: fora de api.schemas do PostgREST e sem USAGE para anon/authenticated.
-- Funções: SECURITY DEFINER, SET search_path = '', nomes qualificados.
-- Erros: RAISE EXCEPTION com a mensagem = código que a API devolve (saldo_insuficiente, ...).

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
grant usage on schema private to docredux_backend, service_role;
alter default privileges for role postgres in schema private revoke execute on functions from public, anon, authenticated;

-- --------------------------------------------------------------------------- append-only
-- Precedente (Engram despensa-disable-trigger-...): nenhuma função desliga este gatilho;
-- o ajuste da exclusão da conta é um INSERT.
create function private.ledger_append_only() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception 'ledger_append_only'
    using detail = 'creditos_ledger não aceita UPDATE, DELETE nem TRUNCATE';
end
$$;

create trigger creditos_ledger_append_only
  before update or delete on public.creditos_ledger
  for each row execute function private.ledger_append_only();
create trigger creditos_ledger_sem_truncate
  before truncate on public.creditos_ledger
  for each statement execute function private.ledger_append_only();

-- --------------------------------------------------------------------------- auxiliares
-- Reserva aberta = lançamento `reserva` sem captura nem estorno apontando para ela.
create function private.reserva_aberta(p_documento uuid) returns bigint
language sql stable security definer set search_path = '' as $$
  select r.id
    from public.creditos_ledger r
   where r.tipo = 'reserva' and r.documento_id = p_documento
     and not exists (select 1 from public.creditos_ledger x where x.reserva_id = r.id)
   order by r.id desc
   limit 1
$$;

-- Trava a linha do perfil (FOR UPDATE) e devolve o saldo. Toda função que mexe no saldo passa aqui.
create function private._travar_perfil(p_usuario uuid) returns integer
language plpgsql security definer set search_path = '' as $$
declare
  v_saldo integer;
begin
  select u.saldo into v_saldo from public.perfis_usuario u where u.id = p_usuario for update;
  if not found then
    raise exception 'usuario_inexistente';
  end if;
  return v_saldo;
end
$$;

-- Grava o lançamento e atualiza o saldo. Supõe o perfil já travado.
create function private._lancar(
  p_usuario uuid, p_tipo public.tipo_lancamento, p_valor integer, p_documento uuid,
  p_reserva bigint, p_motivo text, p_criado_por uuid
) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
  v_saldo integer;
  v_id bigint;
begin
  update public.perfis_usuario set saldo = saldo + p_valor
   where id = p_usuario
  returning saldo into v_saldo;
  insert into public.creditos_ledger (usuario_id, tipo, valor, saldo_apos, documento_id, reserva_id, motivo, criado_por)
  values (p_usuario, p_tipo, p_valor, v_saldo, p_documento, p_reserva, p_motivo, p_criado_por)
  returning id into v_id;
  return v_id;
end
$$;

-- --------------------------------------------------------------------------- concessão e recarga
create function private.credito_conceder(
  p_usuario uuid, p_valor integer, p_tipo public.tipo_lancamento default 'concessao',
  p_motivo text default null, p_admin uuid default null
) returns bigint
language plpgsql security definer set search_path = '' as $$
begin
  if p_tipo not in ('concessao', 'recarga') then
    raise exception 'tipo_invalido' using detail = 'credito_conceder aceita concessao ou recarga';
  end if;
  if p_valor is null or p_valor <= 0 then
    raise exception 'valor_invalido';
  end if;
  perform private._travar_perfil(p_usuario);
  if exists (select 1 from public.perfis_usuario where id = p_usuario and exclusao_conta_em is not null) then
    raise exception 'conta_excluida';
  end if;
  return private._lancar(p_usuario, p_tipo, p_valor, null, null, p_motivo, p_admin);
end
$$;

-- --------------------------------------------------------------------------- reserva
-- Valor = documentos.orcamento->>'total' (gravado pela API em /confirmar, na mesma transação).
-- Reprocessar de `falhou` (§5): mesma função, com motivo e admin.
create function private.credito_reservar(
  p_documento uuid, p_motivo text default null, p_admin uuid default null
) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
  v_usuario uuid;
  v_total integer;
  v_saldo integer;
  v_id bigint;
begin
  select d.usuario_id,
         case when jsonb_typeof(d.orcamento -> 'total') = 'number' then (d.orcamento ->> 'total')::integer end
    into v_usuario, v_total
    from public.documentos d
   where d.id = p_documento
     for update;
  if not found then
    raise exception 'documento_inexistente';
  end if;
  if v_total is null or v_total <= 0 then
    raise exception 'orcamento_invalido';
  end if;
  if private.reserva_aberta(p_documento) is not null then
    raise exception 'reserva_aberta';
  end if;

  v_saldo := private._travar_perfil(v_usuario);
  if v_saldo < v_total then
    raise exception 'saldo_insuficiente' using detail = format('saldo %s, necessário %s', v_saldo, v_total);
  end if;

  v_id := private._lancar(v_usuario, 'reserva', -v_total, p_documento, null, p_motivo, p_admin);
  update public.documentos set creditos_reservados = v_total where id = p_documento;
  return v_id;
end
$$;

-- --------------------------------------------------------------------------- captura e estorno
-- Idempotentes: a segunda chamada devolve o mesmo lançamento sem mexer no saldo.
-- Índice único em reserva_id garante no máximo uma captura OU um estorno por reserva.
create function private._reserva(p_reserva bigint, out usuario_id uuid, out documento_id uuid, out valor integer)
language plpgsql security definer set search_path = '' as $$
begin
  select l.usuario_id, l.documento_id, l.valor into usuario_id, documento_id, valor
    from public.creditos_ledger l
   where l.id = p_reserva and l.tipo = 'reserva';
  if not found then
    raise exception 'reserva_inexistente';
  end if;
end
$$;

create function private.credito_capturar(p_reserva bigint) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
  r record;
  v_tipo public.tipo_lancamento;
  v_id bigint;
begin
  select * into r from private._reserva(p_reserva);
  perform private._travar_perfil(r.usuario_id);
  select l.tipo, l.id into v_tipo, v_id from public.creditos_ledger l where l.reserva_id = p_reserva;
  if v_tipo = 'captura' then
    return v_id;
  elsif v_tipo = 'estorno' then
    raise exception 'reserva_estornada';
  end if;
  return private._lancar(r.usuario_id, 'captura', 0, r.documento_id, p_reserva, 'debitado', null);
end
$$;

create function private.credito_estornar(
  p_reserva bigint, p_motivo text default null, p_admin uuid default null
) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
  r record;
  v_tipo public.tipo_lancamento;
  v_id bigint;
begin
  select * into r from private._reserva(p_reserva);
  perform private._travar_perfil(r.usuario_id);
  select l.tipo, l.id into v_tipo, v_id from public.creditos_ledger l where l.reserva_id = p_reserva;
  if v_tipo = 'estorno' then
    return v_id;
  elsif v_tipo = 'captura' then
    raise exception 'reserva_capturada';
  end if;
  return private._lancar(r.usuario_id, 'estorno', -r.valor, r.documento_id, p_reserva, p_motivo, p_admin);
end
$$;

-- --------------------------------------------------------------------------- ajuste da exclusão da conta (§14 item 4)
-- Zera o saldo com um lançamento `ajuste` (créditos grátis, sem reembolso). Nada é apagado.
-- Exige a conta marcada (exclusao_conta_em) e nenhuma reserva aberta: um estorno depois do
-- ajuste deixaria saldo numa conta excluída. Saldo já zero: nada a lançar (devolve null).
create function private.credito_ajuste_exclusao(p_usuario uuid, p_admin uuid default null) returns bigint
language plpgsql security definer set search_path = '' as $$
declare
  v_saldo integer;
begin
  v_saldo := private._travar_perfil(p_usuario);
  if not exists (select 1 from public.perfis_usuario where id = p_usuario and exclusao_conta_em is not null) then
    raise exception 'conta_nao_excluida';
  end if;
  if exists (
    select 1 from public.creditos_ledger r
     where r.usuario_id = p_usuario and r.tipo = 'reserva'
       and not exists (select 1 from public.creditos_ledger x where x.reserva_id = r.id)
  ) then
    raise exception 'reserva_aberta';
  end if;
  if v_saldo = 0 then
    return null;
  end if;
  return private._lancar(p_usuario, 'ajuste', -v_saldo, null, null, 'exclusão da conta', p_admin);
end
$$;

-- --------------------------------------------------------------------------- invariantes (§6, §16 FR-9)
-- Vazio = tudo certo. Usado pelos testes da 3.2 e da unidade 4 depois de cada caminho.
create function private.invariantes_credito()
returns table (usuario_id uuid, documento_id uuid, problema text)
language sql stable security definer set search_path = '' as $$
  select u.id, null::uuid, format('saldo %s <> soma do ledger %s', u.saldo, coalesce(s.soma, 0))
    from public.perfis_usuario u
    left join (select l.usuario_id, sum(l.valor) as soma from public.creditos_ledger l group by l.usuario_id) s
      on s.usuario_id = u.id
   where u.saldo <> coalesce(s.soma, 0)
  union all
  select r.usuario_id, r.documento_id, format('reserva %s aberta em documento %s', r.id, d.status)
    from public.creditos_ledger r
    join public.documentos d on d.id = r.documento_id
   where r.tipo = 'reserva'
     and not exists (select 1 from public.creditos_ledger x where x.reserva_id = r.id)
     and d.status in ('aguardando_confirmacao', 'recusado', 'upload_falhou', 'pronto',
                      'falhou', 'cancelado', 'excluido', 'expirado')
  union all
  select x.usuario_id, x.documento_id, format('%s %s aponta lançamento %s que não é reserva', x.tipo, x.id, x.reserva_id)
    from public.creditos_ledger x
    join public.creditos_ledger r on r.id = x.reserva_id
   where r.tipo <> 'reserva' or r.usuario_id <> x.usuario_id
$$;

-- --------------------------------------------------------------------------- usuário novo (FR-3, DT-19)
-- Cria perfis_usuario (senha_provisoria = true) e concede os créditos iniciais.
-- config_num devolve 150 quando a chave falta ou é inválida: um config incompleto não derruba o
-- convite ("Database error saving new user", §6).
create function private.novo_usuario() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
  v_creditos integer;
begin
  insert into public.perfis_usuario (id, email, senha_provisoria) values (new.id, new.email, true);
  v_creditos := public.config_num('creditos_iniciais', 150)::integer;
  if v_creditos > 0 then
    perform private.credito_conceder(new.id, v_creditos, 'concessao', 'créditos iniciais do piloto', null);
  end if;
  return new;
end
$$;

create trigger docredux_novo_usuario
  after insert on auth.users
  for each row execute function private.novo_usuario();

-- --------------------------------------------------------------------------- permissões
revoke execute on all functions in schema private from public, anon, authenticated;
grant execute on function
  private.credito_conceder(uuid, integer, public.tipo_lancamento, text, uuid),
  private.credito_reservar(uuid, text, uuid),
  private.credito_capturar(bigint),
  private.credito_estornar(bigint, text, uuid),
  private.credito_ajuste_exclusao(uuid, uuid),
  private.reserva_aberta(uuid),
  private.invariantes_credito()
  to docredux_backend, service_role;
