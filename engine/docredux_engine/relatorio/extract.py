"""DocRedux - extrator do tipo `relatorio` (R32-R48): PDF -> modelo.json.

Entra por ficha/extract.py (extrair) quando o perfil tem tipo=relatorio. Nao usa as regras de ficha
(R4, R8, R10, R14, R16, R18, R19, R21-R27, RE_Q): relatorio tem paragrafo por recuo (R34),
dehifenizacao (R35), runs com negrito/italico (R36), bloco legenda+figura+fonte (R37),
imagem por bbox (R38), notas por capitulo (R40), referencias (R41), sumario regenerado (R42),
siglas em tabela (R43), paginas decorativas (R44), faixa de cabecalho/rodape (R12).

Elementos do modelo: id, front, h1..h4, p, quote, bullet, ref, fig(caption, body, fontes),
table, img, fonte, siglas, toc, notas.  Runs: [[texto, flags]], flags 1=negrito 2=italico 4=sobrescrito.
"""
import collections, json, re, sys
from pathlib import Path
import pymupdf

LIG = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "\xa0": " ", "­": "", "\t": " "}
B, I, SUP = 1, 2, 4
RE_NUM_NOTA = re.compile(r"^\d{1,3}$")
RE_SURNAME = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý'’\.\- ]{1,}[,.] ")


def clean(s):
    for k, v in LIG.items():
        s = s.replace(k, v)
    return s


def runs_text(runs):
    return "".join(r[0] for r in runs)


def is_url_tail(t):
    tok = t.rsplit(" ", 1)[-1]
    return bool(re.match(r"(https?://|www\.)", tok) or "://" in tok or ("/" in tok and "." in tok))


def join_str(a, b):
    """R35: junta duas linhas consecutivas. Retorna (a', sep, b'): a' pode perder o hifen final e b' o inicial.
    'circunstan-'+'cia' -> circunstancia; 'pre-'+'-escola' -> pre-escola (hifen repetido, convencao pt-BR);
    URL quebrada em '/', '-', '.', '_' -> sem espaco."""
    if not a:
        return a, "", b
    url = is_url_tail(a)
    if a.endswith("-") and len(a) >= 2 and a[-2].isalnum() and not url:
        if b.startswith("-"):
            return a, "", b[1:]
        if b[:1].islower() and a[-2].isalpha():
            return a[:-1], "", b
        return a, "", b
    if url and a[-1] in "/-._=?&#%":
        if a.endswith("-") and b.startswith("-"):
            b = b[1:]
        return a, "", b
    return a, " ", b


def join_text(a, b):
    a2, sep, b2 = join_str(a, b)
    return a2 + sep + b2


def join_runs(a, b):
    """junta duas listas de runs aplicando a dehifenizacao nas bordas"""
    if not a:
        return [list(r) for r in b]
    ta, tb = runs_text(a), runs_text(b)
    a2, sep, b2 = join_str(ta, tb)
    a = [list(r) for r in a]; b = [list(r) for r in b]
    if a2 != ta:  # tirou o hifen final
        a[-1][0] = a[-1][0][:-1]
    if b2 != tb:  # tirou o hifen inicial
        b[0][0] = b[0][0][1:]
    if sep:
        b[0][0] = sep + b[0][0]
    out = a + b
    merged = []
    for r in out:
        if not r[0]:
            continue
        if merged and merged[-1][1] == r[1]:
            merged[-1][0] += r[0]
        else:
            merged.append(r)
    return merged


# ---------------------------------------------------------------- linhas
def page_lines(page, perfil, stats=None):
    """Linhas de texto horizontais com runs. Corta a faixa do cabecalho corrido (baseline < faixa_topo_y)
    e do numero de pagina (baseline > faixa_rodape_y) por POSICAO (R12), nao por regex."""
    top, bot = perfil["faixa_topo_y"], perfil["faixa_rodape_y"]
    out, hdr = [], []
    for blk in page.get_text("dict")["blocks"]:
        for ln in blk.get("lines", []):
            if abs(ln["dir"][1]) > 0.1:
                continue
            spans = [s for s in ln["spans"] if s["text"] != ""]
            if not "".join(s["text"] for s in spans).strip():
                continue
            base = spans[0]["origin"][1]
            txt = clean("".join(s["text"] for s in spans)).strip()
            if base < top:
                hdr.append((txt, ln["bbox"][0]))
                continue
            if base > bot:
                continue
            runs = []
            main = None
            prev_sp = None
            for s in spans:
                fl = 0
                fn = s["font"].split("+")[-1]
                if s["flags"] & 16 or "Bold" in fn:
                    fl |= B
                if s["flags"] & 2 or "Italic" in fn:
                    fl |= I
                sup = bool(s["flags"] & 1) and s["size"] < 8 and clean(s["text"]).strip().isdigit()
                if sup:
                    fl |= SUP
                elif main is None and s["text"].strip():
                    main = s
                t = clean(s["text"])
                if prev_sp is not None and s["bbox"][0] - prev_sp["bbox"][2] > 1.5 and runs and not runs[-1][0].endswith(" ") and not t.startswith(" "):
                    t = " " + t  # span colado sem espaco (ex.: 'Educação' + 'MEC' do CIP)
                prev_sp = s
                if runs and runs[-1][1] == fl:
                    runs[-1][0] += t
                else:
                    runs.append([t, fl])
            if main is None:
                main = spans[0]
            runs[0][0] = runs[0][0].lstrip(); runs[-1][0] = runs[-1][0].rstrip()
            runs = [r for r in runs if r[0]]
            txt = runs_text(runs)
            first = spans[0]
            out.append({"bb": pymupdf.Rect(ln["bbox"]), "y": base, "x0": ln["bbox"][0], "runs": runs, "text": txt,
                        "font": main["font"].split("+")[-1], "size": round(main["size"], 1),
                        "first_size": round(first["size"], 1), "first_text": clean(first["text"]).strip(),
                        "first_font": first["font"].split("+")[-1]})
    out.sort(key=lambda l: (round(l["y"] / 1.5), l["x0"]))
    return out, hdr


