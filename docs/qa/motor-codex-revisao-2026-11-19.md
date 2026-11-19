# Motor Codex — roteiro de revisão e integração

## Isolamento

- Base do Claude: `984b046`, branch `main`, pasta `H:\Nexa\03_Comercial_Clientes\Projetos\DocRedux\Nexa_DocRedux`.
- Implementação: branch `codex/motor-integridade`, worktree `H:\Nexa\03_Comercial_Clientes\Projetos\DocRedux\Nexa_DocRedux_Codex`.
- A pesquisa/auditoria foi registrada separadamente no commit `a79f787`; as correções ficam no commit seguinte.
- Nenhum merge, push, deploy, alteração de banco ou dependência nova foi realizado.
- Os quatro arquivos não rastreados de auditoria no checkout original permaneceram como estavam. A branch contém cópias para revisão, sem movê-los.
- O baseline de páginas e cobertura não foi alterado. A versão experimental deve ser identificada pela branch/commit, antes de definir versão de release na integração.

## Relação com a pesquisa

O levantamento em `docs/research/motor-referencias-2026-11-19.md` compara oito referências oficiais. Docling inspira proveniência; pdfplumber, depuração geométrica; OmniDocBench, avaliação por componente; diff-pdf, comparação de saídas aprovadas; syntok, preservação de posições e pontuação. São referências de abordagem, não dependências adicionadas nem benchmarks que o DocRedux tenha executado. As correções usam a biblioteca PyMuPDF já instalada e a biblioteca padrão.

## O que mudou

| Auditoria | Estado nesta branch | Evidência |
|---|---|---|
| M01 | Corrigido para fichas: conteúdo vetorial/raster/linhas sem texto é preservado como página-imagem a 200 dpi. Na revisão do Claude, o filete de sangria a até 3 pt do corte passou a ser ignorado, porque reprovava os ex. 01/02 | Testes nas duas opções de preservação e nas quatro rotações. Controles: páginas brancas, fundos uniformes, desenhos fora da página e sangria. Regressão 01–05 de volta ao baseline (`validacao-libreoffice-2026-11-19/PLANO.md`) |
| M02 | Corrigido: o relatório de síntese conta pelo modelo original já extraído, seguindo DT-21 | Teste de contrato e prova de que não ocorre nova extração só para contar; triagem real de 91.037 palavras no ex. 05 |
| M03 | Corrigido: divisão de frases preserva delimitadores | Aspas retas/curvas, pontuação, parênteses e abreviações |
| M04 | Corrigido: contagem separa parágrafos e quebras sem duplicar células | Células mescladas, tabela aninhada, runs de uma mesma palavra, quebra de linha e tabulação |
| M05 | Corrigido: arquivos da execução atual são passados pelo pipeline aos verificadores | Cenário com DOCX antigo e atual na mesma pasta; nome derivado do modelo no uso direto |
| M06 | Corrigido: biblioteca admite apenas as três frações do contrato | Frações próximas porém inválidas, NaN, infinito, booleano, string e ausência de fração |
| M07 | Corrigido: páginas da folha são 1-based e validadas; DPI deve ser inteiro positivo | Zero, negativo, fora do limite, lista vazia e tipos inválidos; arquivos não são criados antes da validação |
| M08 | Corrigido: aviso informa o resultado quando o núcleo impede o alvo | Núcleo ocupa 100% do texto, com alvo de 20%; conteúdo obrigatório é preservado |
| M09 | Corrigido na story 2.7: checagem numérica (números, sinais, decimais, frações, %, ordinais e operadores entre números) bloqueia o gate quando falta algo; DT-21 inalterada | 29 testes em `engine/tests/test_numeros.py`. O ex. 05 revelou defeito real (marcador de nota grudado no ano em legenda/título: "2012-20232"), corrigido. Regressão 01–05 e preservação ok (`validacao-libreoffice-2026-11-19/PLANO.md`) |
| M10 | Proteção inicial implementada | Conversão defeituosa para PDF vazio levanta RenderFalhou; PDF com imagem informativa é permitido |
| M11 | Aberto | Política bloqueante para divergência estrutural continua a da spec atual; definir critérios antes de alterá-la |
| M12 | Aberto | Contagem global continua sem provar ordem ou associação; planejar alinhamento por blocos e proveniência |
| M13 | Corrigido no stdout do protocolo | JSON ASCII escapado pode ser lido como UTF-8 em hosts CP1252/ASCII, inclusive com erros acentuados |

## Validação

### Testes novos

Arquivo: `engine/tests/test_integridade.py`. Última execução: **48 passaram**.

Evidência: `motor-codex-green-2026-11-19.xml`.

