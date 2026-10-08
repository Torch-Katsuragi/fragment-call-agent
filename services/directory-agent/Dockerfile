# Directory Agent worker — VM(compose)用コンテナ (2026-07-25)
# ローカルPCではDrive for DesktopのG:を直接読むホストプロセスとして実行するのが正
# (README / 開発記録 経緯 2026-07-17)。このイメージはVMで
# rclone同期(暫定はスナップショット)されたワークスペースをマウントして使う。
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir asyncpg
# 録音を Ogg Opus にする (2026-10-02、recordings.py)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY *.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "worker.py"]
