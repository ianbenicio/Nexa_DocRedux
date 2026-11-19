"""DocRedux - extrator (passo 1+3+5 do pipeline): PDF -> modelo estruturado (JSON).

Abordagem whitelist: so passa para o modelo o que for reconhecido como conteudo
(titulos, enunciados, alternativas, textos-base, fontes, imagens visiveis, tabelas,
linhas de resposta, quadros de desenho). Todo o resto (arte de cabecalho, faixas,
rodape repetido, elementos decorativos) cai automaticamente  -> regras R5/R8.

Uso: python -m docredux_engine.ficha.extract <pdf> <saida_dir> [--perfil perfis/<nome>.json]
(no pacote, a entrada e `extrair()`; o CLI do worker e `python -m docredux_engine`).
"""
import json, math, re, sys
from pathlib import Path
import pymupdf

# ---------------------------------------------------------------- perfil do template (R32/R33)
# O perfil e DADO, nao codigo: um JSON por template em perfis/. O campo "tipo" (ficha | relatorio) liga
# e desliga familias de regras. Escolhido pela assinatura (fontes presentes no PDF) ou por --perfil.
from ..config import PERFIL_DIR  # noqa: E402  (engine/perfis/ ou DOCREDUX_PERFIS)


def _cores(o):
    """'#RRGGBB' -> int, recursivamente (JSON nao tem hexadecimal)."""
    if isinstance(o, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", o):
        return int(o[1:], 16)
    if isinstance(o, list):
        return [_cores(x) for x in o]
    if isinstance(o, dict):
        return {k: _cores(v) for k, v in o.items()}
    return o


def load_perfil(path):
    return _cores(json.loads(Path(path).read_text(encoding="utf-8")))


def fontes_do_pdf(doc, n=30):
    fontes = set()
    for p in list(doc)[:n]:
        fontes.update(f[3] for f in p.get_fonts())
    return fontes


def casa_assinatura(perfil, fontes):
    """R33: todas as fontes de 'assinatura.fontes_contem' aparecem (como substring) nas fontes do PDF."""
    return all(any(x in n for n in fontes) for x in perfil.get("assinatura", {}).get("fontes_contem", ["#sem-assinatura#"]))


def escolher_perfil(doc, perfis=None):
    """R33: perfil por assinatura. Todas as fontes de 'assinatura.fontes_contem' tem de aparecer nas
    primeiras 30 paginas. `perfis`: dicts ja carregados (padrao: PERFIL_DIR/*.json, em ordem de nome).
    Sem perfil compativel -> None (contrato: template novo, Q3)."""
    fontes = fontes_do_pdf(doc)
    if perfis is None:
        perfis = [load_perfil(f) for f in sorted(PERFIL_DIR.glob("*.json"))]
    for pf in perfis:
        if casa_assinatura(pf, fontes):
            return pf
    return None


RE_Q = re.compile(r"^(\d{1,2})\.\s+")
RE_SUB = re.compile(r"^([a-z])\.\s+")
RE_CHK = re.compile(r"^\[\d\]\s*")
RE_BUL = re.compile(r"^[•●▪»]\s*")
RE_OVER = re.compile(r"^(\[\d\]\s*)+$")  # glifos de checkbox extraidos como texto
MALHA_FATOR = 0.6  # R14: quadro/malha mantido a 60% da altura
RE_FIGTXT = re.compile(r"^(?:[A-Za-z]|\d{1,2})$")  # rotulo solto dentro de grade de figuras
RE_LABEL = re.compile(r"^(?:[A-Za-z]|\d{1,2})[.)]$")  # rotulo de figura: a. b. 1) (nao pega "se", "•")


def norm(s):
    return re.sub(r"[ \t ]+", " ", s.replace("\t", " ")).strip()


def join_text(a, b):
    if not a:
        return b
    if a.endswith("-") or re.search(r"(https?://|www\.)\S*$", a) and (a.endswith("/") or a.endswith(".") or a.endswith("_")):
        return a + b
    return a + " " + b



# ---------------------------------------------------------------- desenhos visiveis (respeita clip)
def visible_drawings(page):
    """visible_drawings(page) com o retangulo cortado pelo clip ativo. Fichas reaproveitam a mesma
    tira de figuras em varias paginas e escondem a parte que nao vale com um clip (ex. 04: a tira
    A/B da p.3 tem as linhas C/D no PDF, mas invisiveis). Sem isso, a figura capturada inclui area vazia."""
    out, stack = [], []
    for dr in page.get_drawings(extended=True):
        t, lvl = dr.get("type"), dr.get("level", 0)
        if t == "clip":
            del stack[lvl:]
            stack.append(pymupdf.Rect(dr["scissor"]))
            continue
        if t not in ("s", "f", "fs"):
            continue
        rc = pymupdf.Rect(dr["rect"])
        for c in stack[:lvl]:
            rc = pymupdf.Rect(max(rc.x0, c.x0), max(rc.y0, c.y0), min(rc.x1, c.x1), min(rc.y1, c.y1))
            if rc.x0 > rc.x1 or rc.y0 > rc.y1:
                rc = None
                break
        if rc is None:
            continue
        if rc.is_empty:  # fio de largura/altura 0: Rect.intersects() de retangulo vazio e sempre falso; infla 0,25 pt
            rc = pymupdf.Rect(rc.x0 - 0.25, rc.y0 - 0.25, rc.x1 + 0.25, rc.y1 + 0.25)
        d = dict(dr); d["rect"] = rc
        out.append(d)
    return out

# ---------------------------------------------------------------- imagens visiveis
def visible_image_regions(pdf_path, pno, page, dpi=36):
    """Regioes realmente visiveis das imagens (respeita clip/mascara).
    Truque: renderiza a pagina com e sem as imagens e compara pixels."""
    xrefs = sorted({im[0] for im in page.get_images(full=True)})
    if not xrefs:
        return []
    a = page.get_pixmap(dpi=dpi, alpha=False)
    d2 = pymupdf.open(pdf_path)
    p2 = d2[pno]
    for x in xrefs:
        try:
            p2.delete_image(x)
        except Exception:
            pass
    b = p2.get_pixmap(dpi=dpi, alpha=False)
    w, h, n = a.width, a.height, a.n
    sa, sb = a.samples, b.samples
    mask = bytearray(w * h)
    for i in range(w * h):
        o = i * n
        if abs(sa[o] - sb[o]) + abs(sa[o + 1] - sb[o + 1]) + abs(sa[o + 2] - sb[o + 2]) > 30:
            mask[i] = 1
    # componentes conexos (grade 3x3 px para tolerar ruido)
    seen = bytearray(w * h)
    regs = []
    for i in range(w * h):
        if mask[i] and not seen[i]:
            stack = [i]; seen[i] = 1
            x0 = x1 = i % w; y0 = y1 = i // w; cnt = 0
            while stack:
                j = stack.pop(); cnt += 1
                x, y = j % w, j // w
                x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
                for dx in (-2, -1, 0, 1, 2):
                    for dy in (-2, -1, 0, 1, 2):
                        xx, yy = x + dx, y + dy
                        if 0 <= xx < w and 0 <= yy < h:
                            k = yy * w + xx
                            if mask[k] and not seen[k]:
                                seen[k] = 1; stack.append(k)
            if cnt > 40:
                s = 72 / dpi
                regs.append(pymupdf.Rect(x0 * s, y0 * s, (x1 + 1) * s, (y1 + 1) * s))
    return regs


# ---------------------------------------------------------------- tabelas
def table_grid(page, t, vdraw=None):
    """Texto das celulas por posicao. find_tables nao devolve o texto de celulas que nao tem
    borda propria (ex.: corpo da tabela 'hipotese', p.3 do ex. 03): a celula ausente ganha a
    faixa de linha/coluna dela e recebe as palavras que caem ali.
    Ex. 04: (1) tabela sem borda vertical na 1a/ultima coluna (rotulos 'ACARAJE'/'Unidade de milhar'
    fora do bbox): as linhas horizontais que passam do bbox revelam a coluna e ela e acrescentada;
    (2) celula mesclada ('PARTE INTEIRA' sobre 4 colunas): palavras coladas (< 7 pt) que cairiam em
    celulas vizinhas voltam a ser um texto so, na 1a celula."""
    nr, nc = t.row_count, t.col_count
    cell = {(r, k): (pymupdf.Rect(c) if c is not None else None)
            for r, row in enumerate(t.rows) for k, c in enumerate(row.cells)}
    colx, rowy = {}, {}
    for (r, k), c in cell.items():
        if c is None:
            continue
        a = colx.get(k, (c.x0, c.x1)); colx[k] = (min(a[0], c.x0), max(a[1], c.x1))
        b = rowy.get(r, (c.y0, c.y1)); rowy[r] = (min(b[0], c.y0), max(b[1], c.y1))
    rects = {}
    for r in range(nr):
        for k in range(nc):
            c = cell.get((r, k))
            if c is not None:
                rects[(r, k)] = c
            elif k in colx and r in rowy:
                rects[(r, k)] = pymupdf.Rect(colx[k][0], rowy[r][0], colx[k][1], rowy[r][1])
    bb = pymupdf.Rect(t.bbox)
    xl = xr = None
    if vdraw:
        edges = [y for r in rowy.values() for y in r]
        hl = [d["rect"] for d in vdraw if d["rect"].height < 2 and d["rect"].width > 15 and bb.y0 - 2 <= d["rect"].y0 <= bb.y1 + 2
              and any(abs(d["rect"].y0 - y) < 2 for y in edges)]
        left = [h for h in hl if h.x0 < bb.x0 - 8 and h.x1 >= bb.x0 - 2]
        right = [h for h in hl if h.x1 > bb.x1 + 8 and h.x0 <= bb.x1 + 2]
        if len({round(h.y0) for h in left}) >= 2:
            xl = min(h.x0 for h in left)
        if len({round(h.y0) for h in right}) >= 2:
            xr = max(h.x1 for h in right)
    sh = 1 if xl is not None else 0
    rects = {(r, k + sh): v for (r, k), v in rects.items()}
    if xl is not None:
        for r, (y0, y1) in rowy.items():
            rects[(r, 0)] = pymupdf.Rect(xl, y0, bb.x0, y1)
    if xr is not None:
        for r, (y0, y1) in rowy.items():
            rects[(r, nc + sh)] = pymupdf.Rect(bb.x1, y0, xr, y1)
    ncol = nc + sh + (1 if xr is not None else 0)
    area = pymupdf.Rect(xl if xl is not None else bb.x0, bb.y0, xr if xr is not None else bb.x1, bb.y1)
    cells_w = {(r, k): [] for r in range(nr) for k in range(ncol)}
    words = sorted((w for w in page.get_text("words") if area.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))),
                   key=lambda w: (round(w[1] / 3), w[0]))
    for w in words:
        p = pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        hit = next(((r, k) for (r, k), rc in rects.items() if rc.contains(p)), None)
        if hit:
            cells_w[hit].append(w)
    for r in range(nr):  # (2) celula mesclada: texto colado atravessando a divisa
        for k in range(ncol - 1):
            a_, b_ = cells_w[(r, k)], cells_w[(r, k + 1)]
            if a_ and b_:
                wa = max(a_, key=lambda w: w[2]); wb = min(b_, key=lambda w: w[0])
                if 0 <= wb[0] - wa[2] < 7 and abs(wa[1] - wb[1]) < 3:
                    cells_w[(r, k)] = a_ + b_; cells_w[(r, k + 1)] = []
    grid = [[" ".join(w[4] for w in sorted(cells_w[(r, k)], key=lambda w: (round(w[1] / 3), w[0]))) for k in range(ncol)] for r in range(nr)]
    return [[norm(c) for c in row] for row in grid], area


