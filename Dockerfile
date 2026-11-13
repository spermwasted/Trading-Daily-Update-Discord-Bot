# GOLD FXCM Discord bot (analysis + Discord). Market data comes from fxcm-bridge.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
# The optional "ca" build secret is only needed behind a TLS-inspecting proxy.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --no-cache-dir -r /app/requirements.txt

COPY gold_bot /app/gold_bot

RUN useradd --create-home --uid 10001 bot && mkdir -p /app/data && chown bot /app/data
USER bot
VOLUME ["/app/data"]
CMD ["python", "-m", "gold_bot"]
