# Spec: plataforma web de testes do piloto do DocRedux

> **Versão:** 1.6 · **Data:** 2026-11-19 · **Story:** DOCREDUX-PILOTO · **Complexidade:** STANDARD (15)
> **Autores:** @pm (requisitos e spec) · @architect (complexidade e plano) · @qa (critique)
> **Entradas:** `requirements.json`, `complexity.json`, `critique.json` (rodada 1: 3,8, NEEDS_REVISION; rodada 2 sobre a v1.1: **4,4, APPROVED**; a v1.2 fecha as condições C-21 a C-25). A v1.3 aplica as respostas do responsável às perguntas Q1–Q18 (§18), inclusive a infraestrutura sem custo (DT-17 a DT-19). A v1.4 registra as decisões da unidade 3 (DT-21 e o modelo sem FK para `auth.users`, §6). A v1.5 aplica as decisões do responsável de 2026-11-19 sobre domínio e e-mail (Q2, Q8, Q12, Q14 → DT-22). A v1.6 registra a consequência técnica da DT-21 no timeout da triagem (unidade 4). Ver "Histórico". Sem `research.json`: nenhuma lacuna técnica bloqueou a spec (ver `complexity.json`).
> **Fontes rastreadas:** `saas/piloto.md` (PL1–PL5, §2–§5), `saas/processos.md` (P1–P16, D1–D17), `saas/margem.md` §10, `saas/revisao-template-novo.md` §6, `docs/plano-de-execucao.md`, `LOG.md`.
> **Fonte única:** esta cópia, no repositório `Nexa_DocRedux/docs/spec/`. A cópia antiga em `DocRedux/docs/spec/` (fora do git) só aponta para cá.
> **Regra (Artigo IV):** cada requisito aponta para a fonte. O que é escolha técnica (como fazer) aparece como **decisão técnica (DT)**, com o motivo, e não como feature nova. O que falta nas fontes está em **§18 Perguntas abertas**, com um padrão proposto.

---

## 1. Objetivo e escopo

Construir a plataforma em que **10 participantes, durante 4 semanas** (PL1), enviam PDFs, recebem a versão reduzida e avaliam cada entrega; e em que o responsável revisa os casos marcados (PL2). Créditos funcionam como no produto, mas são grátis (PL3). **100% das entregas** passam pelo juiz Opus, pela nota do usuário e, nos casos marcados, pela revisão humana (PL5, D17). Volume esperado: ~200–300 entregas (PL1).

**Dentro do escopo:** os processos da coluna "Entra no piloto" de `piloto.md` §3: P1, P2, P3, P4 (fila manual), P5, P6, P7, P8 (só registro), P9 (sem Pix), P10, P11, P12, e de P13 só os dois avisos por e-mail da Q8 (DT-22). P16 fica manual, fora da plataforma (CON-9).

**Fora do escopo** (piloto §3, coluna "Fica de fora"; CON-4): login Google e B2B; corretor (U-A); presets de saída; estimativa de páginas finais; C1–C4 automáticos; orquestrador multi-LLM (P15); QA visual separado; prévia lado a lado; correção automática e cobrança de formato; Pix e pacotes; regressão em CI; política completa de LGPD; painel financeiro; biblioteca automática (P16); demais notificações por e-mail (P13; a Q8 só trouxe "documento pronto" e "em revisão", DT-22); atendimento de template novo dentro do piloto (Q3: é recusado na triagem e vira pedido de perfil, ver FR-10).

## 2. Arquitetura

```
                 ┌──────────────────── Vercel Hobby (*.vercel.app, funções em gru1) ────────────────────┐
 Navegador ────► │ web/ (Next.js)  telas do usuário + /admin    │  api/ FastAPI (Python Functions)       │
   │             └───────┬────────────────────────────────────────────────┬─────────────────────────────┘
   │ upload TUS em partes (JWT)                                           │ pooler, modo transação (6543)
   ▼                                                                      ▼
┌──────────── Supabase Free (sa-east-1) ───────────┐   saída apenas   ┌──── Worker (Docker) ──────────────────────┐
│ Auth (e-mail + senha, cadastro fechado, sem SMTP)│◄────────────────│ PC do responsável (dev, dry-run) ou        │
│ Storage: originais/ (partes ≤ 42 MB)  saidas/    │  pooler, modo    │ Oracle Always Free São Paulo (piloto, 24 h) │
│ Postgres: tabelas + RLS + fila (jobs)            │  sessão (5432)   │ worker/ (N processos) ── engine/            │
└──────────────────────────────────────────────────┘                  │   └─ LibreOffice headless + Carlito         │
                                                                      │   └─ juiz ───────► API Anthropic (Opus)     │
                                                                      └─────────────────────────────────────────────┘
```

- **Front e API** na Vercel Hobby, **worker** num host Docker no Brasil (PL4: o worker precisa de LibreOffice; CON-2), **Supabase** gerenciado no plano gratuito (CON-1). Infraestrutura sem custo fixo na fase de testes (DT-17, Q2/Q18).
- O worker só faz conexões de saída (Postgres, Storage, Anthropic): não precisa de domínio, TLS nem porta aberta. Por isso pode rodar num PC comum.
- O navegador fala com a **API** para todos os dados. Fala direto com o Supabase só para **login** e para o **upload** do original (DT-3).
- A API **nunca abre PDF**: triagem, processamento e juiz rodam no worker (DT-2).
- Tudo que escreve saldo, ledger, status ou métricas usa o **papel de backend** (service role). O papel `authenticated` só lê o que é dele (NFR-3, NFR-4).

### Decisões técnicas

| # | Decisão | Motivo | Fonte |
|---|---|---|---|
| DT-1 | Fila no Postgres (`FOR UPDATE SKIP LOCKED`) + worker separado; sem Redis | Jobs de 15–60 s+ com LibreOffice precisam sobreviver a reinício; o volume (~300 entregas) não justifica um broker | plano §2 (`adr-001`, pré-condição não vale), NFR-1 |
| DT-2 | Triagem também é job do worker (`tipo = triagem`, prioridade máxima) | PDF não confiável de até 80 MB não deve ser aberto no processo da API; o motor fica num lugar só | NFR-1, NFR-8 |
| DT-3 | Upload direto do navegador ao Storage por TUS (retomável), com o JWT do usuário e política de INSERT só na pasta dele; arquivos acima de 42 MB vão em partes (DT-18) | 80 MB pela API dobraria o tráfego (e a função da Vercel tem limite de corpo); TUS retoma depois de queda de rede | FR-4, NFR-2 (FU-001) |
| DT-4 | Status por polling de 3 s enquanto o documento está em andamento | Mais simples que Realtime e suficiente para 10 usuários; "tempo real" do ponto de vista do usuário | FR-11 |
| DT-5 | O motor roda em subprocesso, com timeout. O perfil do LibreOffice é exclusivo da conversão em andamento e reaproveitado entre conversões seguidas; perfil de conversão que falhou é descartado (story 2.8) | Timeout duro, isolamento de falhas do LibreOffice e de PDF malicioso (o LibreOffice só abre o DOCX gerado pelo motor, nunca o PDF enviado). Perfil novo por chamada custava ~6 s por conversão | NFR-1 |
| DT-6 | O juiz roda **depois** da saída gerada (em `pronto` e em `em_revisao`), sem bloquear a entrega | O gate bloqueante do piloto é a cobertura (P6); o juiz alimenta a fila do backoffice | PL2, PL5, ASM-4 |
| DT-7 | Saldo em coluna materializada, atualizada na mesma transação do ledger por funções `SECURITY DEFINER` num schema não exposto, só executáveis pelo backend | Leitura rápida do saldo e consistência sob concorrência | NFR-3 (`despensa-auditoria`) |
| DT-8 | O preço é uma função pura em `api/` (`creditos.orcar`), testada contra a margem §10 | O preço muda depois do piloto (D17); o motor fica sem regra de negócio | FR-7 |
| DT-9 | Parâmetros do piloto em tabela `config` (limites, créditos iniciais, limiares, juiz, amostra, teto de Storage) | Ajustar sem deploy durante as 4 semanas; D17 prevê reduzir a fração avaliada depois | D17, Q6, Q11 |
| DT-10 | Síntese arredonda **para cima** (`teto`), mínimo 1 | É a regra da margem §10.3 (ficha de ~2 mil palavras × 0,6 = 1,2 → 2). O ex. 05 leve dá 92 com as 91.037 palavras do verify, que valem para o preço (DT-21); a margem mostra 91 porque arredondou o documento para "91 mil". Confirmado em Q9; `processos.md` P9 corrigido | margem §10.3, Q9 |
| DT-11 | Imagem Linux com **Carlito** (métrica idêntica à Calibri) + Liberation; a regressão do NFR-6 roda **dentro do container** | O render usa Calibri (`scripts/render_docx.py:17`), que não existe no Linux; sem fonte de mesma métrica o LibreOffice muda as quebras de página | NFR-6, Q13 |
| DT-12 | O visualizador baixa o PDF inteiro uma vez (URL assinada de 60 s → `blob`) e renderiza/imprime a partir do blob | O pdf.js busca por faixas sob demanda; a URL expirada quebraria a rolagem e a impressão | FR-14 |
| DT-13 | Cancelamento pelo usuário em `na_fila` (FR-21) | Derivado: o usuário que desiste antes do processamento sai sem perder créditos; o estorno segue P9. Com Q3, não há mais espera por template novo | P3, P9 |
| DT-14 | O **worker** conecta pelo Supavisor em **modo sessão** (IPv4, porta 5432); a **API** (função serverless) pelo Supavisor em **modo transação** (porta 6543), sem prepared statements, 1 conexão por instância | A conexão direta do Supabase só tem IPv6 sem add-on (pago); o worker mantém prepared statements e `pg_try_advisory_lock`; a função serverless abre e fecha conexões e não pode esgotar o pooler | NFR-1, unidade 7 |
| DT-15 | Tabela `workers` com `visto_em`, atualizada a cada volta do laço | O heartbeat de job não diz se um worker ocioso está vivo; o `/health` usa esta tabela | unidade 7 (health) |
| DT-16 | Saída versionada: `entregas` tem uma linha por versão; `documentos.entrega_atual` aponta a vigente | Reprocessar não apaga a entrega que o usuário avaliou nem as métricas da primeira passagem | FR-17, FR-20 |
| DT-17 | **Infraestrutura sem custo fixo** na fase de testes: Supabase Free em `sa-east-1`; web e API na Vercel Hobby (subdomínio `*.vercel.app`, funções Python na região `gru1`); worker em Docker no PC do responsável (desenvolvimento e dry-run) e, para o piloto 24 h, Oracle Cloud Always Free (Ampere A1, região São Paulo, até 2 OCPU/12 GB). Se não houver capacidade na Oracle, o piloto roda com o worker no PC e a tela avisa que o processamento acontece em horário comercial. Domínio `.com.br` (Registro.br) só como remetente de e-mail (Resend, DT-22); o site continua em `*.vercel.app`. Sem Hetzner (Q14) | Q2, Q10, Q14 e Q18: sem custo agora e sem servidor fora do Brasil. A fila no Postgres (DT-1) deixa o worker só com conexões de saída. A Vercel Hobby é para uso não comercial: serve ao piloto fechado e gratuito e precisa ser revista no lançamento. O único custo variável que resta é o juiz (API Anthropic, ≈ R$ 0,60 por entrega, D17) | Q2, Q10, Q14, Q18, PL4 |
| DT-18 | **Original em partes** de até 42 MB (7 chunks TUS de 6 MB): `original.part00`, `original.part01`…; o worker baixa e concatena, e confere o tamanho total. Arquivos ≤ 42 MB têm uma parte só. Limite do piloto continua 80 MB (no máximo 2 partes) | O Supabase Free limita cada objeto a 50 MB, e o ex. 05 tem 62 MB | Q18, ASM-5 |
| DT-19 | **Login por e-mail + senha**, sem SMTP: o admin cria a conta no painel do Supabase (confirmada, com senha provisória) e entrega a senha por canal privado. No primeiro acesso, o usuário troca a senha (`supabase.auth.updateUser`). Não há "esqueci a senha": o admin redefine no painel. O domínio e o Resend da Q8 servem só às notificações (DT-22); o link mágico fica opcional para depois (Q12) | Sem domínio não há remetente verificável; o SMTP padrão do Supabase só entrega à equipe do projeto (Q12). Senha não precisa de e-mail | Q12, PL1, ASM-6 |
| DT-20 | **Teto de Storage**: a API recusa documentos novos (`503 capacidade_piloto`) quando o uso estimado (originais + saídas de documentos não apagados) passa de `config.storage_teto_mb` (padrão 900 MB); a A5 mostra o uso | O Supabase Free tem 1 GB de Storage; a retenção de 30 dias libera espaço, mas um pico de relatórios grandes enche o bucket | Q18 |
| DT-21 | **Palavras do preço da síntese = contagem do verify** (`verify`: soma dos tokens normalizados do original, `palavras_original` do relatório), gravada em `documentos.palavras` já na triagem: a triagem passa a contar com a mesma função do verify, ou a chamá-la direto (mudança no motor, unidade 4). A contagem atual da triagem (`get_text("words")`) deixa de valer para o preço | Decisão do responsável em 2026-11-19 (registrada no plano de execução, unidade 2). O preço precisa existir antes da confirmação, quando o verify ainda não rodou; por isso a triagem usa a mesma contagem. No ex. 05: verify 91.037 → 55 / 73 / 92; a contagem antiga da triagem dava 93.166 → 56 / 75 / 94 | margem §10.3, DT-10 |
| DT-22 | **Avisos por e-mail via Resend** (plano grátis: 100/dia, 3.000/mês), remetente no domínio `.com.br` da Q2. Dois eventos: `pronto` (primeira entrega `pronto` do documento, inclusive depois de `em_revisao`) e `em_revisao` (gate bloqueado, ou recusa `template_novo` que vira pedido de perfil). Prazo X: "até 2 meses" no template novo (Q3); no gate, o texto de `config.notificacao_prazo_revisao`, nulo por padrão (Q19). O e-mail leva só um texto fixo e o link `{WEB_URL}/documentos/{id}`: nem nome nem conteúdo do documento. Envio idempotente por (`documento_id`, `evento`): tabela `notificacoes` + cabeçalho `Idempotency-Key` do Resend. O worker envia no fim do job; falha no envio é registrada e repetida pelo laço (até 3 tentativas), sem derrubar o job. Conta excluída (e-mail nulo) não recebe. `RESEND_API_KEY` só em variável de ambiente; sem a chave, o envio fica desligado e o registro marca `desligado` | Q8 = sim (responsável, 2026-11-19); o login continua pela DT-19 (Q12); o Resend entra no termo, item 7 (Q14) | Q2, Q8, Q12, Q14 |

