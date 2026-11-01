FROM python:3.11-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 HF_HOME=/hf-cache PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10
# CPU-only torch keeps the image far smaller than the default CUDA build.
RUN pip install --no-cache-dir torch==2.4.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements-base.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Run as an unprivileged user; these are the only writable locations.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data/uploads /data/results /hf-cache && chown -R app:app /data /hf-cache
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
COPY --chown=app:app experiments ./experiments
COPY --chown=app:app scripts ./scripts
USER app
EXPOSE 8000
# The API service applies migrations on boot. The worker overrides this command (see docker-compose.yml).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
