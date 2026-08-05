FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

# Core needs nothing; install the serving layer only.
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" streamlit pydantic

COPY src/ ./src/
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY data/ ./data/

# Fail the build if classification has regressed.
COPY tests/ ./tests/
RUN pip install --no-cache-dir pytest && python -m pytest tests -q

EXPOSE 8000 8501
CMD ["uvicorn", "rca_agent.api:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
