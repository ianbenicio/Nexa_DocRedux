"""Ordem e associação (M12, story 2.17): o que a contagem de palavras e números não vê.

A cobertura compara multiconjuntos: "O aluno ajuda o professor" e "O professor ajuda o aluno" têm as mesmas
palavras. A 2.13 ancorou cada número nos vizinhos. Aqui entram quatro checagens, cada uma medida nos exemplos
01–05 antes de virar critério (docs/qa/ordem-associacao-2026-11-22/):

1. **Palavras** — cada trio de palavras seguidas *dentro de uma linha do original* reaparece na saída. A linha do
   original é atômica: o render muda a ordem dos blocos (selo antes do título, texto ao lado da imagem), não a ordem
   dentro da linha. Trio com palavra que a cobertura já dá como faltando fica com a cobertura.
2. **Rótulos** — linha do original que é só "palavra número" ("AULA 10", "AULAS 1 E 2"): o parágrafo da saída que
   começa com esse rótulo traz texto das páginas onde o rótulo aparece. Selo de aula trocado não passa mais.
3. **Imagens** — cada imagem da saída tem, nos 2 parágrafos de cada lado, texto da vizinhança dela no original
   (até 120 pt acima/abaixo, na mesma página). Conta só o trio que é exclusivo daquela página: legenda genérica
   ("Fonte: Inep") não prova nada.
4. **Tabelas** — cada célula da saída é localizada no original (páginas da tabela). Na mesma linha da tabela, a
   célula precisa de um par com o mesmo y; na coluna, o y não pode voltar; na linha, o x não pode voltar. Tabela
   de diagramação (célula com imagem) fica com a checagem de imagens.

Tudo devolve {"total": n, "trechos": [...]}; `verificar()` junta as quatro e põe "ok".
"""
import collections
import re

import pymupdf
from docx import Document
from docx.oxml.ns import qn

from ..pdftexto import DICT

WORD = re.compile(r"[\wÀ-ÿ%]+", re.UNICODE)
URLISH = re.compile(r"_|^(?:pdf|www|http|https|html|br|gov|org|com)$")


def words(text):
    """Mesma tokenização da cobertura (verify.ficha.words)."""
    return [w.lower() for w in WORD.findall(text.replace("ᵒ", "º")) if not w.isdigit() or len(w) > 1]


def _trios(ws):
    return [tuple(ws[i:i + 3]) for i in range(len(ws) - 2)]


# ----------------------------------------------------------------------------------------------- 1. palavras
def palavras(textos_original, textos_saida, limite=10):
    """Linhas do original com algum trio de palavras que não reaparece na saída (com as 3 palavras presentes)."""
    seq = [w for t in textos_saida for w in words(t)]
    saida = collections.Counter(_trios(seq))
    vocab = collections.Counter(seq)
    falta = collections.Counter(w for t in textos_original for w in words(t)) - vocab
    usados = collections.Counter()
    total, trechos = 0, []
    for t in textos_original:
        ruim = None
        for g in _trios(words(t)):
            if any(falta[w] or URLISH.search(w) for w in g):
                continue  # palavra ausente: a cobertura ja cobra
            if usados[g] < saida[g]:
                usados[g] += 1
            elif ruim is None:
                ruim = g
        if ruim is not None:
            total += 1
            if len(trechos) < limite:
                trechos.append(" ".join(t.split())[:90])
    return {"total": total, "trechos": trechos}


# ----------------------------------------------------------------------------------------------- 2. rotulos
ROT = re.compile(r"^\s*([^\W\d_]+)\s+(\d+(?:\s+[EeAa]\s+\d+)?)\s*$")


