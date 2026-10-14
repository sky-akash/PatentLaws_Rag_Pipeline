# Patent Law Navigator - API + page. The index (data/) is built outside the image and mounted, so the image stays
# small and the law can be refreshed without rebuilding:  docker compose up
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY patentlaw ./patentlaw
COPY static ./static
COPY eval ./eval
EXPOSE 8010
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8010/api/health')"
CMD ["uvicorn", "patentlaw.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010"]
