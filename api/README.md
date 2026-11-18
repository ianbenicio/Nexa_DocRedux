# api/

FastAPI (rotas do spec §9) + worker (fila no Postgres com `FOR UPDATE SKIP LOCKED`, jobs `triagem`, `processar`, `juiz`). Unidades 4 e 6.

O worker chama o motor em subprocesso: `python -m docredux_engine triar|processar|folha ... ` com saída JSON (spec §4, DT-5).
