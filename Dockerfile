FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/licensing.db \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && mkdir -p /data

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import os,urllib.request;port=os.getenv('PORT','8080');urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2).read()" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
