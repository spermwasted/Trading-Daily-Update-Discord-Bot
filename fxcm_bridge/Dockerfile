# FXCM ForexConnect bridge: read-only market data service on Python 3.7.
FROM python:3.7-slim-bullseye

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY fxcm_bridge/requirements.txt /app/fxcm_bridge/requirements.txt
# The optional "ca" build secret is only needed behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r /app/fxcm_bridge/requirements.txt

COPY fxcm_bridge /app/fxcm_bridge

RUN useradd --create-home --uid 10001 bridge && mkdir -p /tmp/fxcm-history && chown bridge /tmp/fxcm-history
USER bridge

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "fxcm_bridge"]
