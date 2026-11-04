# Image du dashboard pour Cloud Run (construite par « gcloud run deploy --source . », voir deployer.ps1).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py metrics.py store.py stripe_client.py instagram.py collecte_ig.py carte.py ./
COPY geo ./geo
COPY static ./static

# Cloud Run fournit le port dans $PORT. Les données sont dans le bucket (GCS_BUCKET), la clé Stripe
# vient de Secret Manager : ni .env ni data/ ne sont copiés dans l'image.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips="*"
