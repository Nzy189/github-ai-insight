FROM python:3.11-slim

LABEL org.opencontainers.image.title="GitHub AI Insight" \
      org.opencontainers.image.description="每日 GitHub AI 开源项目日报，自动分析打分并推送企业微信"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Dubai \
    DATA_DIR=/app/data \
    PUID=1000 \
    PGID=1000

WORKDIR /app

# tini 负责 PID 1 的信号转发，gosu 用于按 PUID/PGID 降权（适配 NAS 权限）
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini gosu ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY templates/ ./templates/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# sed 去掉可能的 CRLF —— 在 Windows 上 clone 的仓库直接构建也不会挂
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod +x /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p /app/data/reports /app/data/archive

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import sqlite3,os; sqlite3.connect(os.environ.get('DATA_DIR','/app/data')+'/github_ai_insight.db').close()" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "main.py"]
