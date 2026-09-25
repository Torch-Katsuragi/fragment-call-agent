FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Silero VAD 等のモデルをビルド時に取得 (起動のたびのダウンロードを避ける)
RUN python agent.py download-files
CMD ["python", "agent.py", "start"]
