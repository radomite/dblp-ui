FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DBLP_DB=/data/dblp.sqlite3 DBLP_DATA_DIR=/data DBLP_TIMEZONE=Europe/Berlin
WORKDIR /app
COPY app /app/app
COPY entrypoint.sh /app/entrypoint.sh
RUN DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates tzdata && rm -rf /var/lib/apt/lists/* \
    && chmod +x /app/entrypoint.sh && useradd --uid 10001 --create-home dblp && mkdir /data && chown dblp:dblp /data
USER dblp
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=6h CMD python -c "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4))['ready']" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