def is_figure_table(t, vdraw):
    """Tabela cujas celulas contem desenhos vetoriais (ex. 04: tira de figuras A-D com formas e
    numeros 1-6). Texto de celula nao serve: os desenhos sao o conteudo (R7)."""
    cells = [pymupdf.Rect(c) for c in t.cells if c is not None]
    n = 0
    for dr in vdraw:
        r = dr["rect"]
        if r.width < 6 or r.height < 6:
            continue
        for c in cells:
            if c.x0 + 2 <= r.x0 and r.x1 <= c.x1 - 2 and c.y0 + 2 <= r.y0 and r.y1 <= c.y1 - 2:
                n += 1
                break
    return n >= 3

# ---------------------------------------------------------------- figuras vetoriais
def banner_bottom(vd, W, H):
    """Fim da faixa de cabecalho da aula: o maior preenchimento largo colado no topo da pagina.
    Tudo que comeca acima disso e arte do cabecalho (R5/R8). Sem faixa: 20% da pagina."""
    b = [d["rect"].y1 for d in vd if d.get("fill") and d["rect"].width >= W * 0.8 and d["rect"].y0 <= 2 and d["rect"].y1 < H * 0.3]
    return max(b) + 1 if b else H * 0.2


def inner_drawings(rc, vd):
    """Desenhos (nao so pontos) dentro de um retangulo: separa quadro de desenho vazio de grade/figura."""
    return [d for d in vd if d["rect"] != rc and rc.contains(d["rect"]) and (d["rect"].width >= 6 or d["rect"].height >= 6)]