def gray_bands(page):
    r = []
    for d in page.get_drawings():
        f = d.get("fill")
        rc = d["rect"]
        if f and len(f) == 3 and 0.7 <= min(f) and max(f) < 0.92 and max(f) - min(f) < 0.05 and rc.width > 150 and 8 < rc.height < 35:
            r.append(rc)
    return r


# ---------------------------------------------------------------- imagens e tabelas
def page_images(page):
    """R38: imagem raster = bbox da propria imagem (sem diff de pixels). Descarta tiras < 5 pt (sangria),
    foto de pagina inteira (tratada na remocao de pagina) e agrupa imagens coladas (<= 8 pt)."""
    rects = []
    for im in page.get_image_info():
        r = pymupdf.Rect(im["bbox"]) & page.rect
        if r.is_empty or r.width < 5 or r.height < 5 or r.get_area() < 400:
            continue
        if any(abs(r.x0 - q.x0) < 1 and abs(r.y0 - q.y0) < 1 and abs(r.x1 - q.x1) < 1 and abs(r.y1 - q.y1) < 1 for q in rects):
            continue
        rects.append(r)
    merged = True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if (rects[i] + (-8, -8, 8, 8)).intersects(rects[j]):
                    rects[i] = rects[i] | rects[j]; del rects[j]; merged = True; break
            if merged:
                break
    return rects


def table_cells(page, t):
    """Tabela vetorial -> grade logica (bordas das celulas), com mesclagem e preenchimento.
    Retorna {"nrows","ncols","cells":[{r,c,rs,cs,text,fill,size}], "bbox"}."""
    rects = [pymupdf.Rect(c) for row in t.rows for c in row.cells if c is not None]
    if not rects:
        return None
    xs = sorted({round(v, 0) for r in rects for v in (r.x0, r.x1)})
    ys = sorted({round(v, 0) for r in rects for v in (r.y0, r.y1)})

    def snap(vals):
        out = []
        for v in vals:
            if not out or v - out[-1] > 2.5:
                out.append(v)
        return out
    xs, ys = snap(xs), snap(ys)

    def idx(vals, v):
        return min(range(len(vals)), key=lambda i: abs(vals[i] - v))
    fills = [(d["rect"], d["fill"]) for d in page.get_drawings() if d.get("fill") and d["rect"].width > 6 and d["rect"].height > 4]
    words = page.get_text("words")
    cells, used = [], set()
    for r in sorted(rects, key=lambda r: (r.y0, r.x0)):
        c0, c1 = idx(xs, r.x0), idx(xs, r.x1)
        r0, r1 = idx(ys, r.y0), idx(ys, r.y1)
        if c1 <= c0 or r1 <= r0 or (r0, c0) in used:
            continue
        used.add((r0, c0))
        inside = [w for k, w in enumerate(words) if k not in used and r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
        for w in inside:
            used.add(words.index(w) if False else id(w))
        inside.sort(key=lambda w: (round(w[1] / 3), w[0]))
        # linhas
        lines, cur = [], []
        for w in inside:
            if cur and abs(w[1] - cur[-1][1]) > 3:
                lines.append(cur); cur = []
            cur.append(w)
        if cur:
            lines.append(cur)
        text = ""
        for ln in lines:
            lt = " ".join(x[4] for x in ln)
            text = join_text(text, lt) if text else lt
        cx, cy = r.x0 + 1.5, r.y0 + 1.5
        fill = None
        best = None
        for fr, fc in fills:
            if fr.contains(pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)) and (best is None or fr.get_area() < best):
                best = fr.get_area(); fill = fc
        sp = [s_ for blk in page.get_text("dict", clip=r + (1, 1, -1, -1))["blocks"] for ln in blk.get("lines", []) for s_ in ln["spans"] if s_["text"].strip()]
        cor = "%06X" % sp[0]["color"] if sp else "231F20"
        neg = bool(sp) and all(("Bold" in s_["font"] or s_["flags"] & 16) for s_ in sp)
        cells.append({"r": r0, "c": c0, "rs": r1 - r0, "cs": c1 - c0, "text": clean(text).strip(), "cor": cor, "neg": neg,
                      "fill": "%02X%02X%02X" % tuple(int(round(x * 255)) for x in fill[:3]) if fill and len(fill) == 3 else None})
    # tamanho de fonte do texto da tabela
    sizes = []
    for blk in page.get_text("dict", clip=pymupdf.Rect(t.bbox))["blocks"]:
        for ln in blk.get("lines", []):
            for s in ln["spans"]:
                if s["text"].strip():
                    sizes.append(round(s["size"], 1))
    size = collections.Counter(sizes).most_common(1)[0][0] if sizes else 8
    return {"nrows": len(ys) - 1, "ncols": len(xs) - 1, "cells": cells, "bbox": [round(v, 1) for v in t.bbox],
            "colw": [round(xs[i + 1] - xs[i], 1) for i in range(len(xs) - 1)], "size": size}


