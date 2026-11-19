# deploy/

Arquivos de implantação. Segredos nunca entram no git (`.env`, `.env.*` estão no `.gitignore`; só os `.env.example` são versionados). Deploy, contas e segredos de produção: unidade 7 (@devops).

| Arquivo | Para quê |
|---------|----------|
| [`../engine/Dockerfile`](../engine/Dockerfile) | Imagem base do motor: Python 3.12 + LibreOffice 26.8.3.1 + Carlito + Liberation (amd64 e arm64) |
| [`../api/Dockerfile`](../api/Dockerfile) | API e worker numa imagem só, sobre a do motor: `docredux worker` (padrão) ou `docredux api` (uvicorn :8000) |
| [`docker-compose.dev.yml`](docker-compose.dev.yml) | Worker (e, no perfil `api`, a API) em container contra o Supabase local |
| [`.env.example`](.env.example) | Variáveis do worker de produção (Oracle A1 ou PC) |
| [`vercel.json`](vercel.json) | Projeto da API na Vercel Hobby (função Python `api/index.py`, região `gru1`, cron diário no `/health`) |

## Desenvolvimento

```bash
cd db && supabase start && cd ..
python api/scripts/dev_setup.py          # grava api/.env e deploy/.env.dev
docker build -t docredux-engine engine/
docker compose -f deploy/docker-compose.dev.yml up --build worker
```

A API local roda fora do container com `uvicorn app.main:app --reload` (em `api/`, com o `.venv` de `requirements-dev.txt`) ou no perfil `api` do compose.

E2E (story 4.6): `python -m tests.e2e` em `api/`, com o worker do compose de pé e os exemplos em `DOCREDUX_FIXTURES`.

## Produção (unidade 7)

- **Worker:** `docker build -t docredux-engine engine/ && docker build -t docredux-worker api/`; na Oracle A1, `docker buildx build --platform linux/arm64` nas duas, com `--build-arg BASE=<motor arm64>` na segunda. Rodar com `--env-file deploy/.env --restart unless-stopped`.
- **API:** projeto Vercel com *Root Directory* `api/` e este `vercel.json` copiado para lá; variáveis do `api/.env.example` no painel da Vercel. O `web/` é outro projeto Vercel (unidade 5).
- Conferir na unidade 7: limite de corpo e duração das funções Python na Vercel Hobby (spec §16).