## 3. Monorepo

```
docredux/                      (local do repositório: pergunta Q1)
├── engine/                    pacote Python `docredux_engine` (unidade 2)
│   ├── docredux_engine/       api.py (contrato §4), cli.py, ficha/, relatorio/, sintese/, verify/, folha.py
│   ├── perfis/                perfis semente (mathema-saeb.json, mec-sase-relatorio.json)
│   └── tests/                 unidade + regressão dos ex. 01–05 (fixtures fora do git, ver Q1)
├── api/                       FastAPI + worker (unidades 4 e 6)
│   ├── app/                   routers/, auth.py, creditos.py (orcar), db.py, storage.py, schemas.py
│   ├── worker/                loop.py (fila), jobs/{triagem,processar,juiz}.py, limpeza.py, falhas.py, juiz/ (prompt, schema)
│   ├── tests/
│   └── Dockerfile             Python + LibreOffice + fonts-crosextra-carlito + fonts-liberation; uma imagem, dois comandos (api, worker)
├── web/                       Next.js (App Router) + supabase-js + tus-js-client + pdf.js (unidades 5 e 6)
│   └── app/{entrar,termo,documentos,enviar,creditos,admin}/
├── db/                        supabase/migrations/*.sql, seed.sql, tests/ (pgTAP: RLS e ledger) (unidade 3)
├── deploy/                    Dockerfile e docker-compose.yml do worker, vercel.json (web + API), .env.example (unidade 7; segredos fora do git)
└── docs/                      spec/ (este pacote), plano-de-execucao.md
```

Os scripts atuais (`scripts/*.py`) migram para `engine/` sem mudar o comportamento; `LOG.md`, `REGRAS.md`, `saas/` e `perfis/` continuam como documentação do método. A migração é da unidade 2.

### Dependências (versão mínima; a versão exata é fixada no lockfile da unidade que a introduz)

| Componente | Versão | Observação |
|---|---|---|
| Python | ≥ 3.12 | baseline atual dos scripts: 3.15.1 (Windows) |
| PyMuPDF | 1.28.x | baseline: 1.28.2; triagem, extração, verify e folha dependem dela |
| python-docx | 1.2.x | baseline: 1.2.0 |
| LibreOffice | mesma linha do baseline do Windows (registrar o número na unidade 2) | headless no container; fontes Carlito + Liberation (DT-11) |
| FastAPI | ≥ 0.115 | com `psycopg` 3.2 (pool) |
| Postgres | o do projeto Supabase (15+) | pgTAP nos testes |
| Next.js | 15.x (React 19) | App Router |
| `@supabase/supabase-js` | 2.x | só auth no front |
| `tus-js-client` | 4.x | upload retomável |
| `pdfjs-dist` | 4.x | visualizador (DT-12) |
| SDK Anthropic (Python) | atual na unidade 6 | confirmar com a skill `claude-api` |

## 4. Contrato do motor (`engine/`)

Biblioteca Python sem rede e sem LLM (D16, CON-6). **Reprodutível:** a mesma entrada com as mesmas opções gera as mesmas páginas, a mesma cobertura e o mesmo relatório (o PDF não é idêntico byte a byte: o LibreOffice grava a data de criação).

```python
# docredux_engine/api.py — contrato (assinaturas; a implementação é da unidade 2)

@dataclass(frozen=True)
class Opcoes:
    modo: Literal["original", "sintese"] = "original"     # U-B
    fracao: Literal[0.20, 0.35, 0.50] | None = None       # obrigatória se modo == "sintese"
    manter_capa_elementos: bool = False                    # U-C: desliga as regras de remoção correspondentes
    docx: bool = False                                     # U-E: o PDF é sempre gerado (D9)

@dataclass(frozen=True)
class Triagem:
    paginas: int
    palavras: int
    tamanho_bytes: int
    tipo: Literal["ficha", "relatorio", "desconhecido"]
    perfil_id: str | None            # perfil casado pela assinatura (fontes); None = template novo
    recusa: Literal["escaneado", "protegido", "corrompido", "excede_paginas", "excede_tamanho"] | None
    sintese_disponivel: bool         # True só para "relatorio" no piloto (ASM-1, Q4)

@dataclass(frozen=True)
class Modificacao:
    tipo: Literal["removido", "reorganizado", "sintetizado"]
    descricao: str                   # ex.: "capa removida", "21 fotos decorativas removidas"
    paginas_original: list[int]
    pagina_saida: int | None

@dataclass(frozen=True)
class Relatorio:
    versao_motor: str
    perfil_id: str
    paginas_antes: int
    paginas_depois: int
    palavras_original: int
    palavras_saida: int
    cobertura: float | None          # modo original (verify / verify_relatorio)
    fracao_real: float | None        # modo síntese
    condensado: list[str]            # síntese: o que saiu (notas, referências, exemplos), com contagem
    gate: Literal["ok", "bloqueado"]
    motivo_gate: str | None          # ex.: "cobertura 99,41% < 99,5%"; "números do original ausentes na saída: 2, operador +"
    estrutura: dict[str, int]        # contagens: capítulos, legendas, tabelas, imagens, questões, notas...
    modificacoes: list[Modificacao]  # base do relatório de modificações (U-G)
    regras_disparadas: list[str]     # R1..R48
    tempos_s: dict[str, float]       # extracao, sintese, render_docx, pdf, verify
    avisos: list[str]                # ex.: estilo fora do perfil

@dataclass(frozen=True)
class Resultado:
    pdf: Path
    docx: Path | None
    relatorio: Relatorio

def triar(pdf: Path, perfis: list[dict], *, max_paginas: int = 300,
          max_bytes: int = 80 * 2**20) -> Triagem: ...
def processar(pdf: Path, opcoes: Opcoes, perfil: dict, saida_dir: Path,
              *, ao_progresso: Callable[[str], None] | None = None) -> Resultado: ...
def folha_contato(pdf: Path, saida_dir: Path, *, dpi: int = 60,
                  paginas: list[int] | None = None) -> list[Path]: ...
def validar_perfil(perfil: dict) -> list[str]: ...   # erros de schema; [] = válido

class MotorErro(Exception): ...
class PdfRecusado(MotorErro): motivo: str
class PerfilInvalido(MotorErro): ...
class RenderFalhou(MotorErro): ...       # LibreOffice
```