def imgs_in(imgs, reg):
    return any(reg.intersects(r) for r in imgs)


def capish(l):
    """linha de legenda: negrito (Cambria-Bold) ou CambriaMath (a legenda de alguns graficos vem nessa fonte)"""
    return all(r[1] & B for r in l["runs"]) or l["font"].startswith("CambriaMath")


def side_by_side(rects):
    """Grupos de >=2 visuais lado a lado (sobreposicao vertical >= 50%, colunas distintas)."""
    rs = sorted(rects, key=lambda r: r.x0)
    groups = []
    for r in rs:
        for g in groups:
            if any(min(q.y1, r.y1) - max(q.y0, r.y0) >= 0.5 * min(q.height, r.height) and r.x0 >= q.x1 - 2 for q in g["rects"]):
                g["rects"].append(r); break
        else:
            groups.append({"rects": [r]})
    out = []
    for g in groups:
        if len(g["rects"]) < 2:
            continue
        rr = sorted(g["rects"], key=lambda r: r.x0)
        out.append({"iy0": min(r.y0 for r in rr), "iy1": max(r.y1 for r in rr), "top": min(r.y0 for r in rr) - 75, "bot": max(r.y1 for r in rr) + 80,
                    "x0": min(r.x0 for r in rr), "x1": max(r.x1 for r in rr),
                    "bounds": [(rr[i].x1 + rr[i + 1].x0) / 2 for i in range(len(rr) - 1)]})
    return out


def col_of(g, xc):
    return sum(1 for b in g["bounds"] if xc > b)


