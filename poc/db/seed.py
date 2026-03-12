"""
Seed the database from existing parquet files.

Run once to populate the database with the initial 910K crash records.
Usage: python -m poc.db.seed
"""
import os
import sys
import time

import pandas as pd

# Force unbuffered output so Docker logs show progress
os.environ["PYTHONUNBUFFERED"] = "1"

# Add project root to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from poc.db.connection import get_engine, init_db, table_row_count


def seed_crash_records():
    """Load cas_features.parquet into crash_records table."""
    engine = get_engine()
    init_db()

    # Check if already seeded
    try:
        count = table_row_count("crash_records")
        if count > 0:
            print(f"crash_records already has {count:,} rows. Skipping seed.", flush=True)
            return
    except Exception:
        pass  # Table might not exist yet in the right shape

    parquet_path = os.path.join(ROOT_DIR, "cas_features.parquet")
    if not os.path.exists(parquet_path):
        print(f"ERROR: {parquet_path} not found. Cannot seed.", flush=True)
        sys.exit(1)

    print(f"Loading {parquet_path}...", flush=True)
    df = pd.read_parquet(parquet_path)
    print(f"  {len(df):,} records, {len(df.columns)} columns", flush=True)

    # Convert category dtype to string (PostgreSQL doesn't support pandas categoricals)
    for col in df.columns:
        if df[col].dtype.name == "category":
            df[col] = df[col].astype(str)

    print("Writing to database (this may take a few minutes)...", flush=True)
    start = time.time()

    # Insert in chunks with progress logging
    # Use default method (not "multi") to avoid generating huge SQL statements
    chunk_size = 10000
    total = len(df)
    for i in range(0, total, chunk_size):
        chunk = df.iloc[i:i + chunk_size]
        mode = "replace" if i == 0 else "append"
        chunk.to_sql(
            "crash_records",
            engine,
            if_exists=mode,
            index=False,
            chunksize=1000,
        )
        elapsed = time.time() - start
        done = min(i + chunk_size, total)
        pct = done / total * 100
        print(f"  {done:,}/{total:,} ({pct:.0f}%) — {elapsed:.0f}s", flush=True)

    elapsed = time.time() - start
    print(f"  Inserted {total:,} records in {elapsed:.0f}s", flush=True)

    # Create indexes
    print("Creating indexes...", flush=True)
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_h3 ON crash_records ("h3_index")'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_h3r7 ON crash_records ("h3_r7")'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_year ON crash_records ("crashYear")'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_oid ON crash_records ("OBJECTID")'))
    print("  Indexes created.", flush=True)


def seed_cell_stats():
    """Load cas_cell_stats.parquet into cell_stats table."""
    engine = get_engine()

    parquet_path = os.path.join(ROOT_DIR, "cas_cell_stats.parquet")
    if not os.path.exists(parquet_path):
        print(f"WARNING: {parquet_path} not found. Cell stats will be computed at startup.", flush=True)
        return

    print(f"Loading {parquet_path}...", flush=True)
    df = pd.read_parquet(parquet_path)
    print(f"  {len(df):,} cell stats", flush=True)

    df.to_sql("cell_stats", engine, if_exists="replace", index=False, chunksize=1000)
    print(f"  Cell stats loaded.", flush=True)


if __name__ == "__main__":
    seed_crash_records()
    seed_cell_stats()
    print("Seed complete.", flush=True)
