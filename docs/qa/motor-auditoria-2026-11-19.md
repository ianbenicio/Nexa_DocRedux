# Auditoria do motor DocRedux — 2026-11-19

Escopo: `engine/`. Revisão de código, execução dos testes existentes com os cinco PDFs externos e testes adicionais de integridade. Nenhum arquivo de produção foi alterado. API web, banco, upload e implantação ficam fora desta rodada.

## Resultado executivo

O principal risco está na integridade e na capacidade do verificador de detectar perdas. Há descarte demonstrável de páginas com diagramas e erros na síntese/contagem. Cobertura textual alta não comprova integridade do PDF entregue.

Foram criados 16 casos adicionais em `motor_auditoria_repro.py`: 15 falharam e 1 passou. As falhas representam 13 temas, com repetição para os dois verificadores e para as duas opções de preservação. Parte dos casos expressa melhorias propostas ao contrato de qualidade, e não regressões contra a especificação atual. Os testes usam PDFs/DOCX sintéticos; dois casos injetam resultados controlados na etapa de pipeline para isolar o gate e os metadados. O teste de PDF vazio injeta apenas uma conversão defeituosa e executa extração, geração de DOCX e verificação reais.

Evidência estruturada: `motor-auditoria-2026-11-19.xml`.

## Execução da suíte existente

Com `DOCREDUX_FIXTURES` configurado, os 37 casos existentes terminaram em **366,13 s**: **29 passaram, 7 falharam e 1 foi pulado**.

- Passaram as triagens dos cinco exemplos, inclusive os valores de contagem DT-21 (1.334 no ex. 03 e 91.037 no ex. 05).
- Passaram a síntese do modelo real do exemplo 05 nas frações 20%, 35% e 50%, com os critérios existentes de inclusão, núcleo e preservação das figuras no modelo. Isso não certifica o PDF renderizado.
- Seis casos falharam na conversão por falta do LibreOffice. Não constituem, por si só, seis defeitos no código.
- O caso CLI restante falhou com `UnicodeDecodeError` na leitura de subprocesso e `AttributeError` decorrente. A ausência de LibreOffice acionou a mensagem acentuada que revelou o problema de encoding. M13 isola a incompatibilidade sem depender do LibreOffice.
- O teste de schema JSON Schema foi pulado por falta da dependência opcional `jsonschema`.
- A triagem do ex. 05 levou **49,68 s** neste host. O teste que conta duas vezes o mesmo documento levou **99,75 s**; isso reforça a oportunidade de evitar reextração, sem permitir extrapolar capacidade de produção.

Evidência completa: `motor-pytest-2026-11-19.xml`. As 7 falhas existentes e as 15 falhas da auditoria são grupos separados, com causas e objetivos distintos.

## Ambiente e limites

- Interpretador utilizado: `api/.venv/Scripts/python.exe`, Python 3.13.13. PyMuPDF funciona neste ambiente.
- O ambiente `engine/.venv` observado anteriormente falha ao importar a DLL do PyMuPDF. Não foi alterado.
- `soffice.exe`/`soffice.com` não foram encontrados nos caminhos padrão, embora exista a pasta do LibreOffice.
- Docker não está disponível para executar a imagem de referência nesta sessão.
- `jsonschema`, coverage e ruff não estão instalados no ambiente utilizado. Não se apresenta porcentagem de cobertura de código nem validação por lint/typecheck.
- A regressão oficial foi tentada e interrompeu antes do primeiro documento por `SofficeNaoEncontrado`. Portanto, paginação, fontes e qualidade visual do PDF final não estão certificados nesta rodada.
- As contagens/paginação do baseline histórico permanecem inalteradas. Nenhum resultado antigo foi tratado como uma nova execução.

## Erros reproduzidos

### M01 — Alta: página com diagrama descartada como branca

Local: `engine/docredux_engine/ficha/extract.py:651`.

`is_cover_or_blank()` usa ausência de texto como critério suficiente para página branca. Um PDF sintético com instrução na primeira página e um diagrama vetorial na segunda perde a segunda página. O problema ocorre com `manter_capa_elementos=False` e `True`: a preservação não recupera páginas classificadas como brancas.

Testes: `test_pagina_com_diagrama_nao_e_branca[False/True]`.

Correção sugerida: só classificar como branca após examinar imagens, vetores e área efetivamente pintada; conteúdo visual sem texto deve ser preservado ou sinalizado. Registrar o motivo e a evidência da remoção. A regra equivalente de relatório (`page_removal`) também merece teste próprio: conteúdo vetorial sem texto pode ser classificado como `sem_conteudo`.