def grid_table(page, rect):
    """Tabela sem grade detectavel: agrupa palavras da regiao (legenda -> fonte) em linhas e celulas,
    junta linhas de continuacao de cabecalho e define colunas pela linha com mais celulas."""
    ws = [w for w in page.get_text("words") if rect.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
    if len(ws) < 4:
        return None
    ws.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows = []
    for w in ws:
        yc = (w[1] + w[3]) / 2
        if rows and abs(yc - rows[-1]["y"]) <= 3.2:
            rows[-1]["w"].append(w); rows[-1]["y"] = (rows[-1]["y"] * (len(rows[-1]["w"]) - 1) + yc) / len(rows[-1]["w"])
        else:
            rows.append({"y": yc, "w": [w]})
    for r in rows:
        r["w"].sort(key=lambda w: w[0]); cells = []
        for w in r["w"]:
            if cells and w[0] - cells[-1]["x1"] < 6:
                cells[-1]["t"] += " " + w[4]; cells[-1]["x1"] = max(cells[-1]["x1"], w[2])
            else:
                cells.append({"x0": w[0], "x1": w[2], "t": w[4]})
        r["c"] = cells
    merged = []
    for r in rows:  # linha de continuacao (ex.: 'potavel' sob 'Sem agua')
        if merged and r["y"] - merged[-1]["y"] <= 11.5 and len(r["c"]) <= len(merged[-1]["c"]) and                 all(any(min(c["x1"], q["x1"]) - max(c["x0"], q["x0"]) > 0 for q in merged[-1]["c"]) for c in r["c"]) and len(r["c"]) < len(merged[-1]["c"]):
            for c in r["c"]:
                q = max(merged[-1]["c"], key=lambda q: min(c["x1"], q["x1"]) - max(c["x0"], q["x0"]))
                q["t"] += " " + c["t"]; q["x0"] = min(q["x0"], c["x0"]); q["x1"] = max(q["x1"], c["x1"])
            merged[-1]["y"] = r["y"]
        else:
            merged.append(r)
    rows = merged
    ref = max(rows, key=lambda r: len(r["c"]))["c"]
    nc = len(ref)
    bounds = [-1e9] + [(ref[i]["x1"] + ref[i + 1]["x0"]) / 2 for i in range(nc - 1)] + [1e9]
    fills = [(d["rect"], d["fill"]) for d in page.get_drawings() if d.get("fill") and d["rect"].width > 6 and d["rect"].height > 4]
    cells_out = []
    for ri, r in enumerate(rows):
        last_c = -1
        for c in r["c"]:
            cols = [i for i in range(nc) if min(c["x1"], ref[i]["x1"]) - max(c["x0"], ref[i]["x0"]) > 1]
            if not cols:
                xc = (c["x0"] + c["x1"]) / 2
                cols = [max(0, min(nc - 1, sum(1 for b in bounds[1:-1] if xc > b)))]
            c0, c1 = cols[0], cols[-1]
            c0 = max(c0, last_c + 1)
            if c0 >= nc:
                continue
            c1 = max(c1, c0); last_c = c1
            pt = pymupdf.Point((c["x0"] + c["x1"]) / 2, r["y"])
            fill, best = None, None
            for fr, fc in fills:
                if fr.contains(pt) and (best is None or fr.get_area() < best):
                    best = fr.get_area(); fill = fc
            sp = [s_ for blk in page.get_text("dict", clip=pymupdf.Rect(c["x0"] - 1, r["y"] - 5, c["x1"] + 1, r["y"] + 5))["blocks"]
                  for ln in blk.get("lines", []) for s_ in ln["spans"] if s_["text"].strip()]
            cells_out.append({"r": ri, "c": c0, "rs": 1, "cs": c1 - c0 + 1, "text": clean(c["t"]).strip(),
                              "cor": "%06X" % sp[0]["color"] if sp else "231F20",
                              "neg": bool(sp) and all(("Bold" in s_["font"] or s_["flags"] & 16) for s_ in sp),
                              "fill": "%02X%02X%02X" % tuple(int(round(x * 255)) for x in fill[:3]) if fill and len(fill) == 3 else None})
    x0 = min(c["x0"] for r in rows for c in r["c"]); x1 = max(c["x1"] for r in rows for c in r["c"])
    sizes = [round(w[3] - w[1], 0) for w in ws]
    colw = []
    for i in range(nc):
        lo = max(bounds[i], x0 - 3); hi = min(bounds[i + 1], x1 + 3)
        colw.append(round(hi - lo, 1))
    return {"nrows": len(rows), "ncols": nc, "cells": cells_out, "bbox": [round(x0, 1), round(rect.y0, 1), round(x1, 1), round(rect.y1, 1)],
            "colw": colw, "size": 8}


# ---------------------------------------------------------------- pagina
def page_removal(page, lines, perfil):
    """R44/R1: pagina sem corpo (so cabecalho/numero/'Foto:'), ou foto/arte de pagina inteira com quase nenhum texto."""
    body = [l for l in lines if not l["text"].startswith("Foto:")]
    words = sum(len(l["text"].split()) for l in body)
    big = 0.0
    for im in page.get_image_info():
        big = max(big, (pymupdf.Rect(im["bbox"]) & page.rect).get_area() / page.rect.get_area() * 100)
    if words == 0:
        return "sem_conteudo" if big < perfil["foto_pagina_inteira_min_pct"] else "foto"
    if big >= perfil["foto_pagina_inteira_min_pct"] and words < perfil["pagina_vazia_max_palavras"]:
        return "foto_ou_arte"
    return None


def parse_page(doc, pno, perfil, ctx, outdir, stats):
    page = doc[pno]
    H = page.rect.height
    lines, hdr = page_lines(page, perfil)
    why = page_removal(page, lines, perfil)
    if why:
        return None, why, hdr
    left = perfil["margem_esq_par"] if (pno + 1) % 2 == 0 else perfil["margem_esq_impar"]
    bands = gray_bands(page)
    cfg_leg = re.compile(perfil["legenda_regex"])
    cfg_fonte = re.compile(perfil["fonte_regex"])

    imgs = page_images(page)
    # tabelas vetoriais ligadas a uma legenda Tabela/Quadro logo acima (R37). Se find_tables nao cobre as
    # palavras entre a legenda e a fonte, a regiao vira tabela por grade de texto (grid_table).
    tabs = []
    caps_tab = [l for l in lines if cfg_leg.match(l["text"]) and l["text"].split()[0] in ("Tabela", "Quadro")]
    if caps_tab:
        found = page.find_tables().tables
        re_fim = re.compile(r"^(Fonte|Nota|Notas|Elaboração|Legenda)\b")
        for cl in caps_tab:
            yend = cl["y"]
            for l2 in lines:  # ultima linha contigua da legenda
                if l2["y"] > yend and l2["y"] - yend <= 16 and capish(l2) and not cfg_leg.match(l2["text"]) and abs(l2["x0"] - cl["x0"]) < 200:
                    yend = l2["y"]
            fim = next((l2 for l2 in lines if l2["y"] > yend + 4 and re_fim.match(l2["text"]) and l2["size"] <= 10.6), None)
            c = [t for t in found if t.bbox[1] >= cl["bb"].y1 - 3 and t.bbox[1] - cl["bb"].y1 < 80 and t.row_count >= 2 and t.col_count >= 2]
            t = min(c, key=lambda t: t.bbox[1]) if c else None
            tc = table_cells(page, t) if t else None
            reg = None
            if fim is not None:
                reg = pymupdf.Rect(page.rect.x0, yend + 4, page.rect.x1, fim["y"] - fim["size"] - 2)
                out_n = len([w for w in page.get_text("words") if reg.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
                             and (t is None or not pymupdf.Rect(t.bbox).contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)))])
                if out_n > 4 and (t is not None or not imgs_in(imgs, reg)):
                    g = grid_table(page, reg)
                    if g:
                        tc, t = g, None; stats["tabelas_grade_texto"] += 1
                        tabs.append((None, tc, pymupdf.Rect(reg.x0, reg.y0, reg.x1, reg.y1)))
                        continue
            if tc and t is not None and t not in [x[0] for x in tabs]:
                tabs.append((t, tc, pymupdf.Rect(t.bbox)))
    tab_rects = [tr for _, _, tr in tabs]
    lines = [l for l in lines if not any((tr + (-1, -1, 1, 1)).contains(pymupdf.Point((l["bb"].x0 + l["bb"].x1) / 2, l["y"] - 2)) for tr in tab_rects)]

    # R46: figuras lado a lado (mesma faixa vertical, colunas distintas) -> legendas/fontes por coluna
    nthr0 = perfil["nota_rodape"]["size_numero_max"]
    groups = side_by_side(imgs + tab_rects)
    groups.sort(key=lambda g: g["iy0"])
    for g0, g1 in zip(groups, groups[1:]):
        ls = [l["bb"].y0 for l in lines if cfg_leg.match(l["text"]) and g0["iy1"] < l["y"] < g1["iy0"]]
        if ls:
            ymid = min(ls) - 2
            g0["bot"] = min(g0["bot"], ymid); g1["top"] = max(g1["top"], ymid)
    for g in groups:
        win = [i for i, l in enumerate(lines) if g["top"] <= l["y"] <= g["bot"] and (l["bb"].x1 - l["bb"].x0) < 0.65 * (g["x1"] - g["x0"])
               and g["x0"] - 16 <= l["x0"] <= g["x1"] + 16 and l["first_size"] > nthr0
               and (capish(l) or l["size"] <= 9.6)]
        srt = sorted((lines[i] for i in win), key=lambda l: (col_of(g, (l["bb"].x0 + l["bb"].x1) / 2), l["y"], l["x0"]))
        for i, l in zip(win, srt):
            lines[i] = l
    # notas de rodape: 1a linha com 1o span pequeno e numerico, na parte baixa
    nthr = perfil["nota_rodape"]["size_numero_max"]
    fn_y = None
    for l in lines:
        if l["first_size"] <= nthr and RE_NUM_NOTA.match(l["first_text"]) and l["y"] > H * 0.55 and len(l["text"]) > len(l["first_text"]) + 2:
            fn_y = l["y"] if fn_y is None else min(fn_y, l["y"])
    fns = []
    fz = []
    ev = []  # eventos (y0, dict)
    cur = None  # paragrafo em construcao
    last_y = None

    def flush():
        nonlocal cur
        if cur is not None:
            ev.append((cur["y0"], cur)); cur = None

    first_text_on_page = True
    for l in lines:
        t, size, font = l["text"], l["size"], l["font"]
        runs = l["runs"]
        # --- zona de notas de rodape: coletada e tratada depois, por coluna (R40)
        if fn_y is not None and l["y"] >= fn_y - 0.5:
            fz.append(l)
            continue
        # --- H1
        if size >= perfil["h1"]["size_min"]:
            if cur is not None and cur["k"] == "h" and cur["lvl"] == 1 and l["y"] - cur["y1"] < 30:
                cur["text"] = join_text(cur["text"], t); cur["y1"] = l["y"]
                continue
            flush()
            cur = {"k": "h", "lvl": 1, "text": t, "y0": l["y"], "y1": l["y"]}
            ctx["refs"] = False
            if t in perfil["secoes_h1"]:
                ctx["secao"] = perfil["secoes_h1"][t]
            elif re.match(perfil["capitulo_regex"], t):
                ctx["secao"] = "capitulo"
            else:
                ctx["secao"] = "outro"
            continue
        sec = ctx["secao"]
        if sec == "sumario":  # R42: o sumario estatico sai; e regenerado a partir dos H1
            ctx["sumario"].append([page.number + 1, t])
            continue
        if sec == "siglas":
            if font.startswith("Archer-Bold") and l["x0"] < 140:
                ev.append((l["bb"].y0, {"k": "sigla", "text": t, "y0": l["y"], "y": l["y"]}))
            else:
                ev.append((l["bb"].y0, {"k": "sigla_sig", "text": t, "y0": l["y"], "y": l["y"]}))
            continue
        if sec == "front":  # pre-textuais (expediente, CIP): blocos compactos
            if cur is not None and cur["k"] == "front" and l["y"] - last_y <= 16:
                cur["lines"].append(t)
            else:
                flush(); cur = {"k": "front", "lines": [t], "y0": l["y"]}
            last_y = l["y"]
            continue
        in_band = any(b.contains(pymupdf.Point((l["bb"].x0 + l["bb"].x1) / 2, (l["bb"].y0 + l["bb"].y1) / 2)) for b in bands)
        bold = all(r[1] & B for r in runs)
        # --- H2: faixa cinza + titulo conhecido, ou Cambria-Bold 14
        if (in_band and bold and font.startswith("Archer")) or (font.startswith("Cambria-Bold") and size >= perfil["h2"]["cambria_bold_size"] - 0.1) \
                or (t in perfil["h2"]["textos"] and bold and l["x0"] <= left + 2):
            if cur is not None and cur["k"] == "h" and cur["lvl"] == 2 and l["y"] - cur["y1"] < 20:
                cur["text"] = join_text(cur["text"], t); cur["y1"] = l["y"]
                continue
            flush()
            cur = {"k": "h", "lvl": 2, "text": t, "y0": l["y"], "y1": l["y"]}
            ctx["refs"] = (t == perfil["referencia"]["secao"])
            continue
        # --- H3/H4
        if font.startswith("Archer-Bold") or font.startswith("Archer-BookItalic"):
            lvl = 3 if (font.startswith("Archer-Bold") and size >= perfil["h3"]["size"] - 0.1) else 4
            if cur is not None and cur["k"] == "h" and cur["lvl"] == lvl and l["y"] - cur["y1"] < 17:
                cur["text"] = join_text(cur["text"], t); cur["y1"] = l["y"]
                continue
            flush()
            cur = {"k": "h", "lvl": lvl, "text": t, "y0": l["y"], "y1": l["y"]}
            continue
        # --- legenda (R37)
        if cfg_leg.match(t) and (bold or font.startswith("CambriaMath")):
            flush()
            cur = {"k": "cap", "text": t, "y0": l["y"], "y1": l["y"], "font": font, "x": l["bb"].x0}
            last_y = l["y"]
            continue
        if cur is not None and cur["k"] == "cap" and (font == cur["font"] or font.startswith("Cambria")) and l["y"] - last_y <= 16 and size >= 9.9 and (bold or font.startswith("CambriaMath")):
            cur["text"] = join_text(cur["text"], t); cur["y1"] = l["y"]; last_y = l["y"]
            continue
        # --- Fonte / Nota
        if cfg_fonte.match(t) and size <= 10.6 and (t.startswith(("Fonte", "Foto", "Elaboração")) or size <= 9.6):
            flush()
            cur = {"k": "fonte", "runs": [list(r) for r in runs], "y0": l["y"], "x": l["bb"].x0}
            last_y = l["y"]
            continue
        if cur is not None and cur["k"] == "fonte" and size <= 9.6 and l["y"] - last_y <= 13.5 and not ctx["refs"]:
            cur["runs"] = join_runs(cur["runs"], runs); last_y = l["y"]
            continue
        # --- referencias (R41)
        if ctx["refs"] and size <= perfil["referencia"]["size_max"]:
            if cur is not None and cur["k"] == "ref" and l["y"] - last_y <= perfil["referencia"]["gap_nova_entrada"]:
                cur["runs"] = join_runs(cur["runs"], runs)
            else:
                flush()
                cur = {"k": "ref", "runs": [list(r) for r in runs], "y0": l["y"], "topo": first_text_on_page}
            last_y = l["y"]; first_text_on_page = False
            continue
        # --- bullet
        if t.startswith("•"):
            flush()
            rr = [list(r) for r in runs]
            rr[0][0] = re.sub(r"^•\s*", "", rr[0][0])
            cur = {"k": "bullet", "runs": [r for r in rr if r[0]], "y0": l["y"], "topo": first_text_on_page}
            last_y = l["y"]; first_text_on_page = False
            continue
        # --- citacao longa
        indent = l["x0"] - left
        if perfil["citacao"]["size_min"] <= size <= perfil["citacao"]["size_max"] and indent >= perfil["citacao"]["recuo_min"]:
            if cur is not None and cur["k"] == "quote" and l["y"] - last_y <= 16:
                cur["runs"] = join_runs(cur["runs"], runs)
            else:
                flush()
                cur = {"k": "quote", "runs": [list(r) for r in runs], "y0": l["y"]}
            last_y = l["y"]; first_text_on_page = False
            continue
        # --- corpo: paragrafo pelo recuo da 1a linha (R34)
        if cur is not None and cur["k"] == "bullet" and l["y"] - last_y <= 17:  # continuacao de item (recuo ~9 pt)
            cur["runs"] = join_runs(cur["runs"], runs); last_y = l["y"]
            continue
        new_par = indent >= 8 or cur is None or cur["k"] not in ("p", "bullet") or l["y"] - last_y > 19
        if re.match(r"^[a-z]\)\s", t):  # alinea colada do Word
            new_par = True
        if new_par:
            flush()
            cur = {"k": "p", "runs": [list(r) for r in runs], "y0": l["y"], "indent": indent >= 8, "topo": first_text_on_page}
        else:
            cur["runs"] = join_runs(cur["runs"], runs)
        last_y = l["y"]; first_text_on_page = False
    flush()

    # notas de rodape: 1 ou 2 colunas (cada nota comeca por um numero pequeno; linhas seguem a coluna da nota)
    starts = sorted({round(l["x0"]) for l in fz if l["first_size"] <= nthr and RE_NUM_NOTA.match(l["first_text"])})
    colf = lambda l: max([i for i, x in enumerate(starts) if x <= l["x0"] + 6] or [0])
    for l in sorted(fz, key=lambda l: (colf(l), l["y"], l["x0"])):
        runs = l["runs"]
        if l["first_size"] <= nthr and RE_NUM_NOTA.match(l["first_text"]):
            rr = [list(r) for r in runs]
            rr[0][0] = rr[0][0][len(l["first_text"]):].lstrip() if rr[0][0].startswith(l["first_text"]) else rr[0][0]
            fns.append({"n": int(l["first_text"]), "runs": [r for r in rr if r[0]], "y0": l["bb"].y0})
        elif fns:
            fns[-1]["runs"] = join_runs(fns[-1]["runs"], runs)

    # imagens
    for k, r in enumerate(imgs):
        fn = f"p{pno+1:03d}_img{k+1}.jpg"
        page.get_pixmap(dpi=perfil.get("img_dpi", 200), clip=r).save(outdir / "img" / fn, jpg_quality=82)
        ev.append((r.y0, {"k": "visual", "y0": r.y0, "y1": r.y1,
                          "body": {"t": "img", "file": fn, "w": round(r.width, 1), "h": round(r.height, 1), "bbox": [round(v, 1) for v in r], "pagina": pno + 1}}))
        stats["imagens"] += 1
    for t, tc, tr in tabs:
        ev.append((tr.y0, {"k": "visual", "y0": tr.y0, "y1": tr.y1, "body": dict(tc, t="table", pagina=pno + 1)}))
        stats["tabelas"] += 1
    def ev_key(y, e):
        for g in groups:
            if e["k"] in ("cap", "fonte", "visual") and g["top"] <= y <= g["bot"]:
                xc = e["x"] if e["k"] != "visual" else (e["body"]["bbox"][0] + e["body"]["bbox"][2]) / 2 if e["body"]["t"] == "img" else (e["body"]["bbox"][0] + e["body"]["bbox"][2]) / 2
                if g["x0"] - 16 <= xc <= g["x1"] + 16:
                    return (g["top"], col_of(g, xc), y)
        return (y, 0, y)
    ev.sort(key=lambda e: ev_key(*e))
    events = [e for _, e in ev]
    # siglas da pagina: pareia sigla + significado
    # bloco legenda + visual + fontes
    out = []
    i = 0
    while i < len(events):
        e = events[i]
        if e["k"] == "cap":
            j = i + 1
            if j < len(events) and events[j]["k"] == "visual":
                blk = {"t": "fig", "caption": e["text"], "body": events[j]["body"], "fontes": []}
                j += 1
                while j < len(events) and events[j]["k"] == "fonte":
                    blk["fontes"].append(events[j]["runs"]); j += 1
                out.append(blk); i = j; stats["figuras_blocos"] += 1
                continue
            out.append({"t": "fig", "caption": e["text"], "body": None, "fontes": []})
            stats["legenda_sem_visual"] += 1
            i += 1
            continue
        if e["k"] == "visual":
            blk = {"t": "fig", "caption": "", "body": e["body"], "fontes": []}
            j = i + 1
            while j < len(events) and events[j]["k"] == "fonte":
                blk["fontes"].append(events[j]["runs"]); j += 1
            out.append(blk); i = j; stats["visual_sem_legenda"] += 1
            continue
        if e["k"] == "h":
            out.append({"t": f"h{e['lvl']}", "text": e["text"], "secao": ctx["secao"] if e["lvl"] == 1 else None})
        elif e["k"] == "fonte":
            out.append({"t": "fonte", "runs": e["runs"]})
        elif e["k"] in ("p", "quote", "bullet", "ref"):
            d = {"t": e["k"], "runs": e["runs"], "pg": pno + 1}  # pg: página de origem (síntese, story 2.4)
            if e["k"] == "p" and not e.get("indent") and e.get("topo"):
                d["continua"] = True
            if e["k"] in ("ref", "bullet") and e.get("topo"):
                d["topo"] = True
            out.append(d)
        elif e["k"] in ("sigla", "sigla_sig"):
            out.append({"t": e["k"], "text": e["text"], "y": e["y"]})
        elif e["k"] == "front":
            out.append({"t": "front", "lines": e["lines"]})
        i += 1
    return {"els": out, "notas": fns}, None, hdr


