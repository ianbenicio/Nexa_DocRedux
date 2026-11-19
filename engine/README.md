# engine/

Pacote Python `docredux_engine`: o motor determinístico do DocRedux como biblioteca (contrato do spec §4). Unidade 2.

## Instalação

```bash
cd engine
pip install -e ".[test]"        # Python >= 3.12, PyMuPDF 1.28.x, python-docx 1.2.x
```

Precisa do LibreOffice (DOCX → PDF) e da fonte **Carlito** (métrica da Calibri, Q13). No Windows o motor acha o
`soffice` no caminho padrão; no Linux, pelo `PATH`. A imagem `engine/Dockerfile` já traz os dois.

## Variáveis de ambiente

| Variável | Para quê | Padrão |
|---|---|---|
| `DOCREDUX_SOFFICE` | caminho do `soffice` | `PATH`, depois o caminho padrão do SO |
| `DOCREDUX_PERFIS` | diretório dos perfis JSON | `engine/perfis/` |
| `DOCREDUX_FIXTURES` | PDFs dos ex. 01–05 (fora do git) para testes e regressão | — (testes que precisam deles são pulados) |

## Contrato (`docredux_engine.api`)

```python
from docredux_engine.api import triar, processar, folha_contato, validar_perfil, carregar_perfis, Opcoes

perfis = carregar_perfis()
t = triar("doc.pdf", perfis)                 # Triagem: paginas, palavras, tipo, perfil_id (None = desconhecido), recusa
perfil = next(p for p in perfis if p["nome"] == t.perfil_id)
res = processar("doc.pdf", Opcoes(docx=True), perfil, "saida/")
res.pdf, res.docx, res.relatorio.to_json()   # relatório também gravado em saida/relatorio.json
```

- Recusas da triagem: `escaneado`, `protegido`, `corrompido`, `excede_paginas` (> 300), `excede_tamanho` (> 80 MB).
- `Opcoes.modo="sintese"` só vale para relatório (Q4); `fracao` em 0.20 / 0.35 / 0.50.
- Modo original: cobertura < 99,5% → `relatorio.gate = "bloqueado"` com `motivo_gate`.
- Erros: `MotorErro` (base), `PdfRecusado(motivo)`, `PerfilInvalido(erros)`, `RenderFalhou`.
- `validar_perfil(perfil)` → lista de erros (`[]` = válido), contra `docredux_engine/perfil.schema.json`.

### Opção `manter_capa_elementos` (story 2.5)

`Opcoes(manter_capa_elementos=True)` (CLI `--manter-capa-elementos`) preserva a identidade visual. Desligada, a saída é idêntica ao baseline.

| Regra | Onde | Ligada |
|---|---|---|
| R1 capa/contracapa | `ficha/extract.py` `is_cover_or_blank()` | viram imagem de página inteira (`{"t": "pagina"}`) |
| R5 decorativos | `ficha/extract.py` `vector_figures()` | entram como figura |
| R12 cabeçalho corrido | `ficha/extract.py` `page_items()`; `relatorio/extract.py` `page_lines()` | `meta.cabecalho` repetido no cabeçalho do DOCX |
| R44 foto / arte | `relatorio/extract.py` `page_removal()` | viram imagem de página inteira |

Continuam: páginas em branco (R11) e sem conteúdo (R44 `sem_conteudo`), número de página, aba lateral. As páginas-imagem ficam em `meta.paginas_imagem` e fora da cobertura (texto em imagem, como R7). Mapa completo: `REGRAS.md` do acervo.

## CLI (como o worker chama, DT-5)

Uma linha JSON no stdout; avisos e progresso no stderr; código 1 em erro (o JSON traz `erro`, `mensagem`).

```bash
python -m docredux_engine triar doc.pdf
python -m docredux_engine processar doc.pdf saida/ [--perfil mathema-saeb] [--modo sintese --fracao 0.35] [--manter-capa-elementos] [--docx]
python -m docredux_engine folha doc.pdf saida/ [--paginas 1,2,3]
python -m docredux_engine validar-perfil perfis/mathema-saeb.json
```

## Testes

```bash
pytest                                       # contrato (PDFs sintéticos) + ex. 01–05 se DOCREDUX_FIXTURES existir
python tests/regressao.py                    # ex. 01–05 contra tests/baseline.json (páginas e cobertura)
```

Container (mesma regressão em Linux):

```bash
docker build -t docredux-engine engine/
docker run --rm -v "<pasta dos exemplos>:/fixtures:ro" docredux-engine python tests/regressao.py
```

## Layout

| Módulo | Origem (`scripts/` do acervo) |
|---|---|
| `ficha/extract.py`, `ficha/render.py` | `extract.py`, `render_docx.py` |
| `relatorio/extract.py`, `relatorio/render.py` | `extract_relatorio.py`, `render_relatorio.py` |
| `verify/ficha.py`, `verify/relatorio.py` | `verify.py`, `verify_relatorio.py` |
| `sintese/extrativa.py` | `sintese_extrativa.py` |
| `folha.py` | `folha.py` |
| `soffice.py`, `pipeline.py`, `api.py`, `perfil.py`, `cli.py` | novos (unidade 2) |
