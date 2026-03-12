FROM python:3.11-slim

WORKDIR /app

# System deps for psycopg2, pyproj, lightgbm (OpenMP)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libproj-dev proj-data libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy model artifacts
COPY lgb_dsi_model.pkl .
COPY model_features.pkl .

# Copy seed data (for initial DB population)
COPY cas_features.parquet .
COPY cas_cell_stats.parquet .

# Copy application code
COPY poc/ ./poc/

# Entrypoint script
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 5001

ENTRYPOINT ["./entrypoint.sh"]
