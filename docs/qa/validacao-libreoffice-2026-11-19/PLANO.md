# Validação real de conversão — continuação da story 2.6

## Escopo

Continuação autorizada pelo responsável em 2026-11-19. Trabalhar somente na branch `codex/motor-integridade`; preservar a `main` e os baselines aprovados.

## Diagnóstico inicial

- Lançadores `soffice.exe`/`soffice.com` ausentes na instalação padrão.
- O `soffice.bin` restante inicia com código `0xC0000135` (dependência ausente), sem versão utilizável.
- Docker Desktop não está em execução.
- A versão de referência 26.8.3.1 foi localizada pelo servidor oficial da Document Foundation. O MSI será verificado pelo SHA-256 publicado no cabeçalho da resposta oficial antes da extração em diretório isolado.
- Não reparar nem substituir a instalação global e não modificar o ambiente Python do checkout original.

## Sequência

- [x] Verificar hash e extrair o runtime isolado; registrar caminho e versão efetivos.
- [x] Gerar um PDF mínimo e confirmar o uso de Carlito na saída.
- [x] Rodar o exemplo 03 na versão original e na branch Codex, com o mesmo runtime.
- [x] Rodar regressão padrão 01–05 e a opção de preservar elementos.
- [x] Rodar suíte completa, incluindo síntese com renderização real.
- [x] Inspecionar amostra visual e sumário do relatório final.
- [x] Registrar diferenças, corrigir regressões reais encontradas e repetir somente as verificações afetadas.

## Critérios

Padrão: páginas 19/14/5/12/142 e coberturas mínimas 99,96/100/100/100/99,97, conforme `engine/tests/baseline.json`. Preservação: conferir também o registro histórico em `DocRedux/REGRAS.md` (27/19/8/17/174), sem substituir o baseline caso o ambiente produza diferença. Comparar a versão original sob o mesmo runtime para separar efeitos de ambiente dos efeitos de código.

Os PDFs e DOCX de saída ficam fora do repositório público. Evidências compactas, versões, hashes e resultados de teste podem ser registrados neste diretório. A ausência de regressão visual não comprova integridade numérica ou ordem dos blocos; M09/M11/M12 continuam como etapas posteriores.

## Preparação confirmada

MSI oficial: SHA-256 `0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94`. Extração administrativa (`msiexec /a /qn TARGETDIR=...`) em `.runtime/libreoffice-26.8.3.1/extraido`, fora dos checkouts. Não houve instalação global. Versão efetiva: `LibreOffice 26.8.3.1 861effc5289d539f15c4c18d1ba8579ffd78728b`.

Copiar os quatro arquivos `Carlito*.ttf` do diretório `Fonts` extraído para `share/fonts/truetype` do próprio runtime fez o PDF mínimo incorporar Carlito. Antes disso a conversão substituía a fonte por Calibri. Não foi necessário registrar fontes no Windows. Manifesto: `ambiente.json`.

O exemplo 03 passou na versão original e na Codex com 16→5 páginas, cobertura 100%, no mesmo runtime. Evidências: `original-03.json` e `codex-03.json`.

## Regressão encontrada e corrigida (revisão do Claude, 2026-11-19)

A primeira rodada da Codex reprovou os ex. 01 (52→21, baseline 19) e 02 (34→16, baseline 14): `codex-demais.json`, e `test_manter::test_ficha_ex01` em `pytest-com-libreoffice.xml`. Na preservação, os mesmos dois ficaram 2 páginas acima do registro histórico (29 e 21). Em `original-demais.json`, a versão original passou com o mesmo runtime, então o efeito vem do código.

Causa: nos dois exemplos, a p. 2 do original é um fundo creme uniforme mais um filete de 0,28 × 158 pt colado à borda direita (x 594,99–595,28), sobra de sangria da gráfica. `has_visual_content` tratou o filete como conteúdo e a página vazia virou página-imagem de 200 dpi.

