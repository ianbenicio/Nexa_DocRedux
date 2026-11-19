"""Contrato público do motor (spec §4): triar, processar, folha_contato, validar_perfil.

Biblioteca sem rede e sem LLM (D16, CON-6). Reprodutível: a mesma entrada com as mesmas opções gera as
mesmas páginas, a mesma cobertura e o mesmo relatório (o PDF muda só na data gravada pelo LibreOffice).
"""
from __future__ import annotations

import collections
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Literal

import pymupdf

from . import __version__, pipeline
from . import perfil as _perfil
from .config import PERFIL_DIR
from .ficha.extract import escolher_perfil, load_perfil
from .folha import folha as _folha
from .soffice import SofficeNaoEncontrado

GATE_COBERTURA = 0.995          # FR-12: abaixo disso a entrega é bloqueada (modo original)
FRACOES = (0.20, 0.35, 0.50)    # U-B
MIN_PALAVRAS_PAGINA = 5         # página "com texto" na triagem
MIN_FRACAO_PAGINAS_TEXTO = 0.2  # abaixo disso o PDF é tratado como escaneado (sem camada de texto)


# ---------------------------------------------------------------- tipos do contrato
@dataclass(frozen=True)
class Opcoes:
    modo: Literal["original", "sintese"] = "original"     # U-B
    fracao: float | None = None                            # 0.20 | 0.35 | 0.50; obrigatória se modo == "sintese"
    manter_capa_elementos: bool = False                    # U-C: desliga as regras de remoção correspondentes
    docx: bool = False                                     # U-E: o PDF é sempre gerado (D9)


@dataclass(frozen=True)
class Triagem:
    paginas: int
    palavras: int
    tamanho_bytes: int
    tipo: Literal["ficha", "relatorio", "desconhecido"]
    perfil_id: str | None
    recusa: Literal["escaneado", "protegido", "corrompido", "excede_paginas", "excede_tamanho"] | None
    sintese_disponivel: bool

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Modificacao:
    tipo: Literal["removido", "reorganizado", "sintetizado"]
    descricao: str
    paginas_original: list[int]
    pagina_saida: int | None


@dataclass(frozen=True)
class Relatorio:
    versao_motor: str
    perfil_id: str
    paginas_antes: int
    paginas_depois: int
    palavras_original: int
    palavras_saida: int
    cobertura: float | None
    fracao_real: float | None
    condensado: list[str]
    gate: Literal["ok", "bloqueado"]
    motivo_gate: str | None
    estrutura: dict[str, int]
    modificacoes: list[Modificacao]
    regras_disparadas: list[str]
    tempos_s: dict[str, float]
    avisos: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def to_json(self, **kw):
        return json.dumps(self.to_dict(), ensure_ascii=False, **kw)


@dataclass(frozen=True)
class Resultado:
    pdf: Path
    docx: Path | None
    relatorio: Relatorio

    def to_dict(self):
        return {"pdf": str(self.pdf), "docx": str(self.docx) if self.docx else None,
                "relatorio": self.relatorio.to_dict()}


class MotorErro(Exception):
    pass


class PdfRecusado(MotorErro):
    def __init__(self, motivo: str, mensagem: str | None = None):
        super().__init__(mensagem or motivo)
        self.motivo = motivo


class PerfilInvalido(MotorErro):
    def __init__(self, erros: list[str]):
        super().__init__("; ".join(erros))
        self.erros = erros


class RenderFalhou(MotorErro):  # LibreOffice
    pass


# ---------------------------------------------------------------- perfis
def carregar_perfis(diretorio: Path | None = None) -> list[dict]:
    """Perfis semente (engine/perfis/ ou DOCREDUX_PERFIS), em ordem de nome (a mesma da assinatura, R33)."""
    d = Path(diretorio) if diretorio else PERFIL_DIR
    return [load_perfil(f) for f in sorted(d.glob("*.json"))]


