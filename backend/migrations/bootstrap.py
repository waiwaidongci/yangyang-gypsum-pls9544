from sqlalchemy import inspect, text

from src.config.database import Base, engine
from src.models import account, audit, correction, dataset, review, run  # noqa: F401

# Columns added after the initial snapshot; create_all does not alter tables.
_ADDED_COLUMNS = {
    'datasets': {'version': "INTEGER NOT NULL DEFAULT 1"},
    'analysis_runs': {
        'version': "INTEGER NOT NULL DEFAULT 1",
        'fold_stats': 'JSON',
    },
}


def _add_missing_columns():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {column['name'] for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))


def bootstrap():
    Base.metadata.create_all(engine)
    _add_missing_columns()
