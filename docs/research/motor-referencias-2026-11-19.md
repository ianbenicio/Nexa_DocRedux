# Referências externas para evolução do motor

Pesquisa em 2026-11-19. Foram consultados documentação dos mantenedores, repositórios oficiais e o benchmark dos autores. A comparação abaixo é uma avaliação de adequação ao DocRedux; não é um benchmark executado desses projetos.

## Comparação

| Projeto | Evidência na fonte oficial | Aplicação proposta ao DocRedux | Decisão nesta rodada |
|---|---|---|---|
| [Docling](https://docling-project.github.io/docling/concepts/docling_document/) | Modelo estruturado com texto, tabelas, imagens, hierarquia, geometria e proveniência | Cada elemento deve apontar para página/região de origem; separar corpo e elementos repetidos | Referência arquitetural para próxima etapa; sem substituir o motor |
| [pdfplumber](https://github.com/jsvine/pdfplumber) | Inspeção de caracteres/linhas/retângulos, extração de tabelas e depuração visual | Gerar overlays dos elementos descartados e das associações tabela/figura | Ferramenta de diagnóstico candidata; sem nova dependência agora |
| [OmniDocBench](https://github.com/opendatalab/OmniDocBench) | Avaliação separada de texto, fórmulas, tabelas e ordem de leitura | Acrescentar testes de relações/valores e casos com defeitos injetados, além da cobertura global | Adotar a separação dos critérios; sem alegar resultado do DocRedux no benchmark |
| [diff-pdf](https://github.com/vslavik/diff-pdf) | Comparação visual de dois PDFs e geração de PDF com diferenças destacadas | Comparar a saída do motor com uma saída compacta previamente aprovada | Preparar uma futura regressão visual; original versus compacto terá diferenças intencionais |
| [syntok](https://github.com/fnl/syntok) | Tokens com espaços e posições; possibilidade de reconstruir a sequência original | Preservar pontuação e offsets na divisão de frases da síntese | Aplicar o princípio com correção local e testes; validar português antes de adotar biblioteca |
| [OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/cookbook.html) | Modos diferentes para conteúdo digital, OCR existente e conteúdo misto | Distinguir página sem texto de página sem conteúdo; preservar imagens informativas | Princípio aplicado à correção de páginas visuais; OCR continua fora do escopo atual |
| [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx) | Conversor PDF→DOCX; o mantenedor informa que não faz mais manutenção ativa | Estudar heurísticas de conversão, não presumir que é substituto suportado | Referência secundária, sem dependência nova |
| [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_bboxlog) | Eventos geométricos de texto, imagem e desenho; sequência de pintura e limites | Identificar conteúdo visual e inspecionar o PDF final com a biblioteca já usada | Usar APIs existentes para correções pequenas e verificáveis |

## Conclusão técnica

Não foi demonstrado, nesta pesquisa, um substituto pronto que cumpra simultaneamente as regras específicas de compactação de fichas, a preservação educacional e a síntese extrativa do DocRedux. Os projetos acima resolvem partes próximas do problema. Recomenda-se reaproveitar os conceitos de proveniência, avaliação por componente e regressão visual, mantendo o pipeline determinístico enquanto se medem alternativas em um conjunto separado.

Comparação por pixels é apropriada entre versões de uma mesma saída aprovada. Ela não mede, sozinha, equivalência entre documento original e documento compactado, pois o reposicionamento é intencional. Da mesma forma, um parser ter alta pontuação de OCR não comprova integridade da renderização DOCX→PDF.

## Aplicação e limites desta branch

Primeira rodada: corrigir M01–M08 e M13 da auditoria, e implementar uma proteção inicial para M10 (PDF sem conteúdo visível/textual após conversão). Manter a contagem DT-21 e o contrato público. Não copiar código de terceiros nem instalar parsers/modelos novos.

Próximas rodadas: integridade numérica e fórmulas (M09), critérios bloqueantes de estrutura (M11), alinhamento e ordem por bloco (M12), proveniência completa e regressão visual. A proteção contra PDF vazio não resolve cortes, trocas de imagens ou perda parcial de conteúdo.

## Isolamento para revisão

- Base preservada: `984b046`, branch `main`, checkout `Nexa_DocRedux`.
- Desenvolvimento: branch `codex/motor-integridade`, worktree `Nexa_DocRedux_Codex`.
- Os arquivos de auditoria anteriores foram copiados para a branch; os originais não foram movidos nem alterados.
- O comparativo para integração deve usar `main...codex/motor-integridade`, sem substituir a versão original antes da revisão do responsável/Claude.