def validar_perfil(perfil: dict) -> list[str]:
    """Erros de schema (perfil.schema.json, derivado de perfis/*.json); [] = válido."""
    return _perfil.validar(perfil)


# ---------------------------------------------------------------- triagem
def _palavras(doc) -> tuple[int, int]:
    """(palavras, páginas com texto). Levanta exceção se alguma página não puder ser lida."""
    total = com_texto = 0
    for p in doc:
        n = len(p.get_text("words"))
        total += n
        com_texto += n >= MIN_PALAVRAS_PAGINA
    return total, com_texto


def contar_palavras(pdf: Path, perfil: dict, *, modelo: dict | None = None) -> int:
    """DT-21: palavras do original contadas como o verify conta (extração + contador do original).

    É a contagem que entra no preço da síntese e em `palavras_original` do modo original: o mesmo
    texto que a cobertura mede, sem capa, páginas removidas, cabeçalhos e rodapés repetidos.
    Opções padrão (sem manter capa e elementos). Custa uma extração completa (ex. 05: ~50 s).
    Se `modelo` for informado, reaproveita o modelo original ja extraido, antes da sintese.
    """
    import tempfile

    from .ficha import extract as ficha_extract
    from .verify import ficha as verify_ficha
    from .verify import relatorio as verify_relatorio

    pdf = Path(pdf)
    if modelo is None:
        with tempfile.TemporaryDirectory(prefix="docredux_contagem_") as d:
            ficha_extract.extrair(pdf, Path(d), perfil)
            modelo = json.loads((Path(d) / "modelo.json").read_text(encoding="utf-8"))
    model = modelo
    meta = model["meta"]
    fora = {**meta["removidas"], **{str(k): v for k, v in meta.get("paginas_imagem", {}).items()}}
    if meta.get("tipo") == "relatorio":
        linhas, _ = verify_relatorio.orig_lines(pdf, perfil, fora, meta["recorte"], meta.get("sumario_linhas", []))
        return int(sum(verify_relatorio.orig_counter(linhas).values()))
    figs = [(el["pagina"], pymupdf.Rect(el["bbox"])) for el in model["elementos"] if el.get("figura")]
    contagem, _ = verify_ficha.original_words(pdf, fora, figs)
    return int(sum(contagem.values()))


def triar(pdf: Path, perfis: list[dict], *, max_paginas: int = 300, max_bytes: int = 80 * 2**20,
          contagem_verify: bool = True) -> Triagem:
    """Recusa o que o motor não processa e casa tipo e perfil pela assinatura de fontes (R33).
    Template desconhecido: tipo "desconhecido", perfil_id None (Q3: vira pedido de perfil).

    Documento aceito com perfil: `palavras` vem de `contar_palavras` (DT-21). Recusado ou sem perfil:
    contagem simples do PDF inteiro (não há preço a calcular). `contagem_verify=False` força a simples."""
    pdf = Path(pdf)
    tamanho = pdf.stat().st_size

    def resultado(recusa, paginas=0, palavras=0, perfil=None):
        tipo = perfil["tipo"] if perfil else "desconhecido"
        return Triagem(paginas=paginas, palavras=palavras, tamanho_bytes=tamanho, tipo=tipo,
                       perfil_id=perfil["nome"] if perfil else None, recusa=recusa,
                       sintese_disponivel=recusa is None and tipo == "relatorio")  # Q4

    if tamanho > max_bytes:
        return resultado("excede_tamanho")
    try:
        doc = pymupdf.open(pdf)
    except Exception:  # noqa: BLE001 — qualquer falha de abertura é PDF corrompido
        return resultado("corrompido")
    with doc:
        if doc.needs_pass:
            return resultado("protegido", doc.page_count)
        if not doc.is_pdf or doc.page_count == 0:
            return resultado("corrompido")
        try:
            palavras, com_texto = _palavras(doc)
            perfil = escolher_perfil(doc, perfis)
        except Exception:  # noqa: BLE001
            return resultado("corrompido", doc.page_count)
        paginas = doc.page_count
    if com_texto < max(1, MIN_FRACAO_PAGINAS_TEXTO * paginas):
        return resultado("escaneado", paginas, palavras)
    if paginas > max_paginas:
        return resultado("excede_paginas", paginas, palavras, perfil)
    if perfil is not None and contagem_verify:
        palavras = contar_palavras(pdf, perfil)
    return resultado(None, paginas, palavras, perfil)