def _linhas_pdf(doc, fora):
    """(pagina 0-based, Rect, texto) das linhas horizontais, fora das páginas removidas/mantidas como imagem."""
    for pno, p in enumerate(doc):
        if str(pno + 1) in fora:
            continue
        for b in p.get_text("dict", flags=DICT)["blocks"]:
            for ln in b.get("lines", []):
                if abs(ln["dir"][1]) > 0.1:
                    continue
                yield pno, pymupdf.Rect(ln["bbox"]), "".join(s["text"] for s in ln["spans"])


def _norm_rotulo(w, n):
    return w.lower() + " " + re.sub(r"\s+", " ", n).lower()


def rotulos(doc, fora, textos_saida, limite=10, minimo=0.8):
    """Parágrafo da saída que começa com um rótulo do original: >= 80% das demais palavras vêm das páginas onde o
    rótulo aparece no original. Começar com a palavra de um rótulo ("Aula") e um número que não é rótulo no original
    também conta: o selo foi alterado."""
    pag = collections.defaultdict(collections.Counter)
    rot = collections.defaultdict(set)
    for pno, _, t in _linhas_pdf(doc, fora):
        pag[pno].update(words(t))
        m = ROT.match(t)
        if m:
            rot[_norm_rotulo(m.group(1), m.group(2))].add(pno)
    if not rot:
        return {"total": 0, "trechos": []}
    pat = re.compile(r"^\s*([^\W\d_]+)\s+(\d+(?:\s+[EeAa]\s+\d+)?)(?!\d)")
    nomes = {k.split()[0] for k in rot}
    total, trechos = 0, []
    for t in textos_saida:
        m = pat.match(t)
        if not m:
            continue
        chave = _norm_rotulo(m.group(1), m.group(2))
        if chave not in rot:
            if m.group(1).lower() in nomes:
                total += 1
                if len(trechos) < limite:
                    trechos.append(" ".join(t.split())[:90])
            continue
        resto = words(t[m.end():])
        if not resto:
            continue
        viz = collections.Counter()
        for p in rot[chave]:
            viz |= pag[p]
        if sum(1 for w in resto if viz[w]) / len(resto) < minimo:
            total += 1
            if len(trechos) < limite:
                trechos.append(" ".join(t.split())[:90])
    return {"total": total, "trechos": trechos}


# ----------------------------------------------------------------------------------------------- 3. imagens
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}cNvPr"
JANELA_PT, VIZINHOS = 120, 2


def _locais_imagens(model):
    """nome do arquivo -> (pagina 1-based, Rect) das imagens do modelo (ficha: img; relatório: corpo da fig)."""
    loc = {}
    for e in model["elementos"]:
        b = e.get("body") if e["t"] == "fig" else e if e["t"] == "img" else None
        if isinstance(b, dict) and b.get("file") and b.get("bbox"):
            m = re.match(r"p(\d+)_", b["file"])
            pg = b.get("pagina") or (int(m.group(1)) if m else None)
            if pg:
                loc[b["file"]] = (pg, pymupdf.Rect(b["bbox"]))
    return loc


def _sequencia_docx(d):
    """[(texto, [nomes de imagem])] de cada parágrafo, na ordem do documento (inclui células)."""
    return [("".join(x.text or "" for x in p.iter(qn("w:t"))), [e.get("name") for e in p.iter(PIC)])
            for p in d.element.body.iter(qn("w:p"))]


def _trios_textos(textos):
    s = set()
    for t in textos:
        w = words(t)
        s.update(_trios(w))
        if 0 < len(w) < 3:
            s.add(tuple(w))
    return s


