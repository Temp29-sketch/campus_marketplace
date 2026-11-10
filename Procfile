web: gunicorn app:app --bind 0.0.0.0:$PORT --workers ${WEB_CONCURRENCY:-2} --threads 4 --timeout 60 --access-logfile -
release: flask --app app db upgrade && flask --app app seed