# ---------------------------------------------------------------- relatório de modificações
_MOTIVO = {
    "capa": "capa", "contracapa": "contracapa", "branca": "página em branco",
    "foto": "foto decorativa de página inteira", "foto_ou_arte": "arte decorativa de página inteira",
    "sem_conteudo": "página sem conteúdo (cor lisa ou vazia)",
}


def _plural(n, s, p):
    return f"{n} {s if n == 1 else p}"


def _modificacoes(meta, model, verificacao) -> list[Modificacao]:
    mods = []
    por_motivo = collections.defaultdict(list)
    for pag, motivo in meta.get("removidas", {}).items():
        por_motivo[motivo].append(int(pag))
    for motivo, pags in sorted(por_motivo.items(), key=lambda kv: kv[1][0]):
        rot = _MOTIVO.get(motivo, motivo)
        mods.append(Modificacao("removido", f"{_plural(len(pags), 'página removida', 'páginas removidas')}: {rot}",
                                sorted(pags), None))
    st = meta.get("stats", {})
    if meta.get("paginas_imagem"):  # manter_capa_elementos (story 2.5)
        pags = sorted(int(p) for p in meta["paginas_imagem"])
        mods.append(Modificacao("reorganizado", f"{_plural(len(pags), 'página mantida', 'páginas mantidas')} como imagem de página inteira (capa, fotos ou diagramas)", pags, None))
    if meta.get("manter_capa_elementos") and meta.get("cabecalho"):
        mods.append(Modificacao("reorganizado", "cabeçalho corrido do original repetido no cabeçalho de todas as páginas", [], None))
    elif st.get("descartado_rodape") or st.get("descartado_cabecalho_corrido"):
        mods.append(Modificacao("removido", "rodapé, número de página e cabeçalho corrido repetidos removidos", [], None))
    if st.get("desenhos_decorativos_mantidos"):
        mods.append(Modificacao("reorganizado", f"{_plural(st['desenhos_decorativos_mantidos'], 'elemento gráfico decorativo mantido', 'elementos gráficos decorativos mantidos')} como figura", [], None))
    if st.get("desenhos_decorativos"):
        mods.append(Modificacao("removido", f"{_plural(st['desenhos_decorativos'], 'elemento gráfico decorativo removido', 'elementos gráficos decorativos removidos')}", [], None))
    els = model["elementos"]
    linhas = (verificacao or {}).get("linhas_resposta")
    if linhas and linhas.get("original"):
        mods.append(Modificacao("reorganizado", f"linhas de resposta reduzidas de {linhas['original']} para {linhas['saida']}", [], None))
    if st.get("paragrafos_fundidos"):
        mods.append(Modificacao("reorganizado", f"{_plural(st['paragrafos_fundidos'], 'parágrafo de texto corrido unido', 'parágrafos de texto corrido unidos')} ao anterior", [], None))
    if any(e["t"] == "toc" for e in els):
        mods.append(Modificacao("reorganizado", "sumário regenerado com a paginação nova", [], None))
    if st.get("notas"):
        mods.append(Modificacao("reorganizado", f"{_plural(st['notas'], 'nota de rodapé movida', 'notas de rodapé movidas')} para o fim do capítulo", [], None))
    if st.get("siglas"):
        mods.append(Modificacao("reorganizado", f"lista de siglas compactada em tabela ({st['siglas']} siglas)", [], None))
    if any(e["t"] == "id" for e in els) and meta.get("removidas"):
        mods.append(Modificacao("reorganizado", "identificação do documento num bloco de texto no início", [], 1))
    return mods


