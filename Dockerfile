FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home app && mkdir -p /app/uploads && chown -R app /app
USER app

EXPOSE 8000
# Settings (SECRET_KEY, DATABASE_URL, ...) come from the host's environment at runtime.
# Apply database migrations and seed categories/Bingham (both safe to repeat), then serve.
# If migrations/ is missing this fails loudly at boot, which beats serving a site with no tables.
CMD ["sh", "-c", "flask --app app db upgrade && flask --app app seed && exec gunicorn app:app --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY:-2} --threads 4 --timeout 60 --access-logfile -"]