### M02 — Média: síntese retorna contagem de palavras incompatível com DT-21

Locais: `engine/docredux_engine/api.py:207` e `:360`.

A triagem usa `contar_palavras()`; o relatório do modo síntese usa `_palavras()` sobre o PDF bruto. No exemplo 05, o relatório retorna **93.166**, enquanto o contrato/teste da DT-21 estabelece **91.037**. O teste adicional usa o PDF real e substitui extração/render para isolar o campo do relatório; não mede a qualidade do resumo nem certifica o orçamento.

Teste: `test_sintese_usa_contagem_dt21_no_relatorio`.

Correção sugerida: manter campos distintos e explícitos para palavras brutas, palavras elegíveis para síntese e palavras usadas no orçamento/verificação. O campo público deve seguir a decisão vigente. Evitar nova extração completa só para recomputar um valor já disponível na triagem.

### M03 — Média: divisão de frases remove aspas de fechamento

Local: `engine/docredux_engine/sintese/extrativa.py:81`.

A expressão passada a `re.split` consome a aspa/fechamento opcional após o ponto. Exemplo: `O autor afirmou: "O ensino melhorou." A pesquisa confirmou esse resultado.` perde a aspa depois de `melhorou.`. Isso também afeta os spans usados para reconstruir o texto selecionado.

Teste: `test_divisao_de_frases_preserva_as_aspas_de_fechamento`.

Correção sugerida: dividir por posições preservando pontuação e delimitadores; testar aspas retas, curvas, apóstrofos e parênteses.

### M04 — Média: contagem de DOCX cola parágrafos da mesma célula

Local: `engine/docredux_engine/verify/ficha.py:44`.

Dois parágrafos `Primeira` e `Segunda` viram o token `primeirasegunda`. Isso produz palavras faltantes artificiais na verificação de fichas e também contamina `palavras_saida` público, pois a API usa esse contador para ambos os tipos de documento.

Testes: `test_contagem_docx_separa_paragrafos_na_mesma_celula` falha; `test_contagem_docx_preserva_runs_da_mesma_palavra` passa como controle.

Correção sugerida: preservar fronteiras entre parágrafos/células, mantendo runs de uma mesma palavra unidos. Também testar quebras de linha e tabelas aninhadas para evitar dupla contagem.

### M05 — Média: verificador escolhe DOCX de outra execução

Locais: `engine/docredux_engine/verify/ficha.py:116` e `engine/docredux_engine/verify/relatorio.py:116`.

O arquivo é selecionado por `next(out.glob("*_COMPACTO.docx"))`. Em uma pasta com DOCX antigo e atual, a prova sintética obtém **12,5%** de cobertura apesar de o DOCX atual conter todo o texto correto. A seleção depende do conteúdo/ordem do diretório. O relatório também escolhe PDF por glob.

Teste: `test_verificador_nao_seleciona_docx_de_outra_execucao`.

Correção sugerida: passar os caminhos exatos gerados nesta execução ao verificador e escrever cada execução em diretório próprio. Não depender de ordenação do glob como solução.

### M06 — Baixa: biblioteca aceita fração fora do contrato

Local: `engine/docredux_engine/api.py:312`.

`Opcoes(fracao=0.204, modo="sintese")` passa porque a validação arredonda antes de comparar com 0,20/0,35/0,50. A CLI é mais estrita. O alvo efetivamente usado pode divergir do nome/percentual arredondado da saída.

Teste: `test_fracao_fora_do_contrato_nao_e_arredondada`.

Correção sugerida: validar os valores permitidos com tolerância numérica explícita, sem admitir uma faixa inteira por arredondamento.

### M07 — Baixa: página zero aceita na folha de contato

Local: `engine/docredux_engine/folha.py:13`.

O contrato declara páginas 1-based, mas `[0]` vira índice -1 e seleciona uma página em vez de falhar. Também falta validar limites, negativos, lista vazia e DPI.

Teste: `test_folha_recusa_pagina_zero`.

Correção sugerida: validar os parâmetros antes de gerar arquivos e fornecer erro estável.

### M08 — Média: aviso de síntese anuncia alvo incompatível com o resultado

Local: `engine/docredux_engine/sintese/documento.py:262`.

Em um documento cujo texto elegível está todo em PROBLEMA, a preservação obrigatória do núcleo leva `fracao_real` a **100%**, mas o aviso inserido no documento anuncia `cerca de 20%`. Preservar o núcleo é coerente com a regra existente; o erro está na comunicação do resultado e na ausência de aviso específico de alvo inviável.