def _regras(meta, model, perfil) -> list[str]:
    st = meta.get("stats", {})
    rem = set(meta.get("removidas", {}).values())
    tipos = collections.Counter(e["t"] for e in model["elementos"])
    r = {"R32", "R33", "R6", "R9", "R13"}
    if perfil["tipo"] == "ficha":
        cond = {"R1": rem & {"capa", "contracapa"}, "R11": "branca" in rem,
                "R12": st.get("descartado_rodape") or st.get("descartado_cabecalho_corrido"),
                "R10": tipos["lines"], "R4": tipos["opt"], "R16": st.get("paragrafos_fundidos"),
                "R14": st.get("quadros"), "R26": st.get("malhas"), "R21": st.get("figuras_vetoriais"),
                "R22": st.get("tabelas_figura"), "R27": st.get("lacunas"), "R28": st.get("composicoes_em_linha"),
                "R7": st.get("imagens"), "R5": st.get("desenhos_decorativos")}
    else:
        cond = {"R34": tipos["p"], "R35": tipos["p"], "R36": tipos["p"], "R37": tipos["fig"],
                "R38": st.get("imagens"), "R39": st.get("imagens"), "R40": st.get("notas"), "R41": tipos["ref"],
                "R42": tipos["toc"], "R43": st.get("siglas"), "R44": rem & {"foto", "foto_ou_arte", "sem_conteudo"},
                "R45": tipos["h2"], "R47": True, "R49": tipos["front"], "R50": st.get("tabelas_grade_texto"),
                "R12": st.get("descartado_rodape")}
    r.update(k for k, v in cond.items() if v)
    return sorted(r, key=lambda x: int(re.sub(r"\D", "", x)))


def _estrutura(meta, model) -> dict[str, int]:
    els = model["elementos"]
    st = meta.get("stats", {})
    tipos = collections.Counter(e["t"] for e in els)
    e = {
        "capitulos": tipos["h1"], "secoes": tipos["h2"], "questoes": tipos["q"],
        "legendas": sum(1 for x in els if x["t"] == "fig" and x.get("caption")),
        "figuras": tipos["fig"], "imagens": st.get("imagens", 0), "tabelas": st.get("tabelas", 0),
        "notas": sum(len(x["itens"]) for x in els if x["t"] == "notas"), "referencias": tipos["ref"],
        "siglas": st.get("siglas", 0), "paginas_removidas": len(meta.get("removidas", {})),
    }
    return {k: int(v) for k, v in e.items() if v}


def _palavras_docx(docx) -> int:
    from .verify.ficha import docx_words
    return sum(docx_words(docx).values())


# ---------------------------------------------------------------- processar
def _checar(opcoes: Opcoes, perfil: dict):
    erros = validar_perfil(perfil)
    if erros:
        raise PerfilInvalido(erros)
    if opcoes.modo not in ("original", "sintese"):
        raise MotorErro(f"modo inválido: {opcoes.modo!r}")
    if opcoes.modo == "sintese":
        if perfil["tipo"] != "relatorio":  # Q4: síntese só para relatório
            raise MotorErro("síntese só está disponível para relatórios (Q4)")
        if (not isinstance(opcoes.fracao, (int, float)) or isinstance(opcoes.fracao, bool)
                or not math.isfinite(opcoes.fracao) or opcoes.fracao not in FRACOES):
            raise MotorErro(f"fração da síntese deve ser uma de {FRACOES}")
    elif opcoes.fracao is not None:
        raise MotorErro("fração só vale no modo síntese")


