FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.12.5

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
COPY tests/ tests/
COPY data/ data/
COPY eval/ eval/
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD ["kg", "run"]