Teste: `test_nucleo_maior_que_alvo_exige_aviso_especifico`.

Correção sugerida: calcular a fração mínima viável e gerar o aviso a partir do resultado efetivo, explicando quando o núcleo obriga ultrapassar o alvo. Não cortar o núcleo silenciosamente para satisfazer o percentual.

### M13 — Média: JSON da CLI depende do encoding do host

Local: `engine/docredux_engine/cli.py`, saída final em `main()`; consumidor: `api/worker/motor.py`, `out.decode("utf-8")`.

A CLI escreve JSON com `ensure_ascii=False` na codificação de `sys.stdout`. Em um ambiente CP1252, mensagens como `campo obrigatório` são bytes incompatíveis com o leitor UTF-8 do worker. O teste usa `PYTHONIOENCODING=cp1252` para reproduzir de forma determinística uma configuração legada de Windows. Na suíte existente, a leitura UTF-8 do stderr também gerou uma exceção de thread.

Teste: `test_cli_json_compativel_com_leitor_utf8_em_ambiente_windows`.

Correção sugerida: estabelecer UTF-8 explicitamente no protocolo de subprocesso (stdout e stderr) e no ambiente do filho, ou emitir JSON ASCII escapado no stdout. Testar caminho/nome com acento, recusa e sucesso. Não apenas ignorar erros de decodificação.

## Melhorias de integridade demonstradas por testes

Os próximos itens são limitações do modelo de verificação atual. A mudança do gate deve ser refletida na especificação e nos critérios de aceitação antes de ser considerada concluída.

### M09 — Alta: números e sinais matemáticos não são protegidos pela cobertura

Locais: `engine/docredux_engine/verify/ficha.py:18` e `engine/docredux_engine/verify/relatorio.py:20`.

Os verificadores descartam dígitos isolados e a tokenização ignora diversos sinais. `A resposta correta e 2.` e `A resposta correta e 9.` produzem a mesma contagem. A prova não afirma que o extrator alterou um número nos cinco exemplos; comprova que o detector não perceberia essa alteração.

Teste: `test_alteracao_de_valor_numerico_precisa_ser_detectada`, parametrizado para os dois verificadores.

Melhoria: checagem de integridade numérica por bloco, incluindo decimais, frações, porcentagens, sinais e unidades. Remover números de página pela posição/papel do elemento, sem apagar todos os dígitos isolados do conteúdo.

### M10 — Alta: PDF vazio pode ser aprovado com DOCX correto

Locais: `engine/docredux_engine/pipeline.py` e verificadores.

O pipeline abre o PDF para contar páginas; a cobertura é calculada sobre o DOCX. No teste, uma conversão defeituosa produz um PDF válido, com uma página e nenhum texto. O DOCX continua correto, a cobertura é **100%**, e o gate retorna `ok`.

Teste: `test_gate_verifica_conteudo_do_pdf_entregue` (injeção de falha na conversão; demais etapas reais).

Melhoria: conferir o artefato efetivamente entregue, com texto extraído do PDF e verificações visuais/geometria para imagens, conteúdo cortado, sobreposição e páginas vazias inesperadas. Não confundir sucesso do processo de conversão com integridade do resultado.

### M11 — Alta: divergência estrutural não bloqueia

Local: `engine/docredux_engine/api.py:354`.

`estrutura_ok=False` com cobertura de 100% resulta em `gate=ok` e apenas um aviso. Esse comportamento segue o gate de cobertura atual da spec, mas não protege a promessa de preservar figuras, legendas e relações entre elementos.

Teste: `test_gate_nao_libera_estrutura_divergente` (pipeline controlado para isolar a decisão).

Melhoria: definir quais diferenças estruturais são bloqueantes e quais exigem revisão. Expor o resultado estrutural completo no contrato público, em vez de apenas um aviso genérico.

### M12 — Média: cobertura por contagem ignora ordem e associação

Locais: verificadores `ficha` e `relatorio`.

`O aluno ajuda o professor.` e `O professor ajuda o aluno.` têm o mesmo multiconjunto de palavras. Troca de alternativas, associação imagem–enunciado e deslocamento de números entre células também podem escapar desse tipo de medição.

Teste: `test_cobertura_nao_pode_ignorar_a_ordem_das_palavras`.

Melhoria: manter a cobertura global como diagnóstico e acrescentar alinhamento por blocos/frases, sequência e relação entre questão, alternativa, tabela e figura. O teste registra uma limitação matemática do contador; a implementação futura pode manter o contador e acrescentar uma segunda checagem.