def processar(pdf: Path, opcoes: Opcoes, perfil: dict, saida_dir: Path, *,
              ao_progresso: Callable[[str], None] | None = None) -> Resultado:
    """Despacha pelo perfil['tipo'] (ficha | relatorio) e devolve PDF, DOCX (se pedido) e o relatório."""
    _checar(opcoes, perfil)
    pdf, saida_dir = Path(pdf), Path(saida_dir)
    transformar = None
    if opcoes.modo == "sintese":
        from .sintese import documento as sintese_doc
        estado = {}

        def transformar(model):  # noqa: F811 — aplicada entre a extração e o render
            estado["modelo_original"] = model
            novo, rel = sintese_doc.sintetizar(model, opcoes.fracao)
            estado["rel"] = rel
            (saida_dir / "modelo_sintese.json").write_text(  # trilha de auditoria: o que foi para o render
                json.dumps(novo, ensure_ascii=False, indent=1), encoding="utf-8")
            return novo
    try:
        res = pipeline.rodar(pdf, saida_dir, perfil, transformar=transformar,
                             verificar=opcoes.modo == "original", ao_progresso=ao_progresso,
                             manter_capa_elementos=opcoes.manter_capa_elementos)
    except (SofficeNaoEncontrado, RuntimeError) as e:
        raise RenderFalhou(str(e)) from e
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as e:
        raise PdfRecusado("corrompido", str(e)) from e

    meta, model, v = res["meta"], res["modelo"], res["verificacao"]
    avisos, condensado, fracao_real = [], [], None
    mods = _modificacoes(meta, model, v)
    if opcoes.modo == "original":
        cobertura = v["cobertura_texto_pct"] / 100
        palavras_original = v["palavras_original"]
        motivos = []
        if cobertura < GATE_COBERTURA:
            motivos.append(f"cobertura {cobertura * 100:.2f}% < {GATE_COBERTURA * 100:.1f}%".replace(".", ","))
        num = v.get("numeros")
        if num is not None and not num["ok"]:  # M09, story 2.7: número ou sinal do original ausente na saída
            itens = ", ".join(k.replace("op:", "operador ") + (f" (×{n})" if n > 1 else "")
                              for k, n in list(num["faltando"].items())[:5])
            motivos.append(f"números do original ausentes na saída: {itens}")
        gate, motivo = ("bloqueado", "; ".join(motivos)) if motivos else ("ok", None)
        if v.get("estrutura_ok") is False:
            avisos.append("verificação estrutural divergente (legendas, Fonte, notas ou capítulos)")
    else:  # FR-13: sem gate de cobertura na síntese; vale o relatório de síntese
        rel = estado["rel"]
        cobertura, gate, motivo = None, "ok", None
        palavras_original = contar_palavras(pdf, perfil, modelo=estado["modelo_original"])
        fracao_real = rel["fracao_real"]
        condensado = rel["condensado"]
        mods.append(Modificacao("sintetizado", rel["descricao"], [], None))
    if any(e["t"] == "toc" for e in model["elementos"]) and not res["render"].get("toc_paginas"):
        avisos.append("sumário sem números de página (LibreOffice não devolveu a paginação)")

    relatorio = Relatorio(
        versao_motor=__version__, perfil_id=perfil["nome"],
        paginas_antes=meta["paginas"], paginas_depois=res["paginas_pdf"],
        palavras_original=int(palavras_original), palavras_saida=_palavras_docx(res["docx"]),
        cobertura=cobertura, fracao_real=fracao_real, condensado=condensado,
        gate=gate, motivo_gate=motivo, estrutura=_estrutura(meta, model), modificacoes=mods,
        regras_disparadas=_regras(meta, model, perfil), tempos_s=res["tempos_s"], avisos=avisos,
    )
    (saida_dir / "relatorio.json").write_text(relatorio.to_json(indent=1), encoding="utf-8")
    return Resultado(pdf=res["pdf"], docx=res["docx"] if opcoes.docx else None, relatorio=relatorio)


# ---------------------------------------------------------------- folha de contato
def folha_contato(pdf: Path, saida_dir: Path, *, dpi: int = 60, paginas: list[int] | None = None) -> list[Path]:
    """Folha de contato (8 colunas) do PDF, para QA visual (FR-14). `paginas` 1-based; None = todas."""
    return _folha(pdf, saida_dir, "folha", dpi=dpi, paginas=paginas)
