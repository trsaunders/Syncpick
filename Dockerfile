FROM python:3.12-alpine

LABEL org.opencontainers.image.title="syncpick" \
      org.opencontainers.image.description="Web UI for choosing which parts of a Syncthing folder to keep on this device"

RUN apk add --no-cache su-exec

WORKDIR /app
COPY app ./app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PORT=8080 \
    SYNCTHING_URL=http://syncthing:8384 \
    SYNCTHING_CONFIG_DIR=/syncthing-config \
    PYTHONUNBUFFERED=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:${PORT}/healthz >/dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "app"]