**Regras do contrato:**
- `processar` escolhe o pipeline pelo `perfil["tipo"]`: `ficha` → `extract` + `render_docx` + `verify`; `relatorio` → `extract_relatorio` + `render_relatorio` + `verify_relatorio`. O PDF sai do LibreOffice headless.
- **Gate de cobertura** (FR-12): no modo original, `cobertura < 0,995` → `gate = "bloqueado"`. Na síntese não há gate de cobertura; vale o relatório de síntese (FR-13).
- **Integridade numérica** (FR-12, story 2.7, achado M09): no modo original, todo número do original (com sinal unário, decimal, fração, porcentagem ou ordinal) e todo operador entre números (`+ − - × ÷ : = < > ≤ ≥`; o hífen ASCII entre dígitos conta como menos, story 2.9) precisam chegar à saída na mesma quantidade. Faltou algum → `gate = "bloqueado"`, mesmo com cobertura ≥ 99,5%; o `motivo_gate` lista até 5 itens. A checagem usa os mesmos textos e exclusões da cobertura; número de página (só dígitos, faixa de 8% do topo/rodapé) fica fora. Números a mais na saída (sumário regenerado, rótulos) não bloqueiam. A contagem de palavras usada no preço (DT-21) não muda. Detalhe por execução em `verificacao.json` → `numeros`.
- **Síntese** (FR-6): o protótipo `sintese_extrativa.py` hoje roda por capítulo e não está ligado ao render (P5, "Próximo"). A unidade 2 generaliza para o documento inteiro e liga ao render DOCX/PDF. Toda frase da saída existe no original e aponta para a página (P5).
- **Manter capa e elementos** (FR-6, U-C): liga/desliga as regras de remoção de capa, folha de rosto, cabeçalhos e elementos gráficos (P5).
- O caminho do LibreOffice vem de `DOCREDUX_SOFFICE` (hoje fixo no Windows em `scripts/render_relatorio.py:23`). Perfil do LibreOffice (`-env:UserInstallation`, DT-5, story 2.8): cada conversão reserva um perfil de um pool com trava de arquivo exclusiva. Duas conversões simultâneas nunca dividem perfil, e o perfil é reaproveitado entre conversões seguidas (criar um novo custa ~6 s). O pool fica em `DOCREDUX_LO_POOL`; o worker usa `<cache>/_libreoffice`, que a limpeza diária não apaga. Sem a variável, o pool é um temporário do processo. O perfil fica marcado durante a conversão; se a marca sobrar (falha, código de saída diferente de 0, timeout, processo morto), a próxima reserva descarta o perfil e cria outro num caminho novo. Pool cheio, ou falha no perfil reaproveitado → perfil temporário só daquela chamada. Só conta como conversão a que sai com código 0 e deixa o PDF; arquivo de conversão com código de erro é apagado. O PDF entregue precisa conter ≥ 99,5% das palavras do DOCX, senão a entrega falha (story 2.9).
- `cli.py` expõe `python -m docredux_engine triar|processar|folha ...` com saída JSON. É assim que o worker chama o motor em subprocesso (DT-5).
- **Regressão** (NFR-6, DT-11): roda **na imagem Linux de produção**. Os ex. 01–05 reproduzem as páginas (52→19, 34→14, 16→5, 26→12, 294→142) e cobertura ≥ à registrada (99,96%, 100%, 100%, 100%, 99,97%). Se, mesmo com Carlito, o Linux mudar alguma contagem de páginas, o responsável aprova o novo baseline (Q13) e ele é registrado no `LOG.md`; a cobertura nunca pode cair.

## 5. Créditos (P9, margem §10)

Função pura `api/app/creditos.py::orcar(paginas, palavras, opcoes, template_novo) -> Orcamento` (DT-8). Saída: itens (`rotulo`, `creditos`) e total. Todos os valores são inteiros.

| Item | Regra (p = páginas do original) | Fonte |
|---|---|---|
| Serviço padrão | p ≤ 5 → 2; p ≤ 15 → 4; p ≤ 20 → 6; p > 20 → `6 + 2 × teto((p − 20) / 20)` | margem §10.1 |
| DOCX | p ≤ 15 → +1; p > 15 → +3 | margem §10.2 |
| Manter capa e elementos | p ≤ 15 → +1; p > 15 → +2 | margem §10.2 |
| Síntese | `max(1, teto(taxa × palavras / 1000))`; taxa 0,6 (20%), 0,8 (35%), 1,0 (50%); cálculo em inteiros (`teto(taxa×10 × palavras / 10000)`) | margem §10.3, DT-10 |
| Template novo | p ≤ 15 → +8; 16–60 → +12; p > 60 → +16 | margem §10.2 |

**Casos de teste obrigatórios** (unidade 4):

| Caso | Cálculo | Total |
|---|---|---|
| 5 / 6 / 15 / 16 / 20 / 21 / 40 / 41 / 60 / 61 págs. | serviço padrão | 2 / 4 / 4 / 6 / 6 / 8 / 8 / 10 / 10 / 12 |
| 10 págs. (ficha) | 4 | 4 |
| 34 págs. (ex. 02) | 6 + 2 × teto(14/20) | 8 |
| 120 págs. | 6 + 2 × 5 | 16 |
| 294 págs. (ex. 05) | 6 + 2 × teto(274/20) | 34 |
| 15 págs. + DOCX + capa | 4 + 1 + 1 | 6 |
| 16 págs. + DOCX + capa | 6 + 3 + 2 | 11 |
| 15 / 16 / 60 / 61 págs. com template novo | 4+8 / 6+12 / 10+12 / 12+16 | 12 / 18 / 22 / 28 |
| relatório com 2.000 palavras + síntese 20/35/50% | teto(1,2) / teto(1,6) / 2 | +2 em todos |
| relatório com 400 palavras + síntese 20% | max(1, teto(0,24)) | +1 |
| ex. 05 (91.037 palavras, contagem do verify, DT-21) + síntese 20/35/50% | 55 / 73 / 92 (margem: 55 / 73 / 91, ver DT-10 e Q9) | 89 / 107 / 126 |

A tabela "Exemplos com os documentos reais" em `processos.md` P9 ainda mostra valores da v2 (ex.: ex. 05 = 20); vale a margem §10 (Q9).

**Ciclo do crédito** (FR-9):
1. **Concessão**: 150 créditos ao criar a conta (FR-3, piloto §2).
2. **Reserva**: na confirmação, `−total` (o saldo disponível cai na hora). Saldo insuficiente → 402.
3. **Captura**: na primeira entrega `pronto`, um lançamento de valor 0 marca a reserva como consumida (o extrato mostra "debitado").
4. **Estorno**: `+total` da reserva quando o documento termina sem entrega: falha final, cancelamento, exclusão ou expiração antes da captura.
5. **Recarga**: o admin credita, com motivo (piloto §2, P10).

**Invariante:** toda reserva termina em exatamente uma captura ou um estorno; documento em estado final não tem reserva aberta (teste no §16).

**Template novo** (Q3): no piloto, template não reconhecido é recusado na triagem (`motivo_recusa = template_novo`), sem orçamento e sem reserva. O pedido entra na fila do backoffice. Se o admin criar o perfil antes do documento expirar, o documento volta à triagem e chega a `aguardando_confirmacao` com a sobretaxa da tabela (`template_novo = true`); o usuário confirma ou não, como num envio normal. Prazo avisado na recusa: "o template pode passar a ser aceito em até 2 meses; você pode apagar este envio quando quiser".

**Reprocessar** (backoffice, FR-17; nunca cobra de novo):

| Estado de origem | Situação do crédito | Sucesso | Gate bloqueado | Falha final |
|---|---|---|---|---|
| `em_revisao` | reserva aberta | nova versão → `pronto` + captura | continua `em_revisao` | `falhou` + estorno |
| `pronto` | já capturado | nova versão vira a atual; sem captura nova | mantém a versão anterior; registra no histórico de jobs | mantém a versão anterior; sem estorno |
| `falhou` | já estornado | nova reserva do mesmo orçamento, feita pelo admin na hora de reprocessar (o usuário paga uma vez no total; motivo "reprocessamento de documento que falhou", `criado_por` = admin); saldo insuficiente → o admin recarrega antes | `em_revisao`, nova reserva aberta | estorno da nova reserva |

**Modelo de estado do reprocessamento** (único):
- De `pronto`: `documentos.status` **não muda**. Fica `pronto` com `reprocessando = true`; só o job anda. O usuário continua vendo, baixando e avaliando a versão atual e não pode cancelar (não há reserva aberta).
- De `em_revisao` e `falhou`: o documento passa por `na_fila → processando`, como um envio normal.
- O job guarda `estado_origem` (`em_revisao`\|`pronto`\|`falhou`), que o tratador de falha final usa (§7).
- **Regra geral do estorno:** cancelar, excluir e expirar decidem o estorno pelo **estado da reserva no ledger** (aberta ou não), nunca pelo status do documento.

## 6. Modelo de dados (`db/`)

Postgres do Supabase. Todas as tabelas com **RLS ligada**. O papel `authenticated` só tem `SELECT` nas linhas dele; nenhuma tabela tem política de `INSERT/UPDATE/DELETE` para `authenticated` ou `anon`. Toda escrita passa pela API ou pelo worker com o papel de backend (NFR-3).

**Sem FK para `auth.users`** (unidade 3): `perfis_usuario.id` recebe o id do Auth no gatilho de criação do usuário, mas não referencia `auth.users`; as demais tabelas referenciam `perfis_usuario(id)`. Uma FK sem `CASCADE` impediria remover o usuário no Auth (§14 item 4), e com `CASCADE` apagaria o ledger. Assim, remover o usuário no Auth deixa o `id` como pseudônimo, e o histórico fica.

