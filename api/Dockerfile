# Imagem da API e do worker (story 4.6, spec §3 deploy/): uma imagem, dois comandos.
# Base = imagem do motor (engine/Dockerfile: Python 3.12 + LibreOffice 26.8.3.1 + Carlito + Liberation).
#
#   docker build -t docredux-engine engine/
#   docker build -t docredux-worker api/
#   docker run --env-file deploy/.env docredux-worker                      # worker (padrão)
#   docker run --env-file deploy/.env -p 8000:8000 docredux-worker api     # API (uvicorn)
#
# arm64 (Oracle A1): construir as duas com --platform linux/arm64 e passar --build-arg BASE=<motor arm64>.
ARG BASE=docredux-engine:latest
FROM ${BASE}

USER root
WORKDIR /srv
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY worker ./worker
COPY index.py ./
COPY docker-entrypoint.sh /usr/local/bin/docredux
RUN chmod 0755 /usr/local/bin/docredux \
    && mkdir -p /var/cache/docredux && chown motor /var/cache/docredux

ENV WORKER_CACHE=/var/cache/docredux \
    PYTHONPATH=/srv
USER motor
EXPOSE 8000
ENTRYPOINT ["docredux"]
CMD ["worker"]
