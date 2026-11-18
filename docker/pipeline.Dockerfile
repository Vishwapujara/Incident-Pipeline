# Pipeline image: ingestion, LLM extraction, spaCy validation, embeddings +
# HDBSCAN clustering, detection, summaries. Runs as a scheduled task
# (ECS Fargate via EventBridge Scheduler, or `docker compose run pipeline`).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# CPU-only torch first: the default x86 wheel bundles CUDA (~2 GB more).
RUN pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -m spacy download en_core_web_sm

# Bake the embedding model in, so a scheduled run doesn't download it.
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-small-en-v1.5')"

# AWS RDS CA bundle: lets libpq verify the server (PGSSLMODE=verify-full).
ADD https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem /etc/ssl/rds-global-bundle.pem
RUN chmod 644 /etc/ssl/rds-global-bundle.pem

COPY src/ src/
RUN useradd --system --uid 10001 pipeline && chown -R pipeline /opt/hf
USER pipeline

CMD ["python", "-m", "src.poll"]
