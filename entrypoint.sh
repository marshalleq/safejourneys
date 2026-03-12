#!/bin/bash
set -e

export PYTHONUNBUFFERED=1

echo "=== Safe Journeys — Starting up ==="

# Wait for database to be ready
echo "Waiting for database..."
for i in $(seq 1 30); do
    python -u -c "
from poc.db.connection import get_engine
engine = get_engine()
with engine.connect() as conn:
    conn.execute(__import__('sqlalchemy').text('SELECT 1'))
print('Database ready.')
" 2>/dev/null && break
    echo "  Attempt $i/30 — waiting..."
    sleep 2
done

# Initialise schema
python -c "from poc.db.connection import init_db; init_db()"

# Seed database if empty
python -u -c "
from poc.db.connection import table_row_count
try:
    count = table_row_count('crash_records')
    if count == 0:
        raise Exception('empty')
    print(f'Database has {count:,} records. Skipping seed.')
except:
    print('Seeding database from parquet files...')
    from poc.db.seed import seed_crash_records, seed_cell_stats
    seed_crash_records()
    seed_cell_stats()
"

echo "Starting gunicorn..."
exec gunicorn \
    --bind 0.0.0.0:5001 \
    --workers 1 \
    --threads 4 \
    --timeout 120 \
    poc.app:app
