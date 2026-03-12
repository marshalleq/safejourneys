-- Safe Journeys — Database Schema
-- Column names match the pandas DataFrame / parquet file conventions (camelCase)
-- to avoid a mapping layer between DB and application code.

-- Main crash records table — stores fully-engineered features
-- Written by the ingestion pipeline, read by the Flask app at startup
CREATE TABLE IF NOT EXISTS crash_records (
    "OBJECTID" INTEGER PRIMARY KEY
);

-- Aggregated cell statistics (recomputed after each data refresh)
CREATE TABLE IF NOT EXISTS cell_stats (
    h3_index VARCHAR(20) PRIMARY KEY,
    crash_count INTEGER,
    fatal_count INTEGER,
    serious_count INTEGER,
    minor_count INTEGER,
    years_span SMALLINT,
    first_year SMALLINT,
    last_year SMALLINT,
    mean_speed_limit REAL,
    annual_crash_rate REAL,
    mean_severity REAL,
    max_severity SMALLINT,
    severity_score REAL,
    cell_lat DOUBLE PRECISION,
    cell_lng DOUBLE PRECISION,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Refresh log — tracks API data pulls
CREATE TABLE IF NOT EXISTS data_refresh_log (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    records_fetched INTEGER DEFAULT 0,
    records_new INTEGER DEFAULT 0,
    max_crash_year SMALLINT,
    status VARCHAR(20) DEFAULT 'running',
    error_message TEXT
);