| Tabela | Colunas principais | Leitura pelo usuário (RLS) |
|---|---|---|
| `perfis_usuario` | `id` (= `auth.users.id`), `email`, `papel` (`usuario`\|`admin`), `interno bool` (dry-run e testes, Q15), `saldo int ≥ 0`, `termo_versao`, `termo_aceito_em`, `senha_provisoria bool` (padrão `true`, DT-19), `exclusao_conta_em null` (Q7), `criado_em` | própria linha |
| `perfis_template` | `id text`, `dono uuid null`, `tipo`, `json jsonb`, `versao int`, `ativo bool`, `criado_em` | `dono is null` (semente) ou `dono = auth.uid()` (D2, ASM-3, Q5) |
| `documentos` | `id`, `usuario_id`, `idempotency_key` (único por usuário), `nome_arquivo`, `tamanho_bytes`, `status`, `motivo_recusa`, `paginas`, `palavras`, `tipo`, `perfil_id`, `template_novo`, `opcoes jsonb`, `orcamento jsonb`, `creditos_reservados`, `partes int` (DT-18), `entrega_atual int null`, `reprocessando bool`, `exclusao_pendente bool`, `criado_em`, `confirmado_em`, `entregue_em`, `expira_em` (= `criado_em + 30 dias`) | próprias |
| `jobs` | `id bigserial`, `documento_id`, `tipo` (`triagem`\|`processar`\|`juiz`), `status` (`na_fila`\|`processando`\|`concluido`\|`falhou`\|`cancelado`), `prioridade`, `tentativas`, `max_tentativas` (3), `disponivel_em`, `iniciado_em`, `heartbeat_em`, `worker_id`, `erro`, `origem` (`usuario`\|`reprocessar`), `estado_origem null` (`em_revisao`\|`pronto`\|`falhou`, só em reprocessar), `criado_em`, `concluido_em` | nenhuma |
| `workers` | `worker_id`, `host`, `iniciado_em`, `visto_em` (DT-15) | nenhuma |
| `creditos_ledger` | `id`, `usuario_id`, `tipo` (`concessao`\|`recarga`\|`reserva`\|`captura`\|`estorno`\|`ajuste`; `ajuste` só na exclusão da conta, §14), `valor int`, `saldo_apos`, `documento_id null`, `reserva_id null` (captura/estorno apontam a reserva), `motivo`, `criado_por null`, `criado_em`; único (`reserva_id`) entre capturas e estornos | próprias |
| `entregas` | pk (`documento_id`, `versao`), `job_id`, `caminho_pdf`, `caminho_docx null`, `relatorio jsonb` (§4), `cobertura`, `paginas_depois`, `gate`, `criado_em` (DT-16) | só a `entrega_atual` de documento `pronto` (via `documentos`) |
| `avaliacoes_juiz` | `id`, `documento_id`, `versao`, `modelo`, `veredito` (`aprovado`\|`reprovado`\|`duvidoso`), `nota_juiz 1–5`, `criterios jsonb`, `resumo`, `entrada` (`folha`\|`folha+trechos`), `tokens_entrada`, `tokens_saida`, `custo_usd`, `duracao_s`, `criado_em` | nenhuma (só admin, via API) |
| `feedback_usuario` | pk (`documento_id`, `versao`), `nota 1–5`, `comentario`, `criado_em`, `atualizado_em` (a nota de uma versão nova não sobrescreve a da primeira entrega) | próprio |
| `problemas_marcados` | `id`, `documento_id`, `versao`, `pagina`, `tipo` (`erro`\|`formato`), `descricao`, `status` (`aberto`\|`resolvido`), `criado_em` | próprios |
| `revisoes_humanas` | `id`, `documento_id`, `versao null`, `revisor_id`, `motivo`, `veredito` (`aprovado`\|`reprovado`), `concorda_juiz bool null` (nulo quando não há avaliação do juiz para comparar), `minutos int`, `acao` (`nenhuma`\|`reprocessado`\|`cancelado_estornado`\|`perfil_anexado`), `notas`, `criado_em` | nenhuma |
| `metricas_entrega` | ver §12 | nenhuma |
| `config` | `chave`, `valor jsonb`, `atualizado_em` (DT-9) | nenhuma |
| `notificacoes` | `id`, `documento_id`, `usuario_id`, `evento` (`pronto`\|`em_revisao`), `status` (`pendente`\|`enviado`\|`falhou`\|`desligado`\|`sem_destinatario`), `tentativas`, `erro`, `provedor_id`, `criado_em`, `enviado_em`; único (`documento_id`, `evento`) (DT-22). Não guarda o e-mail nem o texto | nenhuma |

**Papel de backend:** a API e o worker conectam ao Postgres (DT-14) com um papel próprio, `docredux_backend` (login, `BYPASSRLS`, sem superusuário), com os grants de tabela de que precisam. É o "service role" desta spec no caminho SQL; a chave `service_role` do Supabase fica para a API do Storage e do Auth. Pool: API ≤ 5 conexões; cada processo do worker, 1 (cabe no limite do pooler do plano, conferido na unidade 7).

**Funções do saldo** (DT-7):
- Ficam no schema `private` (não exposto pelo PostgREST), com `SECURITY DEFINER`, `SET search_path = ''` e nomes qualificados.
- `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated`; `GRANT EXECUTE` só a `docredux_backend` e `service_role`.
- `private.credito_conceder(usuario, valor, tipo, motivo, admin)`: concessão e recarga.
- `private.credito_reservar(documento)`: trava a linha de `perfis_usuario` (`FOR UPDATE`), confere o saldo, insere a reserva e atualiza o saldo. Saldo insuficiente → erro `saldo_insuficiente`.
- `private.credito_capturar(reserva)` e `private.credito_estornar(reserva, motivo)`: idempotentes pelo índice único em `reserva_id`.
- Gatilho em `auth.users` (insert), `SECURITY DEFINER`: cria `perfis_usuario` e concede `coalesce(config.creditos_iniciais, 150)` (FR-3). O valor padrão evita que um `config` incompleto derrube o convite ("Database error saving new user").
- `creditos_ledger` é append-only: um gatilho recusa `UPDATE` e `DELETE`.
- Invariantes testadas: `perfis_usuario.saldo = soma(creditos_ledger.valor)` por usuário; nenhuma reserva aberta em documento de estado final.
- Ninguém altera `papel` nem `interno` pela API pública. O primeiro admin é definido por SQL na unidade 7.

**Storage** (buckets privados):
- `originais/{usuario_id}/{documento_id}/original.partNN`: cada parte com até 42 MB (DT-18; limite de objeto do bucket: 50 MB, o máximo do plano gratuito); o documento inteiro com até 80 MB. Política: `INSERT` para `authenticated` só quando `(storage.foldername(name))[1] = auth.uid()::text`. Sem `SELECT/UPDATE/DELETE` para usuário.
- `saidas/{usuario_id}/{documento_id}/v{versao}/saida.pdf` e `saida.docx`: só o backend escreve.
- Download sempre por **URL assinada de 60 s** gerada pela API, depois de conferir o dono. O visualizador usa a URL uma vez e trabalha sobre o blob (DT-12).
- Plano gratuito (DT-17, Q18): 50 MB por objeto (resolvido por DT-18), 1 GB no total (DT-20), sem backups automáticos (Q16). O projeto gratuito é pausado depois de 7 dias sem atividade: o `/health` chamado por um cron gratuito (por exemplo, o da Vercel) mantém o projeto ativo fora do piloto.

**Testes de RLS obrigatórios** (unidade 3, NFR-3/NFR-4), com dois usuários A e B, pelo SQL e pela API REST do Supabase (`/rest/v1/...` e `/rest/v1/rpc/...`):
A não lê documento, entrega, extrato, feedback nem arquivo de B; A não insere, altera nem apaga ledger; A não altera o próprio `saldo`, `papel` nem `interno`; A não executa as funções `private.credito_*` (nem por RPC); A não envia arquivo para a pasta de B; A não lê `avaliacoes_juiz`, `revisoes_humanas`, `metricas_entrega`, `jobs`, `workers` nem `config`; A não lê entrega de documento seu em `em_revisao`.

## 7. Fila e worker

**Conexão** (DT-14): o worker usa o Supavisor em modo sessão (IPv4, porta 5432); a API serverless, o modo transação (porta 6543). Sem add-on de IPv4 (custo, Q18).

**Reivindicar job** (DT-1):

```sql
UPDATE jobs
   SET status = 'processando', worker_id = $1, iniciado_em = now(),
       heartbeat_em = now(), tentativas = tentativas + 1
 WHERE id = (
   SELECT id FROM jobs
    WHERE status = 'na_fila' AND disponivel_em <= now()
    ORDER BY prioridade, criado_em
    FOR UPDATE SKIP LOCKED
    LIMIT 1)
RETURNING *;
```

- **Prioridades:** `triagem` = 0, `processar` = 10, `juiz` = 50. A entrega ao usuário vem antes da avaliação.
- **Laço:** a cada volta (2 s quando ocioso), o worker atualiza `workers.visto_em` (DT-15) e procura job.
- **Heartbeat** do job a cada 15 s.
- **Nova tentativa** (coletor e timeout usam a mesma regra): job `processando` com `heartbeat_em` há mais de 2 min (coletor, a cada 60 s, em qualquer worker) ou que estourou o **timeout duro** (`triagem` 5 min, `processar` 15 min, `juiz` 5 min, configuráveis por `WORKER_TIMEOUT_*_S`; o subprocesso é morto, DT-5) volta para `na_fila` com `disponivel_em = now() + 30 s × tentativas`, se `tentativas < max_tentativas`. Senão, chama o **tratador de falha final**.
- **Tratador de falha final** (`worker/falhas.py`, único para worker, timeout e coletor), por tipo:
  - `triagem`: documento → `upload_falhou`.
  - `processar` de origem `usuario`: documento → `falhou` + estorno (ou exclusão, se `exclusao_pendente`).
  - `processar` de origem `reprocessar`: aplica a coluna "Falha final" da tabela de reprocessamento do §5, pelo `estado_origem` do job (de `pronto`: só `reprocessando = false`).
  - `juiz`: motivo `juiz_falhou` na fila do backoffice.
- **Concorrência:** `WORKER_PROCESSOS` (padrão 2; na Oracle A1 com 2 OCPU, 2). Cada job usa um diretório temporário e um perfil do LibreOffice só dele. O original baixado fica em cache local por documento até o fim do processamento.
- **Índices:** parcial em `(prioridade, criado_em) WHERE status = 'na_fila'`; único parcial de um job ativo por (`documento_id`, `tipo`).
- **Limpeza** (retenção, §14): tarefa diária no laço do worker, com `pg_try_advisory_lock` para rodar em um worker só. Não é job da fila.

**Jobs:**

| Tipo | Faz | Sucesso | Falha final |
|---|---|---|---|
| `triagem` | baixa as partes do original e concatena (DT-18), `triar()`, grava páginas, palavras, tipo, perfil e recusa; `perfil_id` nulo → recusa `template_novo` (Q3) + aviso `em_revisao` com prazo de 2 meses (DT-22) | `aguardando_confirmacao` ou `recusado` | `upload_falhou` |
| `processar` | `processar()` com o perfil e as opções; sobe as saídas na versão nova; grava `entregas` e `metricas_entrega` | gate ok → `pronto` + captura (só na primeira entrega) + enfileira `juiz` + aviso `pronto` (DT-22); gate bloqueado → `em_revisao` + enfileira `juiz` + aviso `em_revisao` (reprocessar: ver §5) | tratador de falha final |
| `juiz` | §11 | grava `avaliacoes_juiz` e métricas; recalcula a fila do backoffice | `juiz_falhou` |

**Exclusão durante um job** (LGPD): o worker confere `exclusao_pendente` e os status `excluido`/`expirado` **no fim de todo job** (triagem, processar, juiz) e **antes de cada chamada à API Anthropic**. Se marcado: descarta as saídas, não sobe nada, não chama o juiz, estorna se a reserva estiver aberta, apaga o conteúdo e leva o documento a `excluido` (ou `expirado`). Documentos `excluido`/`expirado` não geram motivos na fila do backoffice.

## 8. Estados do documento