Correção: objetos (imagem, desenho ou sombreamento) cujo trecho visível cabe inteiro numa faixa de 3 pt junto ao corte (`BORDA_SANGRIA`, `_so_na_borda`) não contam como conteúdo. Testes novos: filete à direita como o do ex. 01, faixa no topo e imagem na borda, que continuam brancas; linha a 10 pt do corte e objeto que entra na página, que continuam como conteúdo. A p. 2 dos ex. 01 e 02 volta a ser `branca`.

Depois da correção, com o mesmo runtime:

| Verificação | Resultado | Evidência |
|---|---|---|
| `test_integridade.py` | 53 passaram (48 + 5 novos) | — |
| Regressão padrão | 5/5 OK: 19/14/5/12/142, coberturas iguais ao baseline | `codex-m01-padrao.json` |
| Preservação | 5/5 OK: 27/19/8/17/174, igual ao registro histórico do `REGRAS.md` | `codex-m01-preservacao.json` |
| Suíte completa com LibreOffice | 89 passaram, 1 pulado (`jsonschema` ausente no venv da API), 535 s | `pytest-m01-com-libreoffice.xml` |

Os arquivos `codex-demais.json`, `preservacao.json` e `pytest-com-libreoffice.xml` ficam como retrato da rodada reprovada.

Ainda pendente:
- inspeção visual amostral e do sumário do relatório final (só o ex. 03 foi comparado pixel a pixel);

## Capa sem texto (R1 × M01), 2026-11-19

Na versão anterior, uma capa feita só de imagem, sem texto, na primeira ou na última página, virava `conteudo_visual` no modo padrão em vez de sair pela R1. Correção: `is_cover_or_blank` testa a capa antes da página branca, e `extrair` só aplica o M01 quando a página não é capa nem branca.

Na 1ª e na última página sem texto, contam como capa:
- arte que cobre ≥ 80% da página (`CAPA_IMAGEM_MIN`);
- capa vetorial com > 500 desenhos, que já era a regra antiga.

A mesma arte no meio do documento continua `conteudo_visual`. Uma figura pequena na última página também continua conteúdo.

Testes: `test_capa_so_com_imagem_segue_r1` (duas opções) e `test_capa_vetorial_sem_texto_segue_r1`. Resultados:

| Verificação | Resultado |
|---|---|
| `test_integridade.py` | 56 passaram |
| `test_manter.py` | 4 passaram |
| Regressão padrão das fichas 01–04 | 19/14/5/12 (`codex-capa-padrao.json`) |
| Preservação das fichas 01–04 | 27/19/8/17 (`codex-capa-preservacao.json`) |

O ex. 05 (relatório) usa `relatorio/extract.py`, que essa mudança não toca.

## Inspeção visual (main × Codex), 2026-11-19

Comparação dos PDFs de saída da `main` (`871507d`, motor igual ao `984b046`) com os da branch, no mesmo runtime, página a página: pixels a 96 dpi e texto extraído. Evidência: `visual-main-x-codex.json`.

| Saída | Páginas | Diferenças |
|---|---|---|
| Fichas 01–04, modo padrão (`f1060b9`) | 19/14/5/12 | nenhuma, nem em pixels nem em texto |
| Ex. 05, modo padrão | 142 | nenhuma |
| Ex. 05, síntese de 35% | 81 | nenhuma no PDF |

O PDF entregue é o mesmo do baseline aprovado, pixel a pixel, em 273 páginas. Na síntese só muda o relatório:
- `palavras_original` vai de 93.166 para 91.037, a contagem da DT-21 (M02);
- `palavras_saida` vai de 32.584 para 32.603, porque parágrafos da mesma célula deixaram de ser fundidos (M04).

**Sumário do ex. 05** (`sumario-05.json`): as 21 entradas do sumário impresso e os 179 marcadores do PDF apontam para a página onde está o título.

**Página-imagem nova** (`m01-diagrama-renderizado.png`): numa ficha sintética renderizada pelo LibreOffice, a página só com diagrama sai inteira, legível e depois do enunciado. A capa só com imagem foi removida (R1), e o filete de sangria não criou página extra.

Ponto para depois, sem efeito na integridade: o diagrama ocupa uma página inteira, mesmo pequeno. Recortar a página-imagem pelo conteúdo economizaria páginas.
