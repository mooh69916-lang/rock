#!/usr/bin/env python
"""Create testimonials table and seed sample testimonials.
Run: python scripts/create_testimonials_table.py
"""
import sqlite3
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'app.db')

if not os.path.exists(DB_PATH):
    print('Database not found at', DB_PATH)
    raise SystemExit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute('''
CREATE TABLE IF NOT EXISTS testimonials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT
)
''')
# seed
cur.execute("INSERT INTO testimonials (name, body, created_at) SELECT ?, ?, datetime('now') WHERE NOT EXISTS (SELECT 1 FROM testimonials WHERE name = ?)", ('John M.', 'Turned $200 into consistent weekly profits.', 'John M.'))
cur.execute("INSERT INTO testimonials (name, body, created_at) SELECT ?, ?, datetime('now') WHERE NOT EXISTS (SELECT 1 FROM testimonials WHERE name = ?)", ('Sarah K.', 'Recovered her starting capital in 3 weeks.', 'Sarah K.'))
cur.execute("INSERT INTO testimonials (name, body, created_at) SELECT ?, ?, datetime('now') WHERE NOT EXISTS (SELECT 1 FROM testimonials WHERE name = ?)", ('David A.', 'Upgraded from Starter to Gold within a month.', 'David A.'))
conn.commit()
conn.close()
print('testimonials table ensured and seeded')
