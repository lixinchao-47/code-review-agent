FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends docker-cli && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "langgraph>=1.1,<2.0" \
    "langchain>=1.2,<2.0" \
    "langchain-deepseek>=1.0,<2.0" \
    "pydantic>=2.0" \
    "python-dotenv>=1.0"

COPY src/ ./src/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app/src

CMD ["python", "scripts/run.py"]