def imagens(doc, model, d, limite=10):
    loc = _locais_imagens(model)
    perto = {}
    for nome, (pg, r) in loc.items():
        if pg - 1 >= doc.page_count:
            continue
        ts = []
        for b in doc[pg - 1].get_text("dict", flags=DICT)["blocks"]:
            for ln in b.get("lines", []):
                t = "".join(s["text"] for s in ln["spans"])
                if abs(ln["dir"][1]) > 0.1 or t.strip().isdigit() or ROT.match(t):
                    continue  # lateral girada, numero de pagina, rotulo de pagina: nao e vizinhanca da imagem
                lb = pymupdf.Rect(ln["bbox"])
                if lb.y1 >= r.y0 - JANELA_PT and lb.y0 <= r.y1 + JANELA_PT:
                    ts.append(t)
        perto[nome] = _trios_textos(ts)
    # so vale o trio exclusivo da pagina: texto que se repete perto de imagens de outras paginas nao associa nada
    porpag = collections.defaultdict(set)
    for n, v in perto.items():
        porpag[loc[n][0]] |= v
    freq = collections.Counter(g for v in porpag.values() for g in v)
    perto = {n: ({g for g in v if freq[g] == 1} or v) for n, v in perto.items()}
    seq = _sequencia_docx(d)
    total, trechos = 0, []
    for i, (t, nomes) in enumerate(seq):
        for nome in nomes:
            if not perto.get(nome):
                continue  # imagem sem texto perto no original: nada a associar
            antes = [x for x, _ in seq[:i] if x.strip()][-VIZINHOS:]
            depois = [x for x, _ in seq[i + 1:] if x.strip()][:VIZINHOS]
            if not (_trios_textos(antes + [t] + depois) & perto[nome]):
                total += 1
                if len(trechos) < limite:
                    trechos.append(f"{nome} (pág. {loc[nome][0]}) junto de {' '.join((antes[-1:] or depois[:1] or [''])[0].split())[:60]}")
    return {"total": total, "trechos": trechos}


# ----------------------------------------------------------------------------------------------- 4. tabelas
TOK = re.compile(r"[^\W_]+(?:[.,/][^\W_]+)*%?", re.UNICODE)
TOL_Y, TOL_ORDEM = 4, 8


def _toks(t):
    return [w.lower() for w in TOK.findall(t.replace("ᵒ", "º"))]


class _Palavras:
    """Palavras do original com bbox, por página, e índice token -> (página, posição)."""

    def __init__(self, doc, fora):
        self.pags, self.idx = [], collections.defaultdict(list)
        for pno, p in enumerate(doc):
            ws = []
            if str(pno + 1) not in fora:
                for x0, y0, x1, y1, w, *_ in p.get_text("words"):
                    for t in _toks(w):
                        ws.append((t, pymupdf.Rect(x0, y0, x1, y1)))
            for k, (t, _) in enumerate(ws):
                self.idx[t].append((pno, k))
            self.pags.append(ws)

    def candidatos(self, cel, paginas=None):
        """(página, Rect) onde o começo da célula (até 3 tokens) aparece no original."""
        tk = _toks(cel)[:3]
        res = []
        for pno, k in self.idx.get(tk[0], ()) if tk else ():
            if paginas is not None and pno not in paginas:
                continue
            ws = self.pags[pno]
            if [t for t, _ in ws[k:k + len(tk)]] == tk:
                r = pymupdf.Rect(ws[k][1])
                for _, rr in ws[k + 1:k + len(tk)]:
                    if abs(rr.y0 - r.y0) < 3:
                        r |= rr
                res.append((pno, r))
        return res


def _tabelas_docx(d):
    """[(linhas, tem_imagem)]: texto de cada célula (None na repetição de célula mesclada na mesma linha)."""
    out = []
    for t in d.tables:
        rows = []
        for r in t.rows:
            vistos, row = set(), []
            for c in r.cells:
                if id(c._tc) in vistos:
                    row.append(None); continue
                vistos.add(id(c._tc)); row.append(c.text.strip())
            rows.append(row)
        out.append((rows, any(True for _ in t._tbl.iter(PIC))))
    return out


