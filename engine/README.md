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
| `DOCREDUX_LO_POOL` | pasta do pool de perfis do LibreOffice, reaproveitados entre conversões com trava por perfil (story 2.8) | temporário do processo, apagado na saída |
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
- Modo original, integridade numérica (story 2.7): número ou operador entre números do original ausente na saída → também `"bloqueado"`, mesmo com cobertura alta. Detalhe em `verificacao.json` → `numeros` (`verify/numeros.py`). Número de página e números a mais na saída não bloqueiam.
- Erros: `MotorErro` (base), `PdfRecusado(motivo)`, `PerfilInvalido(erros)`, `RenderFalhou`.
- `validar_perfil(perfil)` → lista de erros (`[]` = válido), contra `docredux_engine/perfil.schema.json`.

### Correções experimentais da branch Codex (story 2.6)

- Fichas: páginas com figuras sem texto são preservadas como página-imagem a 200 dpi, mesmo sem
  `manter_capa_elementos`; fundo uniforme, objetos fora da página e filetes de sangria a até 3 pt do
  corte (`BORDA_SANGRIA`) continuam descartáveis. Na 1ª/última página sem texto, arte que cobre ≥ 80% da
  página (`CAPA_IMAGEM_MIN`) é capa/contracapa e segue a R1. Ex. 01–05 seguem o baseline com a opção desligada.
- A síntese preserva delimitadores das frases, usa a contagem DT-21 no relatório e informa quando
  o núcleo obrigatório impede atingir a fração solicitada. As frações aceitas são exatamente 0.20/0.35/0.50.
- A contagem DOCX separa parágrafos/quebras e percorre células físicas uma única vez. Os verificadores
  usam o arquivo produzido na execução atual, sem escolher o primeiro DOCX da pasta.
- A CLI emite JSON ASCII escapado, compatível com leitores UTF-8 em Windows com encoding legado.
- Conversão para PDF sem conteúdo detectável levanta `RenderFalhou`. É uma proteção inicial: ela
  não certifica equivalência de texto, fórmulas, ordem ou imagens, nem substitui a regressão visual.

Evidências, referências e pendências: `../docs/research/motor-referencias-2026-11-19.md` e
`../docs/stories/2.6.integridade-motor-codex.story.md`. O baseline permanece inalterado.

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
python tests/regressao.py                    # ex. 01–05 contra tests/baseline.json (páginas, cobertura e gate)
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
