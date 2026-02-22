#!/usr/bin/env python
"""Seed example investment plans: Silver and Gold.
Run: python scripts/seed_plans.py
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
plans = [
    ('Silver', 200.0, 30.0, 230.0, 60, 1, 'active'),
    ('Gold', 500.0, 75.0, 575.0, 90, 1, 'active')
]
for p in plans:
    cur.execute('INSERT OR IGNORE INTO investment_plans (plan_name, minimum_amount, profit_amount, total_return, duration_days, capital_back, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (p[0], p[1], p[2], p[3], p[4], p[5], p[6], "datetime('now')", "datetime('now')"))
conn.commit()
conn.close()
print('Seeded plans (if missing)')
