FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build context is the REPOSITORY ROOT, not backend/. That is what makes
# packages/conversa visible to pip - an editable install cannot reach
# outside its build context, and the engine package lives beside the app
# rather than inside it.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The engine, installed editable. In dev the same path is bind-mounted, so
# edits to the package are live without a rebuild.
COPY packages/ /packages/
RUN pip install --no-cache-dir -e /packages/conversa

COPY backend/ .

# Bake the embedding model into the image so the container needs no
# network at runtime and the first search isn't a 20s cold start.
# Goes through the router rather than naming the model here, keeping
# model strings in exactly one place.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
RUN python -c "from conversa.core.model_router import _get_local_model; _get_local_model()"

EXPOSE 8000

# Migrations run before the app starts, so a fresh `make dev` on an empty
# volume comes up with the schema already applied.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