A mesma suíte foi carregada contra o pacote original, sem editar seu código: **38 falharam e 10 passaram** (`motor-codex-red-2026-11-19.xml`). Algumas falhas se referem a APIs auxiliares novas, não a 38 defeitos independentes. A evidência da auditoria anterior também permanece disponível e não foi sobrescrita.

Os testes novos não precisam de LibreOffice: usam PDFs/DOCX sintéticos reais e injeção controlada de conversão defeituosa. Não equivalem a uma regressão completa da renderização.

### Suíte existente e exemplos externos

Execução concluída em **368,15 s**: **73 passaram, 7 falharam e 1 foi pulado**, em `motor-codex-pytest-2026-11-19.xml`. As sete falhas são os cenários que precisam de conversão real pelo LibreOffice indisponível; o teste da CLI agora recebe JSON válido e falha apenas porque não há conversão, sem o erro de encoding anterior. O teste de JSON Schema foi pulado pela ausência da dependência opcional `jsonschema`.

A chamada foi iniciada com 81 casos (37 existentes + 44 novos daquela revisão); quatro cenários de rotação foram acrescentados e verificados na execução focada de 48 casos. As últimas alterações de rotação não afetam os cinco documentos externos: todas as suas 422 páginas têm rotação zero. Mensagens com acentos e o DPI da nova página-imagem também foram validados na execução focada final.

Passaram a triagem dos cinco documentos, a contagem DT-21 (1.334/91.037) e as sínteses do modelo real de 20%, 35% e 50%. Não houve nova falha observada nos cenários executáveis sem LibreOffice. Isso não permite certificar o processamento PDF ponta a ponta.

### Verificações adicionais

- `git diff --check` passou.
- Validação sintática por `ast.parse` dos módulos e testes Python passou.
- Não foram executados lint/typecheck por npm: o motor é um pacote Python sem scripts npm. Não se apresenta resultado de ferramentas Python ausentes no ambiente.
- LibreOffice não foi encontrado e o Docker não está ativo; não há certificação nova de paginação, fontes, sumário final nem comparação visual.

## Limites que o revisor deve considerar

1. A detecção visual é conservadora: preserva páginas sem texto que tenham imagens/desenhos, sem decidir se a arte é semanticamente útil. Páginas inteiramente uniformes continuam descartadas. Transparência, clipping e conteúdo oculto complexo merecem casos adicionais.
2. O tratamento de páginas `sem_conteudo` na família relatório permanece pendente de teste específico; a mudança M01 desta rodada atua em fichas.
3. A proteção M10 detecta ausência total de conteúdo detectável. Não comprova fidelidade de imagens nem percebe toda perda parcial, troca de valores, texto fora da página ou associação incorreta. Uma imagem branca ou um rodapé sozinho não constituem validação de conteúdo suficiente para um gate completo.
4. A correção M02 reaproveita o modelo na etapa de processamento. A triagem ainda faz uma extração própria; cache/reaproveitamento entre processos precisa de invalidação por original/perfil/versão/opções antes de ser implementado.
5. Alterações de contagem em células podem corrigir cobertura e palavras_saida em documentos que antes fundiam tokens. Conferir os valores no corpus real antes de integrar; não ajustar baseline para esconder diferença.
6. O stdout JSON está protegido contra encoding legado; mensagens livres de stderr ainda seguem a configuração do host. O worker deve tratar stderr como diagnóstico e não como o protocolo JSON.
7. Os testes em `docs/qa/motor_auditoria_repro.py` são o retrato anterior da auditoria. Alguns continuam falhando intencionalmente por M09/M11/M12, e outros usam expectativas anteriores à escolha de exceção para PDF vazio. Para validar as correções desta rodada, executar a suíte normal de `engine/tests`.

## Comandos para Claude/revisor

No worktree Codex, com o ambiente Python de referência já existente:

```powershell
$py = 'H:\Nexa\03_Comercial_Clientes\Projetos\DocRedux\Nexa_DocRedux\api\.venv\Scripts\python.exe'
$env:DOCREDUX_FIXTURES = 'H:\Nexa\03_Comercial_Clientes\Projetos\DocRedux\exemplos'
Set-Location 'H:\Nexa\03_Comercial_Clientes\Projetos\DocRedux\Nexa_DocRedux_Codex\engine'
& $py -m pytest tests/test_integridade.py -q -p no:cacheprovider
& $py -m pytest -q -p no:cacheprovider
& $py tests/regressao.py --saida <diretorio-de-saida-vazio>
```

Revisão por Git (sem integrar automaticamente):

```powershell
git log --oneline main..codex/motor-integridade
git diff main...codex/motor-integridade -- engine/ docs/stories/2.6.integridade-motor-codex.story.md
```

Depois que LibreOffice/fontes estiverem disponíveis, executar a regressão original e a de preservação de elementos, mais sínteses 20/35/50 do relatório e inspeção visual. Só então decidir sobre integração e evolução dos gates M09/M11/M12.
