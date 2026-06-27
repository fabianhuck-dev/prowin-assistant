FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

# Venv außerhalb /app — sonst überschreibt das Volume-Mount ./backend:/app die Deps.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml .
# --no-install-project: nur Dependencies installieren, nicht das Projekt selbst
# (Quellcode kommt zur Laufzeit per Volume-Mount)
RUN uv sync --no-dev --no-install-project

COPY backend/ .

ENV PATH="/opt/venv/bin:$PATH"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