def dots_inside(rc, vd):
    return sum(1 for d in vd if d["rect"] != rc and rc.contains(d["rect"]) and d["rect"].width < 4 and d["rect"].height < 4)


def is_empty_box(rc, vd, W):
    """Moldura grande (>40% da largura, >100 pt de altura) SEM desenhos dentro = quadro de desenho (R14).
    Com desenhos dentro (grade quadriculada com figura) e figura, nao quadro."""
    return rc.width > W * 0.4 and rc.height > 100 and not inner_drawings(rc, vd)


def vector_figures(page, tables, imgs, W, H, stats, manter=False):
    """Desenhos vetoriais da area de conteudo agrupados em figuras. Entra como figura se tiver
    preenchimento OU rotulo de texto dentro (ex.: molde preenchido, figuras a/b/c/d). Contorno
    so de linhas sem texto (ex.: pingos decorativos da margem) e descartado (R5).
    Linhas largas de altura zero entram no agrupamento: numa grade elas sao a figura; sozinhas
    (grupo so de linhas) sao linhas de resposta e saem do grupo."""
    vd = visible_drawings(page)
    hb = banner_bottom(vd, W, H)
    cand = []
    for dr in vd:
        rc = dr["rect"]
        if rc.is_empty and max(rc.width, rc.height) <= 6:
            continue
        if rc.y0 < hb or rc.y0 > H * 0.92:             # faixa de cabecalho e rodape
            continue
        if rc.x1 < W * 0.12 or rc.x0 > W - 30:         # faixa lateral e margem direita
            continue
        if is_empty_box(rc, vd, W):                     # quadro de desenho (tratado a parte)
            continue
        if not manter and (rc.y1 > H * 0.97 or rc.x0 < W * 0.02 or rc.x1 > W * 0.98):  # arte que sangra pela borda = decorativa (R5)
            continue
        if any(tb.intersects(rc) for tb in tables) or any(im.intersects(rc) for im in imgs):
            continue
        f = dr.get("fill")  # preenchimento branco/quase branco nao conta (pingos com miolo branco)
        line = rc.height < 2 and rc.width > W * 0.25
        cand.append((rc, f is not None and sum(f) / len(f) < 0.95, line))
    groups = []  # [rect, tem_preenchimento, n, so_linhas]
    for rc, filled, line in cand:
        hit = [g for g in groups if (g[0] + (-12, -12, 12, 12)).intersects(rc)]
        if hit:
            g = hit[0]
            g[0] = g[0] | rc; g[1] = g[1] or filled; g[2] += 1; g[3] = g[3] and line
            for other in hit[1:]:  # une grupos que o desenho conectou
                g[0] = g[0] | other[0]; g[1] = g[1] or other[1]; g[2] += other[2]; g[3] = g[3] and other[3]; groups.remove(other)
        else:
            groups.append([pymupdf.Rect(rc), filled, 1, line])
    words = page.get_text("words")
    out = []
    for g, filled, n, only_lines in groups:
        if only_lines or g.width < 40 or g.height < 40:
            continue
        # rotulo: palavra a ate 14 pt da figura (letras a./b./c. ficam abaixo ou ao lado dos desenhos)
        labels = [w for i, w in enumerate(words) if (RE_LABEL.match(w[4]) or w[4] == "Figura"
                                                    or (RE_FIGTXT.match(w[4]) and i and words[i - 1][4] == "Figura" and abs(words[i - 1][1] - w[1]) < 3))
                  and not (w[0] > W * 0.9 or w[2] < W * 0.12 or w[1] < hb or w[3] > H * 0.92)  # so area de conteudo (sem rodape/aba)
                  and (g + (-14, -14, 14, 14)).contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
        # grade/tira de figuras: letras e numeros soltos (A, B, 1..6) DENTRO do grupo tambem sao rotulo
        inside = [w for w in words if RE_FIGTXT.match(w[4]) and g.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
        if inside:
            labels = labels + [w for w in inside if w not in labels]
        if n < 2 and not (filled or inside):  # desenho unico so entra com preenchimento ou rotulo dentro
            continue
        if not (filled or labels):
            if not manter:  # R5: desenho sem preenchimento nem rotulo = decorativo
                stats["desenhos_decorativos"] = stats.get("desenhos_decorativos", 0) + 1
                continue
            stats["desenhos_decorativos_mantidos"] = stats.get("desenhos_decorativos_mantidos", 0) + 1
        r = pymupdf.Rect(g + (-3, -3, 3, 3)) & page.rect
        for w in labels:
            r |= pymupdf.Rect(w[:4]) & page.rect
        out.append(r)

    def text_between(u, parts):
        """palavras dentro da uniao que nao pertencem a nenhuma das figuras"""
        return [w for w in words if u.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
                and not any(q.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)) for q in parts)]
    # figuras do mesmo exercicio viram uma so: tocando (20 pt), ou ate 60 pt se nao houver texto,
    # imagem ou outra figura entre elas (ex. 04: formas A-I espalhadas na mesma pagina)
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                u = out[i] | out[j]
                near = (out[i] + (-20, -20, 20, 20)).intersects(out[j])
                far = (out[i] + (-60, -60, 60, 60)).intersects(out[j]) and not text_between(u, [out[i], out[j]])                     and not any(k != i and k != j and u.intersects(out[k]) for k in range(len(out)))                     and not any(u.intersects(im) for im in imgs)
                if near or far:
                    out[i] = u; del out[j]; merged = True; break
            if merged:
                break
    return out


