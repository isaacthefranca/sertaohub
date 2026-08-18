"""Migra dados do barbersaas.db para um PostgreSQL vazio.
Uso:
  set DATABASE_URL=postgresql://...
  python migrate_sqlite_to_postgres.py
Faça backup antes. Execute somente uma vez em um banco novo.
"""
import os, sqlite3
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

BASE=Path(__file__).resolve().parent
SQLITE=BASE/'barbersaas.db'
URL=os.getenv('DATABASE_URL','').strip()
if not URL.startswith(('postgresql://','postgres://')):
    raise SystemExit('Defina DATABASE_URL apontando para PostgreSQL.')
if URL.startswith('postgres://'):
    URL='postgresql://'+URL[len('postgres://'):]

TABLES=[
 'tenants','professionals','services','customers','appointments','audit_logs','saas_plans','subscriptions',
 'products','inventory_movements','cash_registers','cash_transactions','sales','sale_items','expenses',
 'commission_entries','membership_plans','customer_memberships','business_hours','payment_orders','webhook_events',
 'tenant_settings','users'
]

src=sqlite3.connect(SQLITE); src.row_factory=sqlite3.Row
dst=psycopg.connect(URL,row_factory=dict_row)
schema=(BASE/'schema_postgres.sql').read_text(encoding='utf-8')
with dst.cursor() as cur:
    for stmt in schema.split(';'):
        if stmt.strip(): cur.execute(stmt)
dst.commit()

for table in TABLES:
    rows=src.execute(f'SELECT * FROM {table}').fetchall()
    if not rows:
        print(table, 0); continue
    cols=rows[0].keys()
    placeholders=','.join(['%s']*len(cols))
    col_sql=','.join(cols)
    with dst.cursor() as cur:
        for row in rows:
            cur.execute(f'INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING', tuple(row[c] for c in cols))
    dst.commit(); print(table, len(rows))

# Reset serial sequences for tables with numeric id columns.
with dst.cursor() as cur:
    for table in TABLES:
        try:
            cur.execute("SELECT pg_get_serial_sequence(%s, 'id') AS seq", (table,))
            seq=cur.fetchone()['seq']
            if seq:
                cur.execute(f'SELECT COALESCE(MAX(id),1) AS m FROM {table}')
                m=cur.fetchone()['m']
                cur.execute('SELECT setval(%s,%s,true)',(seq,m))
        except Exception:
            dst.rollback()
        else:
            dst.commit()
src.close(); dst.close()
print('Migração concluída.')
