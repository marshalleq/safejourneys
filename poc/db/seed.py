"""
Seed the database from existing parquet files.

Run once to populate the database with the initial 910K crash records.
Usage: python -m poc.db.seed
"""
import os
import sys
import time

import pandas as pd

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
            print(f"crash_records already has {count:,} rows. Skipping seed.")
            return
    except Exception:
        pass  # Table might not exist yet in the right shape

    parquet_path = os.path.join(ROOT_DIR, "cas_features.parquet")
    if not os.path.exists(parquet_path):
        print(f"ERROR: {parquet_path} not found. Cannot seed.")
        sys.exit(1)

    print(f"Loading {parquet_path}...")
    df = pd.read_parquet(parquet_path)
    print(f"  {len(df):,} records, {len(df.columns)} columns")

    # Convert category dtype to string (PostgreSQL doesn't support pandas categoricals)
    for col in df.columns:
        if df[col].dtype.name == "category":
            df[col] = df[col].astype(str)

    print("Writing to database (this may take a few minutes)...")
    start = time.time()

    # Drop and recreate to get the right schema from the DataFrame
    df.to_sql(
        "crash_records",
        engine,
        if_exists="replace",
        index=False,
        method="multi",
        chunksize=5000,
    )

    elapsed = time.time() - start
    print(f"  Inserted {len(df):,} records in {elapsed:.0f}s")

    # Create indexes
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_h3 ON crash_records ("h3_index")'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_h3r7 ON crash_records ("h3_r7")'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_year ON crash_records ("crashYear")'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS idx_cr_oid ON crash_records ("OBJECTID")'))
    print("  Indexes created.")


def seed_cell_stats():
    """Load cas_cell_stats.parquet into cell_stats table."""
    engine = get_engine()

    parquet_path = os.path.join(ROOT_DIR, "cas_cell_stats.parquet")
    if not os.path.exists(parquet_path):
        print(f"WARNING: {parquet_path} not found. Cell stats will be computed at startup.")
        return

    print(f"Loading {parquet_path}...")
    df = pd.read_parquet(parquet_path)
    print(f"  {len(df):,} cell stats")

    df.to_sql("cell_stats", engine, if_exists="replace", index=False, method="multi", chunksize=2000)
    print(f"  Cell stats loaded.")


if __name__ == "__main__":
    seed_crash_records()
    seed_cell_stats()
    print("Seed complete.")