# ---------------------------------------------------------------- pagina
def page_items(pdf_path, doc, pno, perfil, out_img_dir, stats, manter=False):
    page = doc[pno]
    W, H = page.rect.width, page.rect.height
    items = []  # (y, ordem, dict)

    # tabelas de conteudo
    tables = []
    vdraw = visible_drawings(page)
    # faixa do cabecalho: so ha arte ate o fim dela; pagina sem faixa (continuacao) pode ter tabela no topo
    hb_tab = banner_bottom(vdraw, W, H) if any(d.get("fill") and d["rect"].width >= W * 0.8 and d["rect"].y0 <= 2 and d["rect"].y1 < H * 0.3 for d in vdraw) else 20
    for t in page.find_tables().tables:
        bb = pymupdf.Rect(t.bbox)
        if bb.y0 < hb_tab or t.row_count < 2 or t.col_count < 2:  # tabela dentro da faixa de cabecalho = arte
            continue
        if is_figure_table(t, vdraw):
            stats["tabelas_figura"] = stats.get("tabelas_figura", 0) + 1
            continue  # grade com desenhos dentro das celulas: sai como figura (vector_figures), nao como tabela de texto
        rows, bb = table_grid(page, t, vdraw)  # bb ja inclui colunas laterais recuperadas
        if sum(1 for r in rows for c in r if c) >= 2:
            tables.append(bb)
            items.append((bb.y0, 1, {"t": "table", "rows": rows}))
            stats["tabelas"] += 1

    # imagens visiveis
    imgs = []
    for k, r in enumerate(visible_image_regions(pdf_path, pno, page)):
        r = r & page.rect
        if r.get_area() < 400:
            continue
        pct = r.get_area() / page.rect.get_area() * 100
        fn = f"p{pno+1:02d}_img{k+1}.jpg"
        page.get_pixmap(dpi=200, clip=r).save(out_img_dir / fn, jpg_quality=82)
        imgs.append(r)
        items.append((r.y0, 0, {"t": "img", "file": fn, "w": round(r.width, 1), "h": round(r.height, 1),
                                "pct": round(pct, 1), "bbox": [round(v, 1) for v in r], "side": []}))
        stats["imagens"] += 1

    # figuras vetoriais (moldes, solidos, desenhos com rotulos a./b.): viram imagem, rotulos incluidos (R7)
    figs = vector_figures(page, tables, imgs, W, H, stats, manter)
    for k, r in enumerate(figs):
        fn = f"p{pno+1:02d}_fig{k+1}.jpg"
        page.get_pixmap(dpi=200, clip=r).save(out_img_dir / fn, jpg_quality=85)
        pct = r.get_area() / page.rect.get_area() * 100
        items.append((r.y0, 0, {"t": "img", "file": fn, "w": round(r.width, 1), "h": round(r.height, 1),
                                "pct": round(pct, 1), "bbox": [round(v, 1) for v in r], "side": [],
                                "figura": True, "pagina": pno + 1}))
        stats["figuras_vetoriais"] = stats.get("figuras_vetoriais", 0) + 1

    # linhas de texto -> rows (mesma linha de base fundidas)
    raw = []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            if abs(ln["dir"][1]) > 0.1:  # texto rotacionado = rodape vertical
                stats["descartado_rodape"] += 1
                continue
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            txt = norm("".join(s["text"] for s in ln["spans"]))
            bb = pymupdf.Rect(ln["bbox"])
            s0 = spans[0]
            if re.search(perfil["rodape_regex"], txt) or (txt.isdigit() and (bb.y0 > H * 0.92 or bb.x0 < 60)):
                stats["descartado_rodape"] += 1
                continue
            if s0["color"] != perfil["titulo_aula"]["color"] and re.match(perfil["running_header_regex"], txt) and (bb.x0 < 60 or bb.x1 > W - 30):
                stats["descartado_cabecalho_corrido"] += 1
                continue
            if any(tb.contains(bb.tl + (1, 1)) for tb in tables):
                continue
            if any(fr.contains(pymupdf.Point((bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2)) for fr in figs):
                continue  # rotulo dentro de figura: ja esta na imagem
            raw.append({"bb": bb, "txt": txt, "raw": "".join(sp["text"] for sp in ln["spans"]).strip(), "font": s0["font"], "size": round(s0["size"], 1),
                        "color": s0["color"], "italic": bool(s0["flags"] & 2)})
    # lacunas de preencher ("A abelhuda e a figura: ______ ."): sublinhado na linha de base de um texto,
    # fora das letras = lacuna, vira "____" no texto (senao a lacuna some). Sublinhado de link fica de fora:
    # passa por baixo das letras (cobertura alta)
    for dr in vdraw:
        rc = dr["rect"]
        if not (rc.height < 2 and rc.width >= 20 and rc.y0 > H * 0.04):
            continue
        if any(tb.intersects(rc) for tb in tables) or any(fr.intersects(rc) for fr in figs):
            continue
        base = [r for r in raw if abs(r["bb"].y1 - rc.y0) < 5]
        if not base:
            continue
        cover = sum(max(0, min(r["bb"].x1, rc.x1) - max(r["bb"].x0, rc.x0)) for r in base) / rc.width
        if cover >= 0.4:
            continue
        ref = min(base, key=lambda r: abs(r["bb"].x1 - rc.x0))
        us = "_" * max(6, min(28, round(rc.width / 9)))
        raw.append(dict(ref, bb=pymupdf.Rect(rc.x0, ref["bb"].y0, rc.x1, ref["bb"].y1), txt=us, raw=us))
        stats["lacunas"] = stats.get("lacunas", 0) + 1
    raw.sort(key=lambda r: (round(r["bb"].y0 / 3), r["bb"].x0))
    rows = []
    for r in raw:
        # glifo de checkbox sobreposto ao texto da mesma linha ("[3]   [1]" sobre "Sim   Ainda nao"):
        # vira ☐ antes de cada segmento do texto, na posicao certa (nao no fim da linha)
        if rows and abs(rows[-1]["bb"].y0 - r["bb"].y0) < 3:
            a, b = rows[-1], r
            main, ov = (a, b) if RE_OVER.match(b["txt"]) else ((b, a) if RE_OVER.match(a["txt"]) else (None, None))
            if main is not None and main.get("raw") and main["bb"].x0 < ov["bb"].x0 < main["bb"].x1:
                n = len(re.findall(r"\[\d\]", ov["txt"]))
                segs = [x for x in re.split(r"\s{3,}", main["raw"]) if x]
                if n == len(segs):
                    segs = ["☐ " + x for x in segs]
                elif n == len(segs) - 1:
                    segs = segs[:1] + ["☐ " + x for x in segs[1:]]
                else:
                    segs = segs + ["☐"] * n
                rows[-1] = dict(main, txt=norm(" ".join(segs)), raw=None, bb=a["bb"] | b["bb"])
                stats["checkbox_sobreposto"] = stats.get("checkbox_sobreposto", 0) + n
                continue
        if rows and abs(rows[-1]["bb"].y0 - r["bb"].y0) < 3 and not any(
                im.intersects(rows[-1]["bb"]) != im.intersects(r["bb"]) for im in imgs):
            prev = rows[-1]
            # funde: ordem por x
            parts = sorted([prev, r], key=lambda z: z["bb"].x0)
            prev.update({"txt": parts[0]["txt"] + " " + parts[1]["txt"], "bb": prev["bb"] | r["bb"], "raw": None,
                         "font": parts[0]["font"], "size": parts[0]["size"], "color": parts[0]["color"]})
            # cor do marcador pode estar no segundo pedaco (checkbox preto + "a." rosa)
            if r["color"] in perfil["cores_marcador"]:
                prev["has_pink"] = True
        else:
            rows.append(dict(r))

    # composicao em linha (R7): texto ENTRE duas imagens da mesma faixa ("[moeda] equivale a duas moedas de
    # [moeda] ou o contrario.") nao tem como virar texto ao lado de imagem: vira uma figura so, com o texto
    # dentro. Sementes: linha de texto com imagem a esquerda e a direita (ate 40 pt). A faixa absorve as
    # linhas e imagens da mesma altura que a tocam.
    comps = []
    for r in rows:
        yc = (r["bb"].y0 + r["bb"].y1) / 2
        L = [im for im in imgs if im.y0 - 3 <= yc <= im.y1 + 3 and 0 <= r["bb"].x0 - im.x1 <= 40]
        R_ = [im for im in imgs if im.y0 - 3 <= yc <= im.y1 + 3 and 0 <= im.x0 - r["bb"].x1 <= 40]
        if L and R_:
            comps.append(pymupdf.Rect(r["bb"]) | L[0] | R_[0])
    # imagens lado a lado na mesma faixa (ate 40 pt): no ex. 04 o texto "equivale a duas moedas de" e uma
    # IMAGEM (texto rasterizado) entre duas moedas; em fluxo de texto as pecas se desmontam
    for i, a_ in enumerate(imgs):
        for b_ in imgs[i + 1:]:
            if min(a_.y1, b_.y1) - max(a_.y0, b_.y0) >= 0.5 * min(a_.height, b_.height) and max(a_.x0, b_.x0) - min(a_.x1, b_.x1) <= 40:
                comps.append(pymupdf.Rect(a_) | b_)
    if comps:
        grew = True
        while grew:
            grew = False
            for i, c in enumerate(comps):
                for r in rows:
                    yc = (r["bb"].y0 + r["bb"].y1) / 2
                    if c.y0 - 2 <= yc <= c.y1 + 2 and r["bb"].x0 <= c.x1 + 40 and r["bb"].x1 >= c.x0 - 40 and not c.contains(r["bb"]):
                        comps[i] = c = c | r["bb"]; grew = True
                for im in imgs:
                    if not c.contains(im) and ((c.y0 - 2 <= (im.y0 + im.y1) / 2 <= c.y1 + 2 and im.x0 <= c.x1 + 40 and im.x1 >= c.x0 - 40)
                                               or (c + (-15, -15, 15, 15)).intersects(im)):
                        comps[i] = c = c | im; grew = True
            for i in range(len(comps)):  # faixas coladas (ate 15 pt) viram uma so
                for j in range(i + 1, len(comps)):
                    if (comps[i] + (-15, -15, 15, 15)).intersects(comps[j]):
                        comps[i] = comps[i] | comps[j]; del comps[j]; grew = True; break
                if grew:
                    break
        for k, c in enumerate(comps):
            c = c & page.rect
            inside = lambda rc: c.contains(pymupdf.Point((rc.x0 + rc.x1) / 2, (rc.y0 + rc.y1) / 2))
            for it in [it for it in items if it[2]["t"] == "img" and inside(pymupdf.Rect(it[2]["bbox"]))]:
                try:
                    (out_img_dir / it[2]["file"]).unlink()
                except OSError:
                    pass
                items.remove(it)
            imgs = [im for im in imgs if not inside(im)]
            rows = [r for r in rows if not inside(r["bb"])]
            fn = f"p{pno+1:02d}_cmp{k+1}.jpg"
            page.get_pixmap(dpi=200, clip=c).save(out_img_dir / fn, jpg_quality=85)
            items.append((c.y0, 0, {"t": "img", "file": fn, "w": round(c.width, 1), "h": round(c.height, 1),
                                    "pct": round(c.get_area() / page.rect.get_area() * 100, 1), "bbox": [round(v, 1) for v in c],
                                    "side": [], "figura": True, "manter_tamanho": True, "pagina": pno + 1}))
            stats["composicoes_em_linha"] = stats.get("composicoes_em_linha", 0) + 1

    # base x da pagina: x dos marcadores de questao
    qx = [r["bb"].x0 for r in rows if RE_Q.match(r["txt"]) and r["color"] in perfil["cores_marcador"]]
    body = [r["bb"].x0 for r in rows if r["size"] >= 10 and r["color"] == 0]
    base_x = min(qx) if qx else (min(body) if body else 95)

    # linhas de resposta (exclui sublinhado de link e linhas dentro de tabela)
    ans = []
    for dr in visible_drawings(page):
        rc = dr["rect"]
        if rc.height < 2 and rc.width > W * 0.25 and rc.y0 > H * 0.04:
            if any(tb.intersects(rc) for tb in tables) or any(fr.intersects(rc) for fr in figs):
                continue
            if any(abs(rw["bb"].y1 - rc.y0) < 5 and rw["bb"].x0 < rc.x1 and rc.x0 < rw["bb"].x1 for rw in rows):
                stats["sublinhado_link"] += 1
                continue
            ans.append(rc)
        elif is_empty_box(rc, vdraw, W) and rc.x0 > 40 and rc.x1 < W - 30 and rc.y1 < H - 30 \
                and dr.get("color") is not None and not dr.get("fill") \
                and max(dr["color"]) - min(dr["color"]) < 0.1                 and sum(dr["color"]) / len(dr["color"]) < 0.85:  # contorno neutro (preto/cinza); colorido ou branco = decorativo
            if dots_inside(rc, vdraw) >= 20:
                # malha pontilhada: o quadro e a malha. Recorta o topo (R14: 60% da altura) mantendo o espacamento dos pontos
                cr = pymupdf.Rect(rc.x0 - 1, rc.y0 - 1, rc.x1 + 1, rc.y0 + rc.height * MALHA_FATOR) & page.rect
                fn = f"p{pno+1:02d}_malha.jpg"
                page.get_pixmap(dpi=150, clip=cr).save(out_img_dir / fn, jpg_quality=85)
                items.append((rc.y0, 0, {"t": "img", "file": fn, "w": round(cr.width, 1), "h": round(cr.height, 1),
                                        "pct": round(cr.get_area() / page.rect.get_area() * 100, 1), "bbox": [round(v, 1) for v in cr],
                                        "side": [], "figura": True, "manter_tamanho": True, "pagina": pno + 1}))
                stats["malhas"] = stats.get("malhas", 0) + 1
            else:
                items.append((rc.y0, 2, {"t": "box", "h": round(rc.height, 1), "w": round(rc.width, 1)}))
                stats["quadros"] += 1
    ys = sorted({round(r.y0) for r in ans})
    run = []
    for y in ys:
        if run and y - run[-1] > 45:
            items.append((run[0], 3, {"t": "lines", "n": len(run)})); run = []
        run.append(y)
    if run:
        items.append((run[0], 3, {"t": "lines", "n": len(run)}))
    stats["linhas_resposta_orig"] += len(ys)

    # cabecalho da aula (R8): tag + titulo viram um H1
    tag = [r for r in rows if r["color"] == 0xFFFFFF and re.match(perfil["tag_aula"]["regex"], r["txt"])]
    tit = [r for r in rows if perfil["titulo_aula"]["font_contains"] in r["font"] and r["color"] == 0xFFFFFF]
    if tit:
        text = " ".join(r["txt"] for r in tit)
        lab = tag[0]["txt"] if tag else ""
        items.append((0, -1, {"t": "h1", "tag": lab.title(), "text": text}))
        rows = [r for r in rows if r not in tit and r not in tag]

    # agrupa rows em paragrafos e classifica (R9)
    para = None
    paras = []
    for r in rows:
        t = r["txt"]
        pink = r["color"] in perfil["cores_marcador"] or r.get("has_pink")
        side_of = next((i for i, im in enumerate(imgs) if im.y0 - 5 <= r["bb"].y0 <= im.y1 and not im.intersects(r["bb"])), None)
        if "Glober" in r["font"] and 13.5 <= r["size"] <= 14.5:
            kind = "h2"
        elif "Glober" in r["font"] and 11.5 <= r["size"] < 13.5 and r["color"] == perfil["cor_destaque"]:
            kind = "h3"  # subtitulo ("Momento 2 – Revisao do proprio texto"), ex. 02
        elif r["size"] <= perfil["fonte_ref_size"] + 0.1 and t.upper().startswith("FONTE"):
            kind = "source"
        elif RE_CHK.match(t):
            kind = "opt"
        elif pink and RE_Q.match(t):
            kind = "q"
        elif pink and RE_SUB.match(t):
            kind = "sub"
        elif RE_BUL.match(t):
            kind = "bullet"
        else:
            kind = None  # continuacao ou paragrafo
        gap = r["bb"].y0 - para["y1"] if para else 999
        cont = para is not None and kind is None and gap <= 19 and para.get("side_of") == side_of \
            and para["kind"] not in ("h2", "h3") \
            and (para["kind"] != "p" or abs(r["bb"].x0 - para["x"]) < 20 or para["kind"] in ("quote", "source"))
        if para and kind is None and para["kind"] == "source" and gap <= 19:
            cont = True
        # titulo H2 que quebra em 2 linhas (mesma fonte/tamanho, colado) continua o mesmo H2
        h2_cont = kind == "h2" and para is not None and para["kind"] == "h2" and gap <= 6             and not re.match(r"^(Atividade|Etapa|Aulas?)\s+\d", t, re.I)
        if cont or h2_cont:
            para["text"] = join_text(para["text"], t)
            para["y1"] = r["bb"].y1
            if r["italic"]:
                para["italic_parts"] = True
            continue
        if kind is None:
            dx = r["bb"].x0 - base_x
            kind = "quote" if dx > 24 and side_of is None else "p"
            if r["color"] not in [0] + perfil["cores_marcador"] and kind == "p":
                kind = "quote"
        para = {"kind": kind, "text": t, "x": r["bb"].x0, "y0": r["bb"].y0, "y1": r["bb"].y1,
                "size": r["size"], "side_of": side_of, "indent": r["bb"].x0 - base_x}
        paras.append(para)

    for p in paras:
        k, t = p["kind"], p["text"]
        if k == "q":
            m = RE_Q.match(t); el = {"t": "q", "num": m.group(1), "text": t[m.end():]}
        elif k == "sub":
            m = RE_SUB.match(t); el = {"t": "sub", "num": m.group(1), "text": t[m.end():]}
        elif k == "opt":
            body = RE_CHK.sub("", t); m = RE_SUB.match(body)
            el = {"t": "opt", "label": m.group(1) if m else "", "text": body[m.end():] if m else body}
        elif k == "bullet":
            el = {"t": "bullet", "text": RE_BUL.sub("", t)}
        elif k == "h2":
            el = {"t": "h2", "text": t}
        elif k == "source":
            el = {"t": "source", "text": t}
        else:
            el = {"t": k, "text": t}
        if p["side_of"] is not None:
            # texto ao lado de imagem (R3) fica preso a imagem
            img_el = [it for it in items if it[2]["t"] == "img"][p["side_of"]][2]
            img_el["side"].append(el)
            continue
        items.append((p["y0"], 4, el))

    items.sort(key=lambda z: (z[0], z[1]))
    return [it[2] for it in items]


def is_cover_or_blank(page, pno, n_pages):
    txt = page.get_text("text").strip()
    if not txt:
        return "branca"
    if pno in (0, n_pages - 1) and len(txt) < 200 and len(page.get_drawings()) > 500:
        return "capa" if pno == 0 else "contracapa"
    return None


def pagina_inteira(page, pno, out_img_dir, motivo, dpi=110):
    """manter_capa_elementos (story 2.5): pagina que seria removida (capa, contracapa, foto) vira imagem
    de pagina inteira. O texto dela fica na imagem, como nas figuras vetoriais (R7); o verify nao o cobra."""
    fn = f"p{pno + 1:03d}_pagina.jpg"
    page.get_pixmap(dpi=dpi).save(out_img_dir / fn, jpg_quality=80)
    return {"t": "pagina", "file": fn, "w": round(page.rect.width, 1), "h": round(page.rect.height, 1),
            "pagina": pno + 1, "motivo": motivo}


def identification(doc):
    """R1: identificacao a partir do rodape repetido (texto do proprio documento)."""
    import collections
    c = collections.Counter()
    for p in doc:
        for ln in p.get_text("text").splitlines():
            if "·" in ln and ("ANO" in ln or "ESTUDANTE" in ln):
                c[norm(ln)] += 1
    parts = [t for t, _ in c.most_common(2)]
    return " · ".join(parts)


def extrair(pdf, out, perfil=None, *, paginas=None, manter_capa_elementos=False):
    """PDF -> out/modelo.json (+ out/img/). Devolve o `meta` do modelo.
    perfil: dict ja carregado (load_perfil); None = escolher pela assinatura (R33).
    paginas: (a, b) 1-based, so no relatorio (recorte de iteracao).
    manter_capa_elementos: desliga R1 (capa/contracapa viram imagem de pagina inteira), R5 (desenhos
    decorativos entram como figura) e R12 (cabecalho corrido vai para o cabecalho do DOCX)."""
    pdf = Path(pdf); out = Path(out); out.mkdir(parents=True, exist_ok=True)
    (out / "img").mkdir(exist_ok=True)
    for old in (out / "img").glob("*.jpg"):  # saida limpa: sem imagens de rodadas anteriores
        old.unlink()
    doc = pymupdf.open(pdf)
    if perfil is None:
        perfil = escolher_perfil(doc)
        if perfil is None:
            raise ValueError("nenhum perfil casa com as fontes do PDF: criar perfil novo (passo L)")
    if perfil["tipo"] == "relatorio":  # R32: familia de regras de relatorio (R34-R48); fichas seguem abaixo
        from ..relatorio import extract as extract_relatorio
        return extract_relatorio.run(pdf, out, doc, perfil, paginas=paginas, manter=manter_capa_elementos)
    stats = {k: 0 for k in ["imagens", "tabelas", "quadros", "linhas_resposta_orig", "descartado_rodape",
                            "descartado_cabecalho_corrido", "sublinhado_link"]}
    removed, como_imagem = {}, {}
    model = [{"t": "id", "text": identification(doc), "arquivo": pdf.name}]
    for pno in range(doc.page_count):
        why = is_cover_or_blank(doc[pno], pno, doc.page_count)
        if why and manter_capa_elementos and why in ("capa", "contracapa"):
            pg = pagina_inteira(doc[pno], pno, out / "img", why)
            model.insert(0, pg) if why == "capa" else model.append(pg)  # capa antes da caixa de identificacao
            como_imagem[pno + 1] = why
            continue
        if why:
            removed[pno + 1] = why
            continue
        model.extend(page_items(str(pdf), doc, pno, perfil, out / "img", stats, manter_capa_elementos))
    # fusoes entre paginas: linhas de resposta divididas e paragrafo que continua na pagina seguinte
    merged = []
    for el in model:
        prev = merged[-1] if merged else None
        if el["t"] == "lines" and prev and prev["t"] == "lines":
            prev["n"] += el["n"]
        elif el["t"] == "p" and el["text"][:1].islower() and prev and prev["t"] in ("q", "sub", "p", "quote", "bullet"):
            prev["text"] = join_text(prev["text"], el["text"])
        elif el["t"] == "p" and prev and prev["t"] == "p":
            # R16: paragrafos de texto corrido consecutivos viram um so (gabarito do usuario, aulas 8 e 9)
            prev["text"] = prev["text"].rstrip() + " " + el["text"].lstrip()
            stats["paragrafos_fundidos"] = stats.get("paragrafos_fundidos", 0) + 1
        else:
            merged.append(el)
    for el in merged:
        if el["t"] == "table":
            el["rows"] = [[norm(c.replace("\n", " ")) for c in r] for r in el["rows"]]
    meta = {"origem": pdf.name, "paginas": doc.page_count, "removidas": removed, "stats": stats}
    if manter_capa_elementos:  # story 2.5; sem a opcao o modelo fica identico ao de antes
        meta.update(manter_capa_elementos=True, paginas_imagem=como_imagem, cabecalho=identification(doc))
    (out / "modelo.json").write_text(json.dumps({"meta": meta, "elementos": merged}, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main():
    perfil = load_perfil(sys.argv[sys.argv.index("--perfil") + 1]) if "--perfil" in sys.argv else None
    pag = None
    if "--paginas" in sys.argv:
        pag = tuple(int(x) for x in sys.argv[sys.argv.index("--paginas") + 1].split("-"))
    print(json.dumps(extrair(sys.argv[1], sys.argv[2], perfil, paginas=pag), ensure_ascii=False))


if __name__ == "__main__":
    main()