```
aguardando_upload ─(upload ok)─► triagem ─┬─► recusado (escaneado | protegido | corrompido | excede_paginas | excede_tamanho | template_novo)
   │      ▲                               │      │ (template_novo + perfil criado pelo admin, FR-10)
   │      │                               │      └────────► triagem
   │      └──(nova tentativa)──┐          └─► aguardando_confirmacao ─(confirmar: reserva)─► na_fila ─► processando ─┬─► pronto ◄──────────┐
   └─(falha)─► upload_falhou ──┘                                                                                    ├─► em_revisao          │
                                                                                                                    └─► falhou              │
reprocessar (admin):  em_revisao | falhou ─► na_fila ─► processando ─► (regras do §5) ─────────────────────────────────────────────────────┘
                      pronto ─► pronto (reprocessando = true; só o job anda; regras do §5)
cancelar:             na_fila ─► cancelado (+estorno)                                [usuário, DT-13]
                      na_fila | em_revisao ─► cancelado (+estorno)                   [admin]
excluir (usuário):    qualquer estado ─► excluido (regras do §14)
expirar (30 dias):    qualquer estado que não seja excluido ─► expirado (regras do §14)
```

- **Em andamento** (polling de 3 s, DT-4): `aguardando_upload`, `triagem`, `na_fila`, `processando`.
- **De espera** (polling de 60 s): `em_revisao`.
- **Finais:** `aguardando_confirmacao` (até o usuário agir), `recusado`, `upload_falhou`, `pronto`, `falhou`, `cancelado`, `excluido`, `expirado`.
- `upload_falhou` volta a `aguardando_upload` quando o usuário tenta de novo com o mesmo documento (mesma `Idempotency-Key`).

## 9. API (`api/`, FastAPI)

- **Autenticação:** `Authorization: Bearer <JWT do Supabase>`, validado pelas chaves do projeto. O `sub` é o `usuario_id`. Rotas `/admin/*` exigem `papel = 'admin'` lido do banco a cada requisição.
- **Erros:** `{"erro": {"codigo": "...", "mensagem": "..."}}`, com os códigos `nao_autenticado` (401), `proibido` (403), `nao_encontrado` (404), `termo_pendente` (403), `saldo_insuficiente` (402), `orcamento_mudou` (409), `estado_invalido` (409), `limite_excedido` (413), `validacao` (422).
- **CORS:** só o domínio do `web/`. Nenhum log grava conteúdo de documento.

### Usuário

| Método e rota | Faz | Requisitos |
|---|---|---|
| `GET /health` | DB ok, algum worker com `visto_em` < 60 s, versão | DT-15, unidade 7 |
| `GET /me` | perfil, saldo, termo aceito?, senha provisória? | FR-2, FR-18, DT-19 |
| `POST /me/termo` `{versao}` | registra o aceite | FR-18 |
| `POST /me/senha-trocada` | chamado depois do `updateUser({password})` no front; zera `senha_provisoria` | FR-1, DT-19 |
| `DELETE /me` `{confirmacao: "EXCLUIR"}` | exclusão da conta (§14) | FR-19, Q7 |
| `GET /creditos/extrato?cursor=` | ledger paginado, com saldo após cada lançamento | FR-2 |
| `POST /documentos` `{nome_arquivo, tamanho_bytes}` + `Idempotency-Key` | confere termo, limite de 80 MB (413) e teto de Storage (503 `capacidade_piloto`, DT-20); cria o documento (`aguardando_upload`) ou devolve o existente; responde `documento_id`, `partes` e os caminhos de upload de cada parte (DT-18) | FR-4, NFR-2 |
| `POST /documentos/{id}/upload-concluido` | confere as partes no Storage (todas existem, soma = `tamanho_bytes`); enfileira `triagem`; ou marca `upload_falhou` | FR-4, FR-8, NFR-2 |
| `GET /documentos/{id}` | status, triagem, opções disponíveis, orçamento, feedback, problemas; a entrega (relatório) **só** em `pronto` (nunca em `em_revisao`) | FR-8, FR-11, FR-14 |
| `POST /documentos/{id}/orcamento` `{opcoes}` | `orcar()`, saldo após; não reserva | FR-7 |
| `POST /documentos/{id}/confirmar` `{opcoes, creditos_esperados}` | recalcula; diferente → 409; reserva; enfileira `processar` | FR-9 |
| `GET /documentos?cursor=` | histórico | FR-14 |
| `GET /documentos/{id}/arquivo?formato=pdf\|docx` | URL assinada de 60 s da versão atual; DOCX só se foi comprado; só em `pronto` | FR-14, NFR-9 |
| `POST /documentos/{id}/cancelar` | só em `na_fila`; estorna (DT-13) | FR-21 |
| `DELETE /documentos/{id}` | §14 exclusão | FR-19 |
| `PUT /documentos/{id}/avaliacao` `{nota, comentario?}` | só em `pronto`; vale para a versão atual | FR-15 |
| `POST /documentos/{id}/problemas` `{pagina, tipo, descricao}` | só em `pronto`; página entre 1 e `paginas_depois` da versão atual | FR-15 |

### Backoffice (`papel = admin`)

| Método e rota | Faz | Requisitos |
|---|---|---|
| `GET /admin/fila?motivo=` | itens com motivo, usuário, idade, dias até expirar, veredito do juiz, nota | FR-17 |
| `GET /admin/documentos/{id}` | tudo de `GET /documentos/{id}` + todas as versões de entrega, avaliações do juiz, jobs, revisões; URLs assinadas do original e das saídas | FR-17 |
| `POST /admin/documentos/{id}/revisao` `{veredito, concorda_juiz?, minutos, notas, motivo}` | grava a revisão; atualiza `metricas_entrega` (intervenção, minutos) | FR-17, FR-20 |
| `POST /admin/documentos/{id}/perfil` `{perfil_json, salvar_como_privado}` | só em `recusado` com `motivo_recusa = template_novo` e sem exclusão/expiração; `validar_perfil`; grava o perfil (dono = usuário do documento, ou semente se `salvar_como_privado = false`); marca `template_novo = true` e reenfileira `triagem` (→ `aguardando_confirmacao` com a sobretaxa) | FR-10, Q3 |
| `POST /admin/documentos/{id}/reprocessar` `{opcoes?, perfil_id?}` | de `em_revisao`, `pronto` ou `falhou`; regras de crédito e estado do §5 | FR-17 |
| `POST /admin/documentos/{id}/cancelar` `{motivo}` | de `na_fila` ou `em_revisao`; cancela e estorna | FR-17 |
| `PATCH /admin/problemas/{id}` `{status}` | marca como resolvido | FR-17 |
| `GET /admin/usuarios` | e-mail, interno?, saldo, documentos, nota média | FR-17 |
| `POST /admin/usuarios/{id}/recarga` `{creditos, motivo}` | `credito_conceder(tipo = recarga)` | FR-17, piloto §2 |
| `DELETE /admin/usuarios/{id}` | exclusão da conta a pedido do participante (mesmas regras de `DELETE /me`, §14) | FR-19, Q7 |
| `GET /admin/metricas?formato=json\|csv&incluir_internos=false` | agregados do §12 + linhas por entrega | FR-20 |
| `GET/PUT /admin/config` | parâmetros do piloto (DT-9) | DT-9 |

**Motivos da fila do backoffice** (calculados por view; limiares em `config`):

| Motivo | Regra | Fonte |
|---|---|---|
| `template_novo` | documento `recusado` com `motivo_recusa = template_novo`, ainda não apagado (pedido de perfil, Q3) | P4, PL2 |
| `gate_cobertura` | documento em `em_revisao` | P6 |
| `nota_baixa` | `feedback.nota ≤ 3` (Q6, definido pelo @pm) | PL2 |
| `divergencia` | juiz `reprovado`/`duvidoso` e nota ≥ 4; ou juiz `aprovado` e nota ≤ 2 (Q6) | PL2, PL5 |
| `juiz_reprovou_sem_nota` | juiz `reprovado` e usuário sem nota há 48 h (Q6) | P6/P10 (gate → revisão humana) |
| `problema_marcado` | problema `aberto` | P8 |
| `juiz_falhou` | job `juiz` em `falhou` | NFR-1 |
| `amostra` | sorteio de **10%** das entregas `pronto` sem outro motivo (`config.amostra_revisao = 0,10`; Q11) | piloto §4 (concordância sem viés) |

O item sai da fila quando há uma revisão humana posterior ao motivo, ou quando o problema é resolvido. A1 destaca itens com menos de 5 dias para expirar.

## 10. Telas

Next.js com `supabase-js` só para o login e o upload. O resto passa pela API. Todas as telas do usuário são testadas em 375 px e no desktop (NFR-7).

### Usuário

| Tela | Conteúdo | Requisitos |
|---|---|---|
| U1 `/entrar` | e-mail + senha (`signInWithPassword`); erro neutro ("e-mail ou senha incorretos"); sem "esqueci a senha": "fale com o responsável" (DT-19) | FR-1 |
| U1b `/trocar-senha` | obrigatória enquanto `senha_provisoria = true`: nova senha (mín. 10 caracteres) e confirmação → `updateUser` → `POST /me/senha-trocada` | FR-1, DT-19 |
| U2 `/termo` | texto do termo v1 (§14) + "Aceito"; bloqueia o resto até aceitar | FR-18 |
| U3 `/documentos` | saldo no topo; botão "Enviar PDF"; histórico: nome, data, status, páginas antes → depois, créditos, "apaga em N dias" | FR-2, FR-14 |
| U4 `/enviar` (passo 1) | área de soltar arquivo; limites (300 págs., 80 MB); progresso único somando as partes; retomada automática; erro com "tentar de novo"; `503 capacidade_piloto` → "o piloto atingiu o limite de armazenamento; tente mais tarde". O arquivo é fatiado com `Blob.slice` em partes de 42 MB (DT-18), enviadas em sequência. `tus-js-client`: chunks de 6 MB, host de storage direto do projeto, token renovado em `onBeforeRequest`; resposta 409 (objeto já existe) → chama `upload-concluido` em vez de reenviar | FR-4, NFR-2 |
| U4 (passo 2) | "analisando…" (triagem); recusa com motivo e o que fazer; `template_novo`: "este modelo de documento ainda não é atendido no piloto; registramos o pedido e ele pode passar a ser aceito em até 2 meses" (Q3) | FR-5, FR-8, FR-10 |
| U4 (passo 3) | tipo detectado; perfil usado; sobretaxa quando o perfil foi criado para este documento (FR-10); opções: texto original ou síntese 20/35/50% (só relatório); manter capa e elementos; PDF ou DOCX; custo item a item, total e saldo depois; "Confirmar" | FR-6, FR-7, FR-10 |
| U5 `/documentos/[id]` (andamento) | etapas: na fila → processando → pronto; "em revisão"; cancelar quando permitido. Se o worker roda no PC (DT-17): "o processamento acontece em horário comercial" | FR-11, FR-21 |
| U5 (pronto) | visualizador pdf.js sobre o blob (DT-12); **Imprimir** (a partir do blob); **Baixar PDF**; **Baixar DOCX** (se comprado); economia: páginas antes/depois, folhas poupadas (frente e verso) e faixa de R$ poupados por cópia a R$ 0,10–0,20 por página (margem §10.1); selo de integridade (cobertura) **ou** relatório de síntese (% real, o que saiu); relatório de modificações com link que pula para a página no visualizador | FR-13, FR-14 |
| U5 (avaliar) | nota 1–5 + comentário; "Marcar problema": página (preenchida pela página aberta), tipo erro/formato, descrição | FR-15 |
| U5 (excluir) | "Apagar documento" com confirmação: apaga original e saídas agora | FR-19 |
| U6 `/creditos` | saldo e extrato | FR-2 |
| U7 `/conta` | e-mail; trocar senha; "Excluir minha conta": explica o efeito (§14), pede para digitar EXCLUIR e encerra a sessão | FR-19, Q7 |

