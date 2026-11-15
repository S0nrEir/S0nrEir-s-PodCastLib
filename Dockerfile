FROM python:3.12-alpine

LABEL org.opencontainers.image.title="podcast-topic-library"
LABEL org.opencontainers.image.description="播客选题库 - 私人选题记录网站"

WORKDIR /app
COPY server.py .
COPY static ./static

ENV DATA_DIR=/data PORT=8390 HOST=0.0.0.0
VOLUME /data
EXPOSE 8390

HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8390/api/health', timeout=5).status==200 else 1)"

CMD ["python", "-u", "server.py"]