## Lacunas adicionais e plano de testes

| Área | Casos a acrescentar/fortalecer | Critério |
|---|---|---|
| Extração visual | Página só com raster, só com vetor, conteúdo recortado, figura na margem, PDF misto | Nenhum elemento informativo removido sem evidência e registro |
| Texto e matemática | Sinais, expoentes, frações, decimais, negação, alternativas, ligaduras e Unicode | Valores e relações preservados por bloco |
| DOCX | Parágrafos em células, runs fragmentados, células mescladas, tabelas aninhadas | Contagem sem fusão nem duplicação |
| Síntese | Aspas, abreviações, texto muito curto, núcleo acima do orçamento, seção sem frases selecionáveis | Trechos íntegros; fração viável e fração real explicitadas |
| PDF entregue | Converter sem produzir arquivo, produzir vazio, cortar figura, truncar texto | Resultado defeituoso bloqueado ou encaminhado à revisão |
| Sumário | Títulos repetidos/longos, mudança de página após segunda passada, mapa parcial | Todas as entradas apontam para a página final correta |
| Perfis | Assinaturas ambíguas, perfil genérico versus específico, perfil inválido, fonte substituída | Seleção inequívoca ou recusa explicada |
| Biblioteca/CLI | Perfil explícito com PDF protegido/corrompido, entrada inexistente, saída não gravável | Exceções públicas estáveis e JSON válido na CLI |
| Reexecução | Dois documentos no mesmo diretório; duas chamadas simultâneas; falha no meio | Nenhum arquivo de outra execução validado/entregue |
| Distribuição | Instalação por wheel em ambiente limpo | Perfis semente encontrados sem depender do checkout |
| Desempenho | CPU/RAM/tempo por etapa, relatório de 300 páginas, capítulo único grande | Limites medidos e repetíveis no container de referência |

Observações da revisão estática:

- `contar_palavras()` executa uma extração completa na triagem, e o processamento extrai novamente. Medir e avaliar reaproveitar o modelo/contagem com chave por hash do original, perfil, versão e opções. Evitar cache sem invalidação.
- TextRank faz comparações quadráticas por capítulo. Testar um documento grande sem divisões de capítulo antes de extrapolar o tempo do exemplo 05 para outros relatórios.
- O pacote inclui `docredux_engine/*.json`, mas os perfis semente ficam em `engine/perfis`, fora do pacote. O checkout/editable e o Docker atual contornam isso; a distribuição por wheel precisa de teste em instalação limpa. Esse ponto não foi validado com build nesta sessão.
- Vários documentos PyMuPDF são abertos sem fechamento explícito/context manager. Testar execução longa e repetida antes de assumir que o coletor de lixo é suficiente.
- A comparação de cobertura usa percentuais arredondados. Preservar precisão no gate e arredondar só na apresentação evita aceitação muito próxima ao limiar.
- A regressão com `manter_capa_elementos` aceita qualquer quantidade de páginas maior ou igual ao baseline padrão. Isso não detecta crescimento acidental sem limite nem comprova preservação de cada elemento. Criar baseline próprio para a opção.

## Ordem sugerida para correção

1. M01, M09 e M10: prevenir descarte visual e detectar perda no artefato entregue.
2. Definir o gate estrutural (M11) e alinhar por blocos (M12), com critérios na spec.
3. M02–M05, M08 e M13: consistência da síntese, contagem, pontuação, seleção de arquivos e protocolo da CLI.
4. M06–M07: validação de entrada; depois distribuição, recursos e desempenho.
5. Executar regressão completa original + preservar elementos + síntese 20/35/50 no container com LibreOffice e fontes de referência; revisão visual amostral e testes com defeitos injetados.

## Reprodução

Na raiz `Nexa_DocRedux`, PowerShell:

```powershell
$env:DOCREDUX_FIXTURES = 'H:\Nexa\03_Comercial_Clientes\Projetos\DocRedux\exemplos'
.\api\.venv\Scripts\python.exe -m pytest docs/qa/motor_auditoria_repro.py -v -p no:cacheprovider
Push-Location engine
..\api\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
..\api\.venv\Scripts\python.exe tests/regressao.py --saida <diretorio-de-saida-vazio>
Pop-Location
```

A auditoria permanece fora do `testpaths=tests` do motor. Não se alterou o comportamento de produção nem se transformaram as falhas em `xfail`. Estes casos devem ser migrados para a suíte normal junto com as correções e com as decisões sobre o contrato de qualidade.
