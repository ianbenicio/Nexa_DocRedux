# Validação @po (Pax): stories 4.1–4.7 — 2026-11-19

Checklist de 10 pontos (`story-lifecycle`): 1 título claro · 2 descrição completa · 3 AC testáveis · 4 escopo (dentro/fora) · 5 dependências · 6 estimativa/complexidade · 7 valor de negócio · 8 riscos · 9 critério de pronto · 10 alinhamento com spec/epic.

| Story | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | Nota | Decisão |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4.1 Esqueleto da API | ✓ | ✓ | ✓ | ✓ | ✓ 3.1–3.3 | ✓ | ✓ | ~ (formato do JWT do Supabase local a confirmar: HS256 ou JWKS) | ✓ | ✓ §9, §13, DT-14, DT-19 | 9 | GO |
| 4.2 Créditos | ✓ | ✓ | ✓ (tabela do §5 inteira) | ✓ | ✓ 4.1 | ✓ | ✓ | ✓ | ✓ | ✓ §5, DT-10, DT-21 | 10 | GO |
| 4.3 Envio em partes | ✓ | ✓ | ✓ | ✓ | ✓ 4.1 | ✓ | ✓ | ~ (upload real no teste usa o endpoint padrão do Storage, não o TUS do front) | ✓ | ✓ §6, §9, DT-3, DT-18, DT-20, FU-001 | 9 | GO |
| 4.4 Worker | ✓ | ✓ | ✓ | ✓ | ✓ 4.2, 4.3, 2.3 | ~ (a maior da unidade) | ✓ | ~ (timeout da triagem sobe para 5 min por causa da DT-21) | ✓ | ✓ §7, §8, §12, DT-21 | 8 | GO |
| 4.5 Ciclo de vida | ✓ | ✓ | ✓ | ✓ | ✓ 4.4 | ✓ | ✓ (P11) | ✓ | ✓ | ✓ §14 | 10 | GO |
| 4.6 Imagem e E2E | ✓ | ✓ | ✓ | ✓ | ✓ 4.4, 4.5 | ✓ | ✓ | ~ (arm64 depende de QEMU no Docker Desktop; RAM livre do PC é baixa) | ✓ | ✓ §2, §3, §16 | 9 | GO |
| 4.7 Avisos por e-mail | ✓ | ✓ | ✓ (cliente falso) | ✓ (conta no Resend fora) | ✓ 4.4 | ✓ | ✓ (Q8) | ✓ (falha não derruba o job) | ✓ | ✓ DT-22, §6, Q19 | 10 | GO |

**Observações:**
- 4.7 é nova (Q8 = sim em 2026-11-19, spec v1.5, DT-22). Fica com o número 4.7 em `implementation.yaml`. A conta no Resend, o domínio e a chave ficam com o responsável e o @devops (unidade 7); a story só lê `RESEND_API_KEY`.
- 4.4 Dev Notes: o timeout da triagem de 60 s (§7) não comporta a contagem exata da DT-21 em relatórios grandes (~50 s no ex. 05 neste PC). Subir para 5 min é consequência técnica da DT-21, não feature nova. Aceito, com registro no spec (§7) no commit da story.
- 4.3 acrescenta `documentos.sha256` (opcional) e `entregas.tamanho_bytes`. O primeiro cumpre a DT-18 ("confere tamanho e SHA-256"), que não tinha onde guardar o hash; o segundo permite o teto da DT-20 sem ler `storage.objects`. Não são features novas.
- 4.1 e 4.5: a API recusa conta com `exclusao_conta_em` mesmo com JWT válido; deriva do §14 ("login bloqueado na hora").
- O juiz (unidade 6) só é enfileirado; nenhum worker desta unidade o consome.
- Nenhuma story faz push, deploy ou cria conta externa.

Status das 7 stories: Draft → **Ready**.