# ---------------------------------------------------------------- documento
def run(pdf, outdir, doc, perfil, paginas=None, manter=False):
    """manter (story 2.5): paginas-foto e de arte (R44: foto, foto_ou_arte; capa e folha de rosto do ex. 05)
    viram imagem de pagina inteira e o cabecalho corrido (R12) vai para o cabecalho do DOCX. Paginas
    sem_conteudo (so numero/cabecalho) continuam saindo."""
    from ..ficha.extract import pagina_inteira
    pdf = Path(pdf); outdir = Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "img").mkdir(exist_ok=True)
    for old in (outdir / "img").glob("*.jpg"):
        old.unlink()
    a, b = 1, doc.page_count
    if paginas:
        a, b = paginas
    stats = collections.Counter()
    ctx = {"secao": "front", "refs": False, "sumario": []}
    removed, como_imagem = {}, {}
    model = []
    notas_cap = []
    hdrs = collections.Counter()
    for pno in range(a - 1, b):
        res, why, hdr = parse_page(doc, pno, perfil, ctx, outdir, stats)
        for t, x0 in hdr:
            hdrs[t] += 1
        if why and manter and why in ("foto", "foto_ou_arte"):
            model.append(pagina_inteira(doc[pno], pno, outdir / "img", why))
            como_imagem[pno + 1] = why
            continue
        if why:
            removed[pno + 1] = why
            continue
        if any(el["t"] == "h1" for el in res["els"]):  # R40: notas do capitulo anterior fecham antes do novo H1
            flush_notas(model, notas_cap, stats)
        model.extend(res["els"])
        notas_cap.extend(res["notas"])
    flush_notas(model, notas_cap, stats)
    titulo = next((t for t, n in hdrs.most_common() if len(t) > 15 and not t.isdigit()), pdf.stem)
    lead = next((k for k, e in enumerate(model) if e["t"] != "pagina"), len(model))  # capa/rosto antes da identificacao
    model.insert(lead, {"t": "id", "text": titulo, "arquivo": pdf.name})
    extra = {"manter_capa_elementos": True, "paginas_imagem": como_imagem, "cabecalho": titulo} if manter else None
    return finalize(model, doc, pdf, outdir, perfil, stats, removed, a, b, ctx, extra)