### Backoffice (`/admin`, só admin)

| Tela | Conteúdo | Requisitos |
|---|---|---|
| A1 Fila | tabela por motivo, idade e dias até expirar; filtros; contadores por motivo | FR-17 |
| A2 Revisão | links para o original e as saídas (abas novas, todas as versões); diagnóstico do juiz por critério, com páginas; relatório do motor (cobertura, estrutura, modificações); nota, comentário e problemas do usuário; histórico de jobs. Ações: registrar revisão (veredito, concorda com o juiz, minutos, notas), reprocessar, cancelar com estorno, resolver problema | FR-17, PL2 |
| A3 Template novo (dentro da A2) | pedido de perfil de um documento recusado por `template_novo`: dias até expirar; baixar o original; colar ou enviar o JSON do perfil; validação; "salvar como perfil privado da conta" (D2) ou como semente; reenviar à triagem | FR-10 |
| A4 Usuários | lista com interno?, saldo, documentos e nota média; recarga com motivo; excluir conta a pedido (Q7). Criar conta e redefinir senha ficam no painel do Supabase (DT-19, ASM-6) | FR-17 |
| A5 Métricas | critérios do piloto §4 (meta × valor atual, só participantes por padrão); tabela por entrega; uso do Storage × teto (DT-20); exportar CSV | FR-20 |
| A6 Configuração | parâmetros da `config` | DT-9 |

## 11. Juiz Opus (P10, D17)

- **Quando:** um job `juiz` para cada saída gerada: **toda entrega `pronto`** (100%, PL5) e também cada saída em `em_revisao`, para o diagnóstico já estar pronto na tela A2 (PL2). `config.juiz_fracao = 1.0` no piloto; D17 prevê reduzir para 25%, 10% e 5% depois, por métricas.
- **Modelo:** `config.juiz_modelo` (padrão: Opus atual, `claude-opus-5-5`). O preço por milhão de tokens também fica em `config`, e o custo é calculado e gravado por chamada (FR-16, P12). A unidade 6 carrega a skill `claude-api` para confirmar preços, cache de prompt e saída estruturada.
- **Entrada mínima** (NFR-5):
  - documento de **até 5 páginas** (2 créditos): só as **folhas de contato** do original e da saída (margem §10.3);
  - **demais documentos:** folhas de contato (60 dpi, várias páginas por imagem) + o relatório do motor (cobertura, palavras faltantes, contagens de estrutura, modificações) + as páginas de maior risco em resolução maior (tabelas, gráficos e páginas com palavras faltantes), com teto de imagens em `config`;
  - **síntese:** soma o texto do núcleo (títulos, PROBLEMA, CAUSAS CRÍTICAS, frases com números) do original e da saída, e as frases da saída que começam com anáfora, detectadas por script.
- **Rubrica** (P10): `cobertura`, `nucleo`, `numeros`, `frases_orfas` (só síntese) e `layout`. Cada critério: `ok`, `problema` ou `nao_aplicavel`, com evidências (`pagina_saida`, `pagina_original`, `descricao`).
- **Saída** (schema validado): `{veredito: aprovado|reprovado|duvidoso, nota_juiz: 1–5, criterios: {...}, resumo}`. Fora do schema → 1 nova tentativa; depois, falha (`juiz_falhou`).
- **Custo esperado:** ≈ R$ 0,60 por documento médio (revisão §6); ≈ R$ 300 para 500 entregas (margem §10.3).
- **Validação** (unidade 6): 10 entregas com defeito plantado (texto faltando, número trocado, frase órfã, tabela quebrada, página em branco) → o juiz aponta ≥ 9.
- **Privacidade:** a API Anthropic está no termo (piloto §2). Nada além da entrada mínima vai para fora; a unidade 6 guarda, em teste, o log de uma requisição para provar o NFR-5.

## 12. Métricas (P12)

`metricas_entrega`, uma linha por documento confirmado, preenchida aos poucos pela API e pelo worker. Os valores de tamanho e qualidade são os da **versão atual**; os da **primeira passagem** ficam em colunas próprias (DT-16).

| Grupo | Colunas |
|---|---|
| Identificação | `documento_id`, `usuario_id`, `interno`, `tipo`, `perfil_id`, `template_novo`, `versao_motor` |
| Opções e preço | `modo`, `fracao_pedida`, `docx`, `manter_capa_elementos`, `creditos` |
| Tamanho | `paginas_antes`, `paginas_depois`, `palavras_original`, `palavras_saida`, `fracao_real` |
| Qualidade | `cobertura`, `gate_primeira_passagem`, `cobertura_primeira_passagem`, `versoes` (1 + reprocessamentos) |
| Tempo | `t_fila_s` (confirmação → início), `t_processamento_s`, `t_total_s` (confirmação → primeiro `pronto`), `tempos_etapas jsonb`, `tentativas` |
| Juiz | `juiz_modelo`, `juiz_veredito`, `juiz_tokens_entrada`, `juiz_tokens_saida`, `juiz_custo_usd` (soma de todas as chamadas do documento) |
| Usuário | `nota_usuario`, `problemas_erro`, `problemas_formato` |
| Humano | `intervencao_humana bool`, `minutos_humanos`, `motivos_intervencao text[]` |

**Agregados do piloto** (view `v_criterios_piloto`, tela A5; piloto §4). Por padrão só participantes (`interno = false`, Q15):

| Métrica | Cálculo | Meta |
|---|---|---|
| Nota média do usuário | média de `nota_usuario` | ≥ 4,5 |
| Erros reportados | entregas com `problemas_erro > 0` / entregas | ≤ 3% |
| Concordância juiz × humano | revisões com `concorda_juiz = true` / revisões com `concorda_juiz` não nulo | ≥ 90% |
| Cobertura (modo original) | entregas originais com `cobertura ≥ 0,995` / entregas originais (sempre 100% por causa do gate; serve de alarme) | 100% |
| Gate na primeira passagem | processamentos em modo original com `gate_primeira_passagem = bloqueado` / processamentos em modo original | medir |
| Sem intervenção humana | entregas com `intervencao_humana = false` / entregas | medir |
| Custo real por entrega | média de `juiz_custo_usd` (convertido) + `minutos_humanos` | medir |

"Disposição de pagar" e "a síntese de 35% dispensa ler o original?" saem das entrevistas de saída (piloto §4), fora da plataforma.

## 13. Segurança

