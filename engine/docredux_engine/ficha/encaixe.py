"""R52: imagem que nao cabe no fim da pagina encolhe um pouco para nao deixar buraco (2a passada).

O render nao sabe onde a pagina termina (quem pagina e o LibreOffice). Quando uma imagem nao cabe no
espaco que sobra, ela salta para a pagina seguinte junto com o titulo e o texto presos a ela (R17) e
deixa um buraco grande. Achado na revisao do A1 (2026-11-20): no ex. 04 o cardapio da Aula 15 deixou
60% da pag. 10 em branco e criou uma pagina a mais.

Aqui se le o PDF da 1a passada, acha o primeiro buraco causado por imagem que cabe encolhendo ate
ENCOLHE_MAX, marca `encaixe` (fator de escala) no elemento e o pipeline renderiza de novo. A 2a passada
so fica se a imagem subiu para a pagina do buraco e o total de paginas nao cresceu; senao volta a 1a.
"""
import pymupdf

from .render import CFG

LACUNA_MIN = 80     # pt: buraco menor nao justifica outra passada pelo LibreOffice
ENCOLHE_MAX = 0.15  # a imagem perde no maximo 15% de cada lado
FOLGA = 36          # pt: espaco antes do titulo (22) e entre paragrafos, que o PDF nao mostra no topo.
                    # Medido no ex. 04 (cardapio): 30 pt dava k = 0,946, e 0,96 ja nao cabia; 36 da margem.
PASSADAS_MAX = 2    # cada passada custa uma conversao do LibreOffice
MARGEM = CFG["margem_cm"] / 2.54 * 72


def _caixas(page):
    """Retangulos de conteudo dentro das margens (fora: cabecalho e rodape do DOCX)."""
    lim = page.rect.height - MARGEM
    rs = [pymupdf.Rect(b[:4]) for b in page.get_text("blocks")]
    rs += [d["rect"] for d in page.get_drawings()]
    rs += [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    # linha de resposta e borda horizontal tem altura 0 (Rect "vazio"), mas e conteudo
    return [r for r in rs if (r.width > 0 or r.height > 0) and r.y0 >= MARGEM - 6 and r.y1 <= lim + 6]


def _imagens(doc):
    """[(pagina, Rect)] na ordem do DOCX (pagina, depois y, depois x)."""
    out = []
    for pno, p in enumerate(doc):
        for i in sorted(p.get_image_info(), key=lambda i: (round(i["bbox"][1]), i["bbox"][0])):
            out.append((pno, pymupdf.Rect(i["bbox"])))
    return out


def _ordem(model):
    """Indices dos elementos que viram figura no DOCX, na ordem do render."""
    return [i for i, e in enumerate(model["elementos"]) if e["t"] in ("img", "pagina")]


def candidato(pdf, model, pular=()):
    """Primeiro buraco que uma imagem encolhida resolve. Devolve {"idx": [...], "k", "pagina"} ou None.

    pagina: indice (0-based) da pagina do buraco, para onde a imagem deve subir.
    pular: indices de elementos ja tentados sem sucesso."""
    els, ordem = model["elementos"], _ordem(model)
    with pymupdf.open(pdf) as doc:
        imgs = _imagens(doc)
        if len(imgs) != len(ordem):  # mapeamento PDF -> modelo incerto: nao mexe
            return None
        for pno in range(doc.page_count - 1):
            a, b = _caixas(doc[pno]), _caixas(doc[pno + 1])
            if not a or not b:
                continue
            lacuna = doc[pno].rect.height - MARGEM - max(r.y1 for r in a)
            prox = [(n, r) for n, (pg, r) in enumerate(imgs) if pg == pno + 1]
            if lacuna < LACUNA_MIN or not prox:
                continue
            y0 = prox[0][1].y0
            linha = [(n, r) for n, r in prox if abs(r.y0 - y0) < 3]  # imagens lado a lado: mesmo paragrafo
            grupo = [els[ordem[n]] for n, _ in linha]
            if any(e["t"] != "img" or e.get("manter_tamanho") or e.get("encaixe") for e in grupo) \
                    or any(ordem[n] in pular for n, _ in linha):
                continue  # pagina inteira, figura com texto (R7), ja encaixada ou ja tentada
            altura = max(r.height for _, r in linha)
            acima = y0 - min(r.y0 for r in b)  # titulo e texto presos a imagem (R17)
            k = (lacuna - acima - FOLGA) / altura
            if 1 - ENCOLHE_MAX <= k < 1:
                return {"idx": [ordem[n] for n, _ in linha], "k": round(k, 3), "pagina": pno}
    return None


def aceito(pdf, model, alvo, paginas_antes):
    """A 2a passada fica se a imagem subiu para a pagina do buraco e o total de paginas nao cresceu."""
    ordem = _ordem(model)
    with pymupdf.open(pdf) as doc:
        imgs = _imagens(doc)
        if len(imgs) != len(ordem) or doc.page_count > paginas_antes:
            return False
        return imgs[ordem.index(alvo["idx"][0])][0] == alvo["pagina"]