def _paginas_da_tabela(rows, pal):
    votos = collections.Counter()
    for row in rows:
        for c in row:
            if c:
                cs = {p for p, _ in pal.candidatos(c)}
                if len(cs) == 1:
                    votos.update(cs)
    if not votos:
        return None
    top = votos.most_common(1)[0][1]
    P = {p for p, v in votos.items() if v >= max(2, top * 0.3)} or {votos.most_common(1)[0][0]}
    return set(range(min(P), max(P) + 2))  # tabela que continua na página seguinte


def _cadeia(seq, chave):
    """Existe uma escolha de candidatos em que (página, coordenada) não volta (tolerância TOL_ORDEM)? Devolve a
    posição da primeira célula sem escolha, ou None. Guloso pelo menor viável: ótimo para cadeia monótona."""
    ult = None
    for n, cs in enumerate(seq):
        ok = sorted(k for k in map(chave, cs) if ult is None or k[0] > ult[0]
                    or (k[0] == ult[0] and k[1] >= ult[1] - TOL_ORDEM))
        if not ok:
            return n
        ult = ok[0]
    return None


def _mesma_linha(cs, ds):
    return any(p == q and (abs(r.y0 - s.y0) <= TOL_Y or (r.y0 < s.y1 and s.y0 < r.y1)) for p, r in cs for q, s in ds)


def tabelas(doc, fora, d, limite=10):
    pal = None
    total, trechos = 0, []

    def falha(ti, i, j, txt, tipo):
        nonlocal total
        total += 1
        if len(trechos) < limite:
            trechos.append(f"tabela {ti + 1}, linha {i + 1}, coluna {j + 1} ({tipo}): {' '.join(txt.split())[:50]}")
    for ti, (rows, tem_imagem) in enumerate(_tabelas_docx(d)):
        if tem_imagem or not rows:
            continue  # diagramação (figuras lado a lado): fica com a checagem de imagens
        pal = pal or _Palavras(doc, fora)
        P = _paginas_da_tabela(rows, pal)
        if not P:
            continue  # nenhuma célula localizável sem ambiguidade (ex.: grade "1", "2", "3")
        cand = [[pal.candidatos(c, P) if c else [] for c in row] for row in rows]
        ruins = set()
        for i, row in enumerate(cand):  # mesma linha: par com o mesmo y
            loc = [j for j, cs in enumerate(row) if cs]
            if len(loc) >= 2:
                for j in loc:
                    if not any(_mesma_linha(row[j], row[k]) for k in loc if k != j):
                        ruins.add((i, j, "linha"))
        for j in range(max(len(r) for r in rows)):  # na coluna, o y não volta
            seq = [i for i in range(len(rows)) if j < len(cand[i]) and cand[i][j]]
            n = _cadeia([cand[i][j] for i in seq], lambda c: (c[0], c[1].y0)) if len(seq) > 1 else None
            if n is not None:
                ruins.add((seq[n], j, "ordem na coluna"))
        for i, row in enumerate(cand):  # na linha, o x não volta
            seq = [j for j, cs in enumerate(row) if cs]
            n = _cadeia([row[j] for j in seq], lambda c: (c[0], c[1].x0)) if len(seq) > 1 else None
            if n is not None:
                ruins.add((i, seq[n], "ordem na linha"))
        vistos = set()
        for i, j, tipo in sorted(ruins):
            if (i, j) not in vistos:
                vistos.add((i, j)); falha(ti, i, j, rows[i][j], tipo)
    return {"total": total, "trechos": trechos}


# ----------------------------------------------------------------------------------------------- tudo
def verificar(pdf, fora, model, docx, textos_original, textos_saida):
    """As quatro checagens. ok=False se qualquer uma acha algo fora de ordem ou de associação."""
    d = Document(docx)
    with pymupdf.open(pdf) as doc:
        r = {
            "palavras": palavras(textos_original, textos_saida),
            "rotulos": rotulos(doc, fora, textos_saida),
            "imagens": imagens(doc, model, d),
            "tabelas": tabelas(doc, fora, d),
        }
    r["ok"] = not any(v["total"] for v in r.values())
    return r
