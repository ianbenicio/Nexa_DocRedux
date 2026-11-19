# Imagem base do motor DocRedux (DT-11, story 2.2): Python 3.12 + LibreOffice da mesma linha do baseline do
# Windows (26.8.3.1) + Carlito (métrica da Calibri) + Liberation. Multi-arquitetura: amd64 (PC) e arm64
# (Oracle A1, story 4.6). O Debian não empacota a 26.8: os .deb oficiais da TDF são baixados por arquitetura.
#
#   docker build -t docredux-engine engine/
#   docker run --rm -v <exemplos>:/fixtures:ro docredux-engine python tests/regressao.py
FROM python:3.12-slim-bookworm

ARG TARGETARCH
ARG LO_VERSION=26.8.3.1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl fontconfig \
        fonts-crosextra-carlito fonts-liberation fonts-dejavu-core \
        libxinerama1 libdbus-1-3 libcups2 libcairo2 libsm6 libice6 libx11-xcb1 libxrender1 libxext6 \
        libxml2 libnss3 libglib2.0-0 libgl1 libxrandr2 libxcursor1 libxi6 libxtst6; \
    case "$TARGETARCH" in \
        amd64|"") LO_ARCH=x86-64; LO_DIR=x86_64 ;; \
        arm64) LO_ARCH=aarch64; LO_DIR=aarch64 ;; \
        *) echo "arquitetura sem LibreOffice oficial: $TARGETARCH"; exit 1 ;; \
    esac; \
    url="https://downloadarchive.documentfoundation.org/libreoffice/old/${LO_VERSION}/deb/${LO_DIR}/LibreOffice_${LO_VERSION}_Linux_${LO_ARCH}_deb.tar.gz"; \
    curl -fsSL "$url" -o /tmp/lo.tgz; \
    mkdir /tmp/lo; tar -xzf /tmp/lo.tgz -C /tmp/lo --strip-components=1; \
    dpkg -i /tmp/lo/DEBS/*.deb; \
    ln -s /opt/libreoffice*/program/soffice /usr/local/bin/soffice; \
    rm -rf /tmp/lo /tmp/lo.tgz /var/lib/apt/lists/*; \
    fc-cache -f; \
    soffice --version

ENV DOCREDUX_SOFFICE=/usr/local/bin/soffice \
    DOCREDUX_FIXTURES=/fixtures \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /engine
COPY pyproject.toml README.md ./
COPY docredux_engine ./docredux_engine
COPY perfis ./perfis
COPY tests ./tests
RUN pip install --no-cache-dir -e ".[test]" \
    && useradd --create-home --uid 10001 motor \
    && mkdir -p /engine/tests/_saida && chown -R motor /engine/tests/_saida
USER motor

CMD ["python", "-m", "docredux_engine", "--help"]
