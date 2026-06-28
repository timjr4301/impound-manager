"""
Run once (safe to re-run) — adds Towbook sync columns to the vehicles table.
Skips any column that already exists.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db
from sqlalchemy import text, inspect

NEW_COLS = [
    ('stock_number',    'VARCHAR(50)'),
    ('call_number',     'VARCHAR(50)'),
    ('invoice_number',  'VARCHAR(50)'),
    ('account',         'VARCHAR(100)'),
    ('model',           'VARCHAR(50)'),
    ('impound_reason',  'VARCHAR(200)'),
    ('have_keys',       'BOOLEAN'),
    ('tasks_overdue',   'INTEGER DEFAULT 0'),
    ('tasks_due_today', 'INTEGER DEFAULT 0'),
    ('tasks_due_next',  'INTEGER DEFAULT 0'),
    ('tasks_due_soon',  'INTEGER DEFAULT 0'),
    ('balance_due',     'FLOAT'),
    ('last_synced',     'DATETIME'),
    # already exist — listed here so they're skipped gracefully
    ('color',           'VARCHAR(30)'),
    ('make',            'VARCHAR(50)'),
    ('year',            'INTEGER'),
    ('plate_state',     'VARCHAR(2)'),
]


def run():
    app = create_app()
    with app.app_context():
        inspector = inspect(db.engine)
        existing = {c['name'] for c in inspector.get_columns('vehicles')}
        added, skipped = [], []
        with db.engine.begin() as conn:
            for col_name, col_type in NEW_COLS:
                if col_name in existing:
                    skipped.append(col_name)
                else:
                    conn.execute(text(f'ALTER TABLE vehicles ADD COLUMN {col_name} {col_type}'))
                    added.append(col_name)
        print(f'Added   ({len(added)}):   {", ".join(added) or "none"}')
        print(f'Skipped ({len(skipped)}): {", ".join(skipped) or "none"}')
        print('Done.')


if __name__ == '__main__':
    run()
