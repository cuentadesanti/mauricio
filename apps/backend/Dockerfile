FROM python:3.12-slim

WORKDIR /app

# MM-1: Copy only pyproject.toml first so deps are cached independently of code changes
COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system .

# Now copy application code (changes here don't bust the pip cache)
COPY apps ./apps
COPY infra ./infra
COPY prompts ./prompts

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "apps.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
