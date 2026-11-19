#!/bin/sh
# `docredux worker [--processos N]` | `docredux api` | qualquer outro comando.
set -e
case "$1" in
    worker) shift; exec python -m worker "$@" ;;
    api) shift; exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers "$@" ;;
    *) exec "$@" ;;
esac
