"""Flags de extração de texto do PyMuPDF usadas pelo motor (story 2.8).

`page.get_text("dict")` traz por padrão os blocos de imagem com os bytes de cada imagem
(TEXT_PRESERVE_IMAGES). O motor só lê as linhas de texto desses dicts, e copiar as imagens a cada
chamada dominava a extração: no ex. 05 (64 MB, 294 páginas), 21,6 s só em `get_text("dict")` de página
inteira contra 0,4 s sem as imagens. As imagens são tratadas à parte (get_image_info, get_pixmap).

Sem os blocos de imagem, o MuPDF pode juntar dois blocos de texto que a imagem separava; as linhas,
spans e coordenadas são as mesmas. O motor não depende do agrupamento em blocos.
"""
import pymupdf

DICT = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
