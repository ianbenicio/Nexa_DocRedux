"""Jobs `triagem` e `processar` (spec §7, stories 4.4 e 4.7). O juiz é da unidade 6.

Cada job: lê o documento; se a exclusão foi pedida, conclui e sai. Baixa o original (partes concatenadas,
tamanho e SHA-256 conferidos), chama o motor em subprocesso e, numa transação final com o documento
travado, confere a exclusão de novo antes de gravar. Os avisos por e-mail saem depois do commit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app import ciclo, notificacoes, supabase
from app.db import config_valor

from . import motor
from .falhas import FalhaDefinitiva, concluir_exclusao, fechar_job, pos_exclusao, travar_doc

log = logging.getLogger("docredux.worker")


@dataclass
class Contexto:
    conn: psycopg.Connection
    worker_id: str
    job: dict
    cache: Path                       # cache local do original, por documento (§7)
    timeouts: dict[str, float]
    email: notificacoes.ClienteEmail | None = None
    avisos: list[tuple[UUID, str]] = field(default_factory=list)

    @property
    def doc_id(self) -> UUID:
        return self.job["documento_id"]

    def pulso(self) -> bool:
        """Heartbeat do job (§7). False → o job não é mais deste worker."""
        row = self.conn.execute(
            """update public.jobs set heartbeat_em = now()
                where id = %s and worker_id = %s and status = 'processando' returning id""",
            (self.job["id"], self.worker_id)).fetchone()
        self.conn.execute("update public.workers set visto_em = now() where worker_id = %s", (self.worker_id,))
        return row is not None


def _ler_doc(conn, doc_id) -> dict:
    return conn.execute("select *, expira_em <= now() as expirou from public.documentos where id = %s",
                        (doc_id,)).fetchone()


def _encerrar_se_excluido(ctx: Contexto) -> bool:
    """Exclusão pedida (§7): fecha o job, conclui a exclusão e devolve True."""
    conta = None
    with ctx.conn.transaction():
        doc = travar_doc(ctx.conn, ctx.doc_id)
        if not ciclo.exclusao_marcada(doc):
            return False
        fechar_job(ctx.conn, ctx.job["id"], "cancelado", "exclusão do documento")
        if doc["status"] not in ciclo.ESTADOS_APAGADOS:
            conta = concluir_exclusao(ctx.conn, doc)
    pos_exclusao(conta)
    _limpar_cache(ctx)
    return True


def _pasta_doc(ctx: Contexto) -> Path:
    p = ctx.cache / str(ctx.doc_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _limpar_cache(ctx: Contexto) -> None:
    shutil.rmtree(ctx.cache / str(ctx.doc_id), ignore_errors=True)


def obter_original(ctx: Contexto, doc: dict) -> tuple[Path, str]:
    """Baixa as partes (DT-18), concatena e confere tamanho e SHA-256. Usa o cache do documento."""
    pasta = _pasta_doc(ctx)
    destino = pasta / "original.pdf"
    marca = pasta / "original.sha256"
    if destino.exists() and marca.exists() and destino.stat().st_size == doc["tamanho_bytes"]:
        return destino, marca.read_text().strip()
    h = hashlib.sha256()
    tmp = pasta / "original.pdf.tmp"
    base = f"{doc['usuario_id']}/{doc['id']}"
    with open(tmp, "wb") as out:
        for i in range(1, doc["partes"] + 1):
            parte = pasta / f"part{i:02d}"
            try:
                supabase.baixar(supabase.ORIGINAIS, f"{base}/original.part{i:02d}", parte)
            except supabase.SupabaseErro as e:
                if "HTTP 400" in str(e) or "HTTP 404" in str(e):
                    raise FalhaDefinitiva(f"parte {i} ausente no Storage") from e
                raise
            with open(parte, "rb") as f:
                for bloco in iter(lambda: f.read(1 << 20), b""):
                    h.update(bloco)
                    out.write(bloco)
            parte.unlink()
            if not ctx.pulso():
                raise motor.JobPerdido()
    tamanho = tmp.stat().st_size
    if tamanho != doc["tamanho_bytes"]:
        tmp.unlink()
        raise FalhaDefinitiva(f"tamanho remontado {tamanho} ≠ {doc['tamanho_bytes']}")
    sha = h.hexdigest()
    if doc["sha256"] and doc["sha256"] != sha:
        tmp.unlink()
        raise FalhaDefinitiva("SHA-256 do original remontado diverge do informado no envio")
    tmp.replace(destino)
    marca.write_text(sha)
    return destino, sha


def perfis_visiveis(conn, usuario_id) -> list[dict]:
    """Sementes (dono nulo) + perfis privados da conta (D2, Q5), ativos."""
    return conn.execute(
        """select id, tipo::text, json from public.perfis_template
            where ativo and (dono is null or dono = %s) order by id""", (usuario_id,)).fetchall()


def _escrever_perfis(pasta: Path, perfis: list[dict]) -> dict[str, str]:
    """Grava cada perfil como `{id}.json`; devolve {nome do perfil no motor: id no banco}."""
    pasta.mkdir(parents=True, exist_ok=True)
    nomes = {}
    for p in perfis:
        (pasta / f"{p['id']}.json").write_text(json.dumps(p["json"], ensure_ascii=False), encoding="utf-8")
        nomes[p["json"].get("nome", p["id"])] = p["id"]
    return nomes


# ---------------------------------------------------------------- triagem
def triagem(ctx: Contexto) -> None:
    if _encerrar_se_excluido(ctx):
        return
    conn = ctx.conn
    doc = _ler_doc(conn, ctx.doc_id)
    original, sha = obter_original(ctx, doc)
    pasta = _pasta_doc(ctx) / f"triagem-{ctx.job['id']}"
    nomes = _escrever_perfis(pasta / "perfis", perfis_visiveis(conn, doc["usuario_id"]))
    max_paginas = int(config_valor(conn, "max_paginas", 300))
    max_mb = int(config_valor(conn, "max_mb", 80))
    t = motor.executar(["triar", str(original), "--perfis-dir", str(pasta / "perfis"), "--max-paginas",
                        str(max_paginas), "--max-mb", str(max_mb)],
                       timeout_s=ctx.timeouts["triagem"], pulso=ctx.pulso, cwd=pasta)
    conta = None
    with conn.transaction():
        doc = travar_doc(conn, ctx.doc_id)
        fechar_job(conn, ctx.job["id"], "concluido")
        if ciclo.exclusao_marcada(doc):
            if doc["status"] not in ciclo.ESTADOS_APAGADOS:
                conta = concluir_exclusao(conn, doc)
        else:
            perfil_id = nomes.get(t["perfil_id"]) if t["perfil_id"] else None
            recusa = t["recusa"] or (None if perfil_id else "template_novo")
            if recusa:
                status, motivo = "recusado", recusa
            else:
                status, motivo = "aguardando_confirmacao", None
            conn.execute(
                """update public.documentos set status = %s, motivo_recusa = %s, paginas = %s, palavras = %s,
                          tipo = %s, perfil_id = %s, sha256 = coalesce(sha256, %s)
                    where id = %s""",
                (status, motivo, t["paginas"], t["palavras"], t["tipo"], perfil_id, sha, ctx.doc_id))
            if recusa == "template_novo":
                ctx.avisos.append((ctx.doc_id, "em_revisao"))  # Q3: prazo de 2 meses no texto (4.7)
            log.info("triagem do documento %s: %s", ctx.doc_id, recusa or status)
    shutil.rmtree(pasta, ignore_errors=True)
    if conta is not None or t.get("recusa"):
        _limpar_cache(ctx)
    pos_exclusao(conta)


# ---------------------------------------------------------------- processar
def _args_processar(original: Path, saida: Path, perfil_json: Path, opcoes: dict) -> list[str]:
    args = ["processar", str(original), str(saida), "--perfil", str(perfil_json), "--modo", opcoes.get("modo", "original")]
    if opcoes.get("modo") == "sintese":
        args += ["--fracao", str(opcoes["fracao"])]
    if opcoes.get("manter_capa_elementos"):
        args.append("--manter-capa-elementos")
    if opcoes.get("docx"):
        args.append("--docx")
    return args


def processar(ctx: Contexto) -> None:
    if _encerrar_se_excluido(ctx):
        return
    conn = ctx.conn
    doc = _ler_doc(conn, ctx.doc_id)
    original, _ = obter_original(ctx, doc)
    perfil = conn.execute("select json from public.perfis_template where id = %s", (doc["perfil_id"],)).fetchone()
    if perfil is None:
        raise FalhaDefinitiva(f"perfil {doc['perfil_id']!r} não existe")
    pasta = _pasta_doc(ctx) / f"processar-{ctx.job['id']}"
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir(parents=True)
    perfil_json = pasta / "perfil.json"
    perfil_json.write_text(json.dumps(perfil["json"], ensure_ascii=False), encoding="utf-8")
    inicio = time.monotonic()
    try:
        r = motor.executar(_args_processar(original, pasta / "saida", perfil_json, doc["opcoes"] or {}),
                           timeout_s=ctx.timeouts["processar"], pulso=ctx.pulso, cwd=pasta)
    except motor.MotorFalhou as e:
        if e.motivo:  # PDF recusado no processar: nova tentativa não muda nada
            raise FalhaDefinitiva(str(e)) from e
        raise
    duracao = time.monotonic() - inicio
    if _encerrar_se_excluido(ctx):  # §7: nada sobe se a exclusão foi pedida durante o motor
        shutil.rmtree(pasta, ignore_errors=True)
        return
    rel = r["relatorio"]
    versao = conn.execute("select coalesce(max(versao), 0) + 1 as v from public.entregas where documento_id = %s",
                          (ctx.doc_id,)).fetchone()["v"]
    base = f"{doc['usuario_id']}/{doc['id']}/v{versao}"
    caminho_pdf = f"{base}/saida.pdf"
    supabase.subir(supabase.SAIDAS, caminho_pdf, Path(r["pdf"]), "application/pdf")
    caminho_docx = None
    if r.get("docx"):
        caminho_docx = f"{base}/saida.docx"
        supabase.subir(supabase.SAIDAS, caminho_docx, Path(r["docx"]),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    tamanho_saidas = Path(r["pdf"]).stat().st_size + (Path(r["docx"]).stat().st_size if r.get("docx") else 0)
    if not ctx.pulso():
        raise motor.JobPerdido()

    conta = None
    with conn.transaction():
        doc = travar_doc(conn, ctx.doc_id)
        job = conn.execute("select * from public.jobs where id = %s for update", (ctx.job["id"],)).fetchone()
        if job["status"] != "processando" or job["worker_id"] != ctx.worker_id:
            raise motor.JobPerdido()
        if ciclo.exclusao_marcada(doc):
            fechar_job(conn, job["id"], "cancelado", "exclusão do documento")
            if doc["status"] not in ciclo.ESTADOS_APAGADOS:
                conta = concluir_exclusao(conn, doc)  # apaga também as saídas que acabaram de subir
        else:
            _gravar_entrega(conn, ctx, doc, job, versao, rel, caminho_pdf, caminho_docx, tamanho_saidas, duracao)
            fechar_job(conn, job["id"], "concluido")
    shutil.rmtree(pasta, ignore_errors=True)
    _limpar_cache(ctx)
    pos_exclusao(conta)


def _gravar_entrega(conn, ctx: Contexto, doc, job, versao, rel, caminho_pdf, caminho_docx, tamanho_saidas, duracao):
    gate = rel["gate"]
    conn.execute(
        """insert into public.entregas (documento_id, versao, job_id, caminho_pdf, caminho_docx, relatorio, cobertura,
                                         paginas_depois, gate, tamanho_bytes)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (doc["id"], versao, job["id"], caminho_pdf, caminho_docx, Jsonb(rel), rel.get("cobertura"),
         rel["paginas_depois"], gate, tamanho_saidas))
    de_pronto = job["origem"] == "reprocessar" and job["estado_origem"] == "pronto"
    if de_pronto:
        # §5: de `pronto`, o status não muda; gate ok → a versão nova vira a atual, sem captura nova
        if gate == "ok":
            conn.execute("update public.documentos set entrega_atual = %s, reprocessando = false where id = %s",
                         (versao, doc["id"]))
        else:
            conn.execute("update public.documentos set reprocessando = false where id = %s", (doc["id"],))
    elif gate == "ok":
        reserva = conn.execute("select private.reserva_aberta(%s) as r", (doc["id"],)).fetchone()["r"]
        if reserva is not None:  # captura só na primeira entrega (a reserva fica aberta até ela)
            conn.execute("select private.credito_capturar(%s)", (reserva,))
        conn.execute(
            """update public.documentos set status = 'pronto', entrega_atual = %s, reprocessando = false,
                      entregue_em = coalesce(entregue_em, now()) where id = %s""", (versao, doc["id"]))
        ctx.avisos.append((doc["id"], "pronto"))
    else:
        # gate bloqueado: em_revisao, sem captura, entrega invisível ao usuário (§9 só mostra em `pronto`)
        conn.execute("""update public.documentos set status = 'em_revisao', entrega_atual = %s, reprocessando = false
                         where id = %s""", (versao, doc["id"]))
        ctx.avisos.append((doc["id"], "em_revisao"))
    conn.execute(
        """insert into public.jobs (documento_id, tipo) values (%s, 'juiz')
           on conflict (documento_id, tipo) where status in ('na_fila', 'processando') do nothing""", (doc["id"],))
    conn.execute(
        """update public.metricas_entrega m set
              versao_motor = %(versao_motor)s, paginas_depois = %(paginas_depois)s,
              palavras_original = coalesce(%(palavras_original)s, m.palavras_original),
              palavras_saida = %(palavras_saida)s, fracao_real = %(fracao_real)s, cobertura = %(cobertura)s,
              gate_primeira_passagem = coalesce(m.gate_primeira_passagem, %(gate)s::public.gate_cobertura),
              cobertura_primeira_passagem = case when m.gate_primeira_passagem is null then %(cobertura)s
                                                 else m.cobertura_primeira_passagem end,
              versoes = m.versoes + 1, tentativas = %(tentativas)s,
              t_fila_s = coalesce(m.t_fila_s, extract(epoch from (j.iniciado_em - d.confirmado_em))),
              t_processamento_s = %(duracao)s,
              t_total_s = coalesce(m.t_total_s, extract(epoch from (now() - d.confirmado_em))),
              tempos_etapas = %(tempos)s, atualizado_em = now()
             from public.documentos d, public.jobs j
            where m.documento_id = %(doc)s and d.id = m.documento_id and j.id = %(job)s""",
        {"versao_motor": rel.get("versao_motor"), "paginas_depois": rel["paginas_depois"],
         "palavras_original": rel.get("palavras_original"), "palavras_saida": rel.get("palavras_saida"),
         "fracao_real": rel.get("fracao_real"), "cobertura": rel.get("cobertura"), "gate": gate,
         "tentativas": job["tentativas"], "duracao": round(duracao, 3), "tempos": Jsonb(rel.get("tempos_s") or {}),
         "doc": doc["id"], "job": job["id"]})
    log.info("documento %s v%s: gate %s", doc["id"], versao, gate)


EXECUTORES: dict[str, Callable[[Contexto], None]] = {"triagem": triagem, "processar": processar}


def enviar_avisos(ctx: Contexto) -> None:
    for doc_id, evento in ctx.avisos:
        notificacoes.notificar(ctx.conn, doc_id, evento, ctx.email)
    ctx.avisos.clear()


POOL_LIBREOFFICE = "_libreoffice"  # pasta do cache que não é de documento (a limpeza pula "_*")


def cache_padrao() -> Path:
    return Path(os.environ.get("WORKER_CACHE") or (Path(tempfile.gettempdir()) / "docredux-cache"))