- Segredos (`SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `RESEND_API_KEY`) só nas variáveis de ambiente da Vercel (API), no `.env` do host do worker (fora do git, permissão só do usuário do Docker) ou no painel do Supabase; o front só tem a chave pública (anon). O worker não precisa da chave `service_role` do Auth, só da de Storage.
- Cadastro aberto **desligado** no Supabase Auth; contas criadas pelo admin no painel, confirmadas, com senha provisória (DT-19, ASM-6). Recuperação de senha por e-mail desligada no front (o SMTP padrão não entrega a participantes).
- Senha mínima de 10 caracteres (configuração do Auth); proteção contra senha vazada, se o plano gratuito oferecer.
- O Auth continua sem SMTP (Q12): o login é pela DT-19. O Resend, com remetente no domínio `.com.br`, envia só os dois avisos da Q8 (DT-22); o link mágico fica opcional para depois.
- PDF não confiável só é aberto no worker, em subprocesso com timeout (DT-2, DT-5).
- URLs assinadas de 60 s; buckets privados; RLS em tudo; funções do saldo fora do schema exposto; testes de RLS na unidade 3.
- Logs sem conteúdo de documento, só ids e métricas.

## 14. Termo do piloto, retenção e exclusão (P11)

**Termo v1** (versão gravada em `perfis_usuario.termo_versao`; texto final na unidade 7), com os itens do piloto §2:
1. Os documentos são usados só para o teste do DocRedux.
2. Original e saídas são apagados automaticamente **30 dias** após o envio. Não há cópias de segurança dos documentos (Q16).
3. Cada entrega é avaliada por uma pessoa (o responsável) e por um modelo de linguagem da Anthropic, que recebe partes do documento (páginas em imagem e trechos).
4. O usuário pode apagar qualquer documento e a própria conta a qualquer momento (Q7).
5. Documentos com dados de alunos: o usuário declara que pode enviá-los (P11, "aviso e tratamento"; "direito autoral declarado").
6. Notas, comentários e métricas numéricas (sem conteúdo do documento) ficam para a análise do piloto. Ao fim do piloto, a conta continua ativa até nova comunicação; os documentos seguem a regra dos 30 dias (Q17).
7. Onde os dados são processados: Supabase em São Paulo; processamento num computador do responsável ou num servidor em São Paulo (DT-17); site e API na Vercel (região São Paulo); avaliação pela Anthropic, nos EUA (transferência internacional, P11); avisos por e-mail ("documento pronto", "em revisão") enviados pelo Resend, nos EUA, que recebe só o seu e-mail e um texto fixo, sem conteúdo do documento (Q8, Q14).

**Apagar conteúdo** (usado pela retenção e pela exclusão): cancela os jobs `na_fila` do documento (triagem, processar, juiz); se houver job `processando` de qualquer tipo, marca `exclusao_pendente` e o worker conclui a remoção no fim dele (§7); remove do Storage o original e todas as versões de saída; apaga `evidencias` e `resumo` de `avaliacoes_juiz` e as `descricao` de `entregas.relatorio.modificacoes` (podem citar texto). Ficam as métricas, os vereditos, as notas e os comentários (item 6). O plano gratuito não tem backups automáticos, e não se fazem dumps de dados (Q16): o schema vive nas migrations do git.

**Retenção** (tarefa diária, §7): para todo documento com `expira_em < now()` que não esteja `excluido` nem `expirado`:
- `processando` ou `triagem`: marca `exclusao_pendente` (o worker conclui na volta seguinte);
- reserva aberta (`na_fila`, `em_revisao`): cancela os jobs, estorna, apaga conteúdo;
- demais estados: apaga conteúdo;
- estado final: `expirado`.

**Exclusão pelo usuário** (`DELETE /documentos/{id}`):
- `aguardando_upload`, `upload_falhou`, `recusado`, `aguardando_confirmacao`: apaga conteúdo.
- `na_fila`, `em_revisao`: cancela, estorna e apaga conteúdo (a reserva não foi capturada).
- `triagem`, `processando`: marca `exclusao_pendente`; o worker descarta as saídas no fim, estorna se a reserva estiver aberta e apaga.
- `pronto`, `falhou`, `cancelado`: apaga conteúdo (com as regras de jobs vivos acima: um reprocessamento ou juiz em curso é descartado no fim); créditos já capturados não voltam (em `falhou` e `cancelado` já foram estornados).
- Em todos os casos, o estorno depende da reserva estar aberta no ledger (§5).
- Estado final: `excluido`.

**Exclusão da conta** (Q7; `DELETE /me` ou `DELETE /admin/usuarios/{id}`):
1. Bloqueia o login na hora (ban no Auth pela API de admin) e grava `perfis_usuario.exclusao_conta_em`.
2. Aplica a exclusão de documento, acima, a todos os documentos da conta que não estejam `excluido` nem `expirado`. Documentos expirados já não têm conteúdo.
3. Apaga os dados pessoais e os textos livres: `perfis_usuario.email` vira nulo, e `feedback_usuario.comentario` e `problemas_marcados.descricao` são apagados (podem identificar a pessoa ou citar o documento).
4. Quando nenhum documento da conta tiver job vivo (`exclusao_pendente` resolvida), a tarefa diária remove o usuário do Auth. `perfis_usuario`, `creditos_ledger` e `metricas_entrega` ficam com o id pseudônimo, sem e-mail, para os agregados do piloto (item 6); o saldo restante vira um lançamento de ajuste para zero (créditos grátis, sem reembolso).
5. As FKs para `auth.users` não usam `ON DELETE CASCADE` nas tabelas que ficam.
6. Estado final da conta: `exclusao_conta_em` preenchido e sem usuário no Auth. Um e-mail igual criado depois é outra conta.

## 15. Riscos

| Risco | Mitigação na spec | Origem |
|---|---|---|
| Síntese ainda é protótipo por capítulo, sem render | story própria na unidade 2, com teste em ≥ 3 capítulos e no documento inteiro do ex. 05 | P5 ("Próximo") |
| "Manter capa e elementos" ainda não existe no motor | story própria na unidade 2; regressão dos ex. 01–05 com a opção desligada e ligada | P5, U-C |
| Regressão do motor ao virar biblioteca e ao mudar de Windows para Linux | NFR-6; DT-11 (Carlito, regressão no container); Q13 | plano §3, critique C-2 |
| Jobs longos | DT-1, DT-5 | plano §3 |
| Escalada via RLS ou RPC | §6 (schema privado, REVOKE de PUBLIC), testes pgTAP e REST | plano §3 |
| Upload de 80 MB falhando | DT-3 (TUS), DT-18 (partes ≤ 42 MB no plano gratuito), NFR-2 | plano §3 |
| Login sem e-mail (senha provisória vazando, participante esquecendo a senha) | DT-19: senha provisória por canal privado, troca obrigatória, reset pelo admin | critique C-11, Q12 |
| Template novo frequente entre os participantes (muitas recusas) | Q3: recusa sem reserva; semear perfis com documentos dos participantes antes da semana 1; contar recusas por `template_novo` nas métricas | plano §3 |
| Infra gratuita: sem capacidade na Oracle, PC desligado, projeto do Supabase pausado, Storage cheio | DT-17 (PC como reserva e aviso de horário), `/health` por cron, DT-20 (teto de Storage); A5 mostra uso e workers vivos | Q2, Q18 |
| Dados de alunos indo para a Anthropic ou para fora do país | NFR-5, termo (item 7), retenção de 30 dias; Supabase, worker e funções da Vercel no Brasil (Q14) | plano §3, P11 |
| Concordância medida com viés | amostra de 10% (Q11) | piloto §4 |
| Dry-run contaminando as métricas | `interno` + filtro na view (Q15) | piloto §5 |

## 16. Verificação por requisito

| Requisito | Como provar | Unidade |
|---|---|---|
| FR-1 | conta criada no painel com senha provisória → login → troca obrigatória (U1b) → login com a nova; senha errada e e-mail inexistente → mesma mensagem neutra; cadastro pela API pública do Auth → recusado | 5, 7 |
| FR-2, FR-3 | conta nova com 150 e lançamento de concessão; extrato mostra concessão, reserva, captura e estorno de um caso real de falha | 3, 5 |
| FR-4, NFR-2 | ex. 05 (62 MB, 2 partes) e um PDF de 80 MB sobem e retomam depois de cortar a rede no meio da 2ª parte; o worker concatena e o SHA-256 bate com o arquivo local; 80 MB + 1 → 413; POST repetido com a mesma chave não duplica; 409 do TUS tratado; teto de Storage atingido → 503 | 4, 5 |
| FR-5 | PDFs sintéticos escaneado, com senha, corrompido e com 301 págs. → motivo certo, nenhuma reserva | 2, 4 |
| FR-6, FR-13 | ex. 05 em original e em síntese 20/35/50%, com e sem capa, PDF e DOCX; síntese mostra % real e o que saiu, sem selo | 2, 5 |
| FR-7 | teste unitário de `orcar` com todos os casos do §5; tela = API | 4, 5 |
| FR-8, NFR-6 | regressão dos ex. 01–05 no container (tipo, perfil, páginas, cobertura) | 2 |
| FR-9 | pgTAP: reserva com saldo insuficiente falha; duas reservas concorrentes; captura e estorno idempotentes; invariante saldo = soma; nenhuma reserva aberta em estado final depois de cada caminho (falha, cancelamento, exclusão em cada estado, expiração, reprocessamento) | 3, 4 |
| FR-10 | PDF de template desconhecido → `recusado` (`template_novo`), sem reserva, item na fila; perfil anexado na A3 → triagem de novo → `aguardando_confirmacao` com a sobretaxa → confirmar → `pronto` | 4, 6 |
| FR-11, NFR-1 | E2E: upload → triagem → processar → pronto; matar o worker no meio → job retomado; matar na última tentativa → `falhou` e saldo devolvido | 4 |
| FR-12, FR-16 | perfil adulterado para cair abaixo de 99,5% → `em_revisao`, item na fila com diagnóstico do juiz, entrega invisível ao usuário | 4, 6 |
| FR-14, NFR-7, NFR-9 | roteiro no browser embutido em 375 px e no desktop: abrir o PDF, esperar 2 min, rolar até o fim e imprimir; DOCX só se comprado; nenhum outro formato | 5 |
| FR-15 | nota, comentário e problema gravados; aparecem nas métricas e na fila | 4, 5 |
| FR-16, NFR-5 | 10 defeitos plantados → ≥ 9 apontados; custo gravado; log de uma requisição com só imagens e trechos | 6 |
| FR-17 | cada motivo da fila com um caso de teste; revisão, reprocessamento (três estados de origem × sucesso/gate/falha do §5), cancelamento e recarga; durante o reprocessamento de um `pronto`, o usuário tenta cancelar → 409 e a versão atual continua acessível | 6 |
| FR-18 | sem termo aceito → upload bloqueado (`termo_pendente`) e telas redirecionam para U2 | 4, 5 |
| FR-19 | documento com `expira_em` no passado em cada estado → efeito do §14; exclusão em cada estado → efeito do §14; excluir durante um reprocessamento e durante um juiz → nada no Storage e nenhuma chamada à Anthropic depois da exclusão; métricas ficam. Excluir a conta com documentos em vários estados (um processando) → login bloqueado na hora; e-mail, comentários e descrições apagados; Storage vazio; usuário removido do Auth depois do job; saldo zerado por `ajuste`; agregados do piloto inalterados | 4, 5, 6 |
| FR-20 | dry-run: 10 documentos, métricas completas, A5 bate com o CSV, internos fora dos agregados | 8 |
| FR-21 | cancelar em `na_fila` estorna; cancelar em `processando` → 409 | 4 |
| DT-22 (Q8) | cliente falso do Resend: `pronto` e `em_revisao` enviados uma vez por documento, mesmo com o job repetido; e-mail sem nome nem conteúdo do documento; falha do envio não muda o status do documento e é repetida; sem `RESEND_API_KEY` → `desligado` | 4 |
| NFR-3, NFR-4 | testes de RLS do §6, por SQL e por REST/RPC | 3 |
| NFR-8 | ex. 05 no host do worker do piloto (Oracle A1 ou PC) termina dentro do timeout de 15 min (esperado: ~1 min de CPU) | 7, 8 |

## 17. Matriz de rastreabilidade

| Requisito | Fonte | Seção da spec |
|---|---|---|
| FR-1 | piloto §3 P1, PL1 | §9 (auth), §10 U1, §13 |
| FR-2 | piloto §3 P1/P9 | §5, §9, §10 U3/U6 |
| FR-3 | piloto §2, PL3 | §5, §6 (gatilho) |
| FR-4 | piloto §3 P2 | §6 Storage, §9, §10 U4 |
| FR-5 | piloto §3 P2, D5 | §4 `triar`, §8, §10 U4 |
| FR-6 | piloto §3 P2, U-B/U-C/U-E | §4 `Opcoes`, §10 U4 |
| FR-7 | piloto §3 P2/P3, P9, margem §10 | §5, §9, DT-10 |
| FR-8 | piloto §3 P3 | §4 `triar`, §7 |
| FR-9 | piloto §3 P3/P9 | §5, §6 funções, §7 |
| FR-10 | piloto §3 P4, D1, Q3 | §5 template novo, §8, §9 admin, §10 U4/A3 |
| FR-11 | piloto §3 P5, PL4 | §7, §8, DT-4 |
| FR-12 | piloto §3 P6, §4 | §4 gate, §7, §9 motivos |
| FR-13 | P6, conflito 1 | §4 `Relatorio`, §10 U5 |
| FR-14 | piloto §3 P7, U-G/U-H, D9 | §9, §10 U5, DT-12 |
| FR-15 | piloto §3 P8, PL5 | §9, §10 U5 |
| FR-16 | PL5, D17, D14, P10/P15 | §11 |
| FR-17 | PL2, piloto §3 P10 | §5 reprocessar, §9 admin, §10 A1–A6 |
| FR-18 | piloto §2/§3 P11 | §9, §10 U2, §14 |
| FR-19 | piloto §2/§3 P11 ("apagar a qualquer momento"; Q7: documentos e conta) | §7 limpeza, §9 `DELETE /me`, §10 U7, §14 |
| FR-20 | piloto §3 P12, §4 | §12, §10 A5 |
| FR-21 | DT-13 (derivado de P3 conflito 5 e P9) | §8, §9 |
| NFR-1 | plano (adr-001) | §7, DT-1, DT-5, DT-14 |
| NFR-2 | plano (FU-001) | §9, §10 U4, DT-3 |
| NFR-3/4 | plano (despensa), P11, D2 | §6, §13 |
| NFR-5 | plano riscos, margem §10.3 | §11 |
| NFR-6 | plano restrições, P10 | §4, DT-11, §16 |
| NFR-7 | plano unidade 5 | §10 |
| NFR-8 | PL1, LOG 5b | §7, §16 |
| NFR-9 | D9, U-H | §9, §10 |

## 18. Perguntas ao responsável (respondidas em 2026-11-18; Q2, Q8, Q12 e Q14 atualizadas em 2026-11-19)

Q1–Q18 respondidas; Q19 aberta, com padrão que não bloqueia. A coluna "Efeito" diz onde a resposta entrou na spec.

| # | Pergunta | Resposta | Efeito |
|---|---|---|---|
| Q1 | Local do repositório | **Confirmado em 2026-11-19.** Repositório novo `https://github.com/ianbenicio/Nexa_DocRedux.git`, com o monorepo. A pasta atual fica como acervo do método. Os PDFs dos ex. 01–05 (até 62 MB) ficam **fora do git**, num diretório de fixtures apontado por variável de ambiente | unidade 2; push só pelo @devops |
| Q2 | VPS e domínio | **Decidido em 2026-11-19:** a infraestrutura segue a DT-17 (Vercel Hobby em `*.vercel.app`, worker na Oracle Always Free São Paulo com o PC como alternativa; sem Hetzner, por causa da Q14). Entra um domínio `.com.br` no Registro.br (~R$ 40/ano), usado só como remetente dos e-mails da Q8; a compra fica com o responsável | DT-17, DT-22, §2, unidade 7 |
| Q3 | Prazo do template novo | 2 meses → no piloto, template novo é **recusado na triagem**, sem reserva, e vira pedido de perfil (até 2 meses) | §1, §5, §8, §9, §10, FR-10, DT-13 |
| Q4 | Síntese só para relatório | Sim (confirmado em 2026-11-19) | §4, U4 |
| Q5 | Perfis semente para todas as contas | Sim, nesta primeira fase | §6 seed |
| Q6 | Limiares | Definidos pelo @pm: nota baixa ≤ 3; divergência = juiz `reprovado`/`duvidoso` com nota ≥ 4, ou juiz `aprovado` com nota ≤ 2; juiz reprovou e não há nota em 48 h → entra na fila. Ajustáveis em `config` | §9 |
| Q7 | Apagar documentos ou a conta | Os dois | §9 `DELETE /me`, §10 U7, §14 |
| Q8 | E-mail quando o documento fica pronto | **Decidido em 2026-11-19: sim.** O backend envia dois e-mails, "documento pronto" e "em revisão, prazo X", via Resend no plano grátis (100/dia, 3.000/mês), com remetente no domínio da Q2. O e-mail não leva conteúdo nem nome do documento, só o link para a plataforma. O status continua na tela (U3/U5) | §1, DT-22, §6 `notificacoes`, story 4.7 |
| Q9 | Tabela P9 e arredondamento | Corrigir P9 (feito em `processos.md`); teto, que é a regra da margem (DT-10) | DT-10, `processos.md` P9 |
| Q10 | Região do Supabase | Ainda não configurado. Criar o projeto em `sa-east-1` (São Paulo) | DT-17, termo item 7 |
| Q11 | Amostra de 10% | Sim | §9 motivos |
| Q12 | SMTP do Auth | **Atualizado em 2026-11-19:** o login continua pela DT-19 (e-mail + senha criada pelo admin). O SMTP (Resend + domínio) atende só às notificações da Q8; o link mágico fica opcional para depois | DT-19, DT-22, §10 U1/U1b, §13 |
| Q13 | Carlito no lugar da Calibri | Aceito (confirmado em 2026-11-19, com aprovação visual do responsável), com novo baseline aprovado pelo responsável e registrado no `LOG.md`, se o Linux mudar alguma contagem; cobertura nunca cai | DT-11, unidade 2 |
| Q14 | Servidor fora do Brasil | **Atualizado em 2026-11-19:** Supabase, worker e funções da Vercel no Brasil; fora ficam só a Anthropic e o Resend (EUA), ambos no termo, item 7. O Resend recebe só o e-mail do usuário e um texto fixo, sem conteúdo de documento | DT-17, DT-22, §14 item 7 |
| Q15 | Usuários internos | Marcar como `interno` e excluir das métricas | §6, §12 |
| Q16 | Backups além dos 30 dias | Não guardar: o plano gratuito não tem backup automático e não se fazem dumps de dados | §6, §14 |
| Q17 | Fim do piloto | Contas mantidas até segunda ordem; documentos seguem os 30 dias | §14 item 6 |
| Q18 | Custo do Supabase Pro | Sem custo agora: plano gratuito | DT-17, DT-18, DT-20 |
| Q19 | Prazo X do e-mail "em revisão" quando a causa é o gate de cobertura | **Aberta (v1.5).** Padrão: o e-mail não promete prazo ("uma pessoa vai conferir; você recebe outro aviso quando ficar pronto"); o responsável define o texto em `config.notificacao_prazo_revisao`, sem deploy. No template novo o prazo é o da Q3 (até 2 meses) | DT-22 |

**Pendências que dependem só de execução** (unidade 7):
- conseguir capacidade de Ampere A1 na Oracle em São Paulo (o cadastro pede cartão para verificação, sem cobrança no Always Free);
- conferir se o pooler do plano gratuito comporta a API (modo transação) e os workers (modo sessão);
- conferir o limite de corpo e a duração das funções Python na Vercel Hobby para as rotas da API.

## Histórico

- **1.0** (2026-11-18): primeira versão, para a critique.
- **1.1** (2026-11-18): correções da critique (rodada 1, média 3,8):
  - C-1: tabela de reprocessamento por estado (§5), entregas versionadas (DT-16), transições no §8.
  - C-2: Carlito + regressão no container (DT-11), Q13.
  - C-3: exclusão em `em_revisao` estorna (§14).
  - C-4: tratador único de falha final; timeout volta para a fila (§7).
  - C-5: retenção alcança todos os estados (§8, §14).
  - C-6: amostra de 10% nos dois lugares (§9, Q11).
  - C-7: arredondamento como DT-10, casos de fronteira, Q9.
  - C-8: visualizador sobre blob (DT-12).
  - C-9: schema `private`, `search_path`, REVOKE de PUBLIC, padrão no gatilho (§6).
  - C-10: Supavisor em modo sessão (DT-14).
  - C-11: SMTP próprio (§13, Q12).
  - C-12: §16 com todos os FR e NFR verificáveis.
  - C-13: `interno` e filtro nos agregados (Q15).
  - C-14: FR-21 como DT-13.
  - C-15: `excede_tamanho` no `Triagem`; limpeza fora da fila, com advisory lock; estados renomeados.
  - C-16: juiz também em `em_revisao`; `concorda_juiz` nulo.
  - C-17: tabela `workers` (DT-15).
  - C-18: detalhes do TUS (U4).
  - C-19: gate na primeira passagem (§12).
  - C-20: tabela de dependências (§3).
  - Perguntas novas: Q12–Q18.
- **1.2** (2026-11-18): condições da rodada 2 (4,4, APPROVED), aplicadas pelo autor, sem nova critique:
  - C-21: modelo único de reprocessamento (de `pronto` o status não muda); `jobs.estado_origem`; estorno decidido pela reserva no ledger (§5, §6, §7, §8).
  - C-22: "apagar conteúdo" cancela jobs na fila e marca `exclusao_pendente`; o worker confere a exclusão no fim de todo job e antes de chamar a Anthropic (§7, §14, §16).
  - C-23: `domainModel` do `requirements.json` atualizado.
  - C-24: linha `falhou` × gate preenchida; motivo da reserva do admin; `feedback_usuario` por versão (§5, §6).
  - C-25: papel `docredux_backend` e tamanho do pool (§6).
- **1.3** (2026-11-18): respostas do responsável a Q1–Q18 (§18):
  - Infraestrutura sem custo: DT-17 (Supabase Free em São Paulo, Vercel Hobby para web e API, worker no PC ou na Oracle Always Free), DT-14 (API no modo transação do pooler), DT-18 (original em partes de 42 MB), DT-19 (login com senha, sem SMTP), DT-20 (teto de Storage).
  - Template novo recusado na triagem (Q3): sai o estado `aguardando_perfil`; FR-10 vira pedido de perfil com reenvio à triagem.
  - Exclusão da conta (Q7): `DELETE /me`, U7, §14; ledger com `ajuste`.
  - Termo: sem backups, conta mantida no fim, locais de processamento (§14).
  - DT-10 confirmado (Q9); `processos.md` P9 corrigido.
  - Sem nova critique. As mudanças tocam a infraestrutura (unidade 7) e os fluxos de upload, login, template novo e exclusão (unidades 3–5); o @qa confere no gate das stories.
- **1.4** (2026-11-19): decisões da unidade 3 (Banco e RLS):
  - DT-21: o preço da síntese usa a contagem do verify, decidida pelo responsável em 2026-11-19; a triagem passa a contar com a mesma função (motor, unidade 4). O caso do §5 continua 55 / 73 / 92 (totais 89 / 107 / 126).
  - §6: sem FK para `auth.users`; o id fica como pseudônimo depois da exclusão da conta.
  - Sem nova critique. A unidade 4 troca a contagem de palavras da triagem e testa o caso do ex. 05.
- **1.5** (2026-11-19): decisões do responsável sobre domínio e e-mail (início da unidade 4):
  - Q2: domínio `.com.br` só como remetente; a infraestrutura segue a DT-17; sem Hetzner (Q14).
  - Q8 = sim: avisos "documento pronto" e "em revisão" via Resend, sem conteúdo nem nome do documento (DT-22, tabela `notificacoes`, story 4.7).
  - Q12: login segue pela DT-19; SMTP só para as notificações. Q14: Resend no termo, item 7.
  - Q19 nova (prazo do e-mail de revisão por gate), com padrão.
  - Fonte única: o repositório. A cópia de fora (`DocRedux/docs/spec/`) passa a ser só um aviso.
  - Sem nova critique.
- **1.6** (2026-11-19): resultado da unidade 4 (API e worker):
  - §7: timeout duro da triagem de 60 s para 5 min. A contagem da DT-21 extrai o texto inteiro (ex. 05: ~50 s no PC, contra ~1,5 s da contagem antiga); 60 s ficaria no limite. Consequência técnica da DT-21, não feature nova (story 4.4).
  - Sem nova critique.
