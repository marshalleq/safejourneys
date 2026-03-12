"""Database connection management."""
import os

from sqlalchemy import create_engine, text

_engine = None

DEFAULT_URL = "postgresql://safejourneys:safejourneys@db:5432/safejourneys"


def get_engine():
    """Return a SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL", DEFAULT_URL)
        _engine = create_engine(url, pool_pre_ping=True, pool_size=5)
    return _engine


def init_db():
    """Create tables if they don't exist."""
    engine = get_engine()
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    with engine.begin() as conn:
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt:
                conn.execute(text(stmt))
    print("Database schema initialised.")


def table_row_count(table_name: str) -> int:
    """Return the number of rows in a table."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        return result.scalar()