def flush_notas(model, notas, stats):
    """R40: notas numeradas num bloco 'Notas' ao fim do capitulo, antes de REFERENCIAS (se houver)."""
    if not notas:
        return
    # inicio do capitulo corrente = ultimo h1
    start = max([i for i, e in enumerate(model) if e["t"] == "h1"] or [0])
    pos = len(model)
    for i in range(start, len(model)):
        if model[i]["t"] == "h2" and model[i]["text"] == "REFERÊNCIAS":
            pos = i
    blk = [{"t": "h2", "text": "NOTAS", "sintetico": True},
           {"t": "notas", "itens": [{"n": n["n"], "runs": n["runs"]} for n in notas]}]
    model[pos:pos] = blk
    stats["notas"] += len(notas)
    notas.clear()


def finalize(model, doc, pdf, outdir, perfil, stats, removed, a, b, ctx, extra=None):
    out = []
    # continuacoes entre paginas: paragrafo sem recuo no topo da pagina emenda no anterior
    for el in model:
        prev = out[-1] if out else None
        if el["t"] == "p" and el.get("continua") and prev and prev["t"] in ("p", "quote"):
            prev["runs"] = join_runs(prev["runs"], el["runs"]); stats["paragrafos_emendados"] += 1
            prev["pg_fim"] = el["pg"]
        elif el["t"] == "ref" and el.get("topo") and prev and prev["t"] == "ref" and \
                not RE_SURNAME.match(runs_text(el["runs"])):
            prev["runs"] = join_runs(prev["runs"], el["runs"]); stats["refs_emendadas"] += 1
            prev["pg_fim"] = el["pg"]
        elif el["t"] == "bullet" and el.get("topo") and prev and prev["t"] == "bullet" and runs_text(el["runs"])[:1].islower():
            prev["runs"] = join_runs(prev["runs"], el["runs"])
            prev["pg_fim"] = el["pg"]
        else:
            out.append(el)
    # siglas: pares (sigla, significado)
    res = []
    cur = None
    for el in out:
        if el["t"] == "sigla":
            cur = {"sigla": el["text"], "sig": ""}
            res.append({"t": "_sig", "d": cur})
        elif el["t"] == "sigla_sig":
            if cur is None:
                cur = {"sigla": "", "sig": el["text"]}; res.append({"t": "_sig", "d": cur})
            else:
                cur["sig"] = join_text(cur["sig"], el["text"]) if cur["sig"] else el["text"]
        else:
            res.append(el); cur = None
    final, tab = [], []
    for el in res:
        if el["t"] == "_sig":
            tab.append(el["d"])
        else:
            if tab:
                final.append({"t": "siglas", "itens": tab}); stats["siglas"] += len(tab); tab = []
            final.append(el)
    if tab:
        final.append({"t": "siglas", "itens": tab}); stats["siglas"] += len(tab)
    # sumario regenerado a partir dos H1 (R42)
    h1s = [e["text"] for e in final if e["t"] == "h1" and e.get("secao") != "sumario"]
    for i, e in enumerate(final):
        if e["t"] == "h1" and e.get("secao") == "sumario":
            final.insert(i + 1, {"t": "toc", "itens": [{"text": t} for t in h1s]})
            break
    for e in final:
        k = e["t"]
        stats["n_" + k] += 1
    meta = {"origem": pdf.name, "paginas": doc.page_count, "recorte": [a, b], "removidas": removed,
            "stats": dict(stats), "tipo": "relatorio", "perfil": perfil["nome"], "sumario_linhas": ctx["sumario"]}
    if extra:
        meta.update(extra)
    (outdir / "modelo.json").write_text(json.dumps({"meta": meta, "elementos": final}, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta
