"""Test-only adversary: edit a captured snapshot, then recompute every documented pin.

The helper deliberately does not call the production reader, so a tampered
snapshot carries a self-consistent manifest (DB SHA/size, canonical rows SHA
and row counts) exactly as a careless or hostile producer could publish it.
"""
from contextlib import closing
import json
import sqlite3

from agent_ee.catalog import CATEGORIES, DERIVED_COLUMNS, category_columns
from agent_ee.storage import canonical, digest, identity, write_json


def decoded_rows(database):
    with closing(sqlite3.connect(database)) as connection:
        cells = connection.execute(
            'SELECT * FROM component_base ORDER BY CAST(substr(lcsc,2) AS INTEGER)').fetchall()
    rows = []
    for values in cells:
        row = dict(zip(DERIVED_COLUMNS, values))
        for key in ('attributes', 'normalized', 'provenance'):
            row[key] = json.loads(row[key])
        for key in ('is_basic', 'is_preferred'):
            row[key] = bool(row[key])
        rows.append(row)
    return rows


def set_derived(connection, lcsc, normalized):
    """Reinsert one row with rewritten normalized JSON in base and category tables.

    Delete/reinsert changes physical row placement, so even an identical value
    produces new database bytes and therefore a genuinely new pin.
    """
    text = canonical(normalized).decode()
    category, = connection.execute('SELECT category FROM component_base WHERE lcsc=?', (lcsc,)).fetchone()
    for table in ('component_base', category):
        row = dict(zip(DERIVED_COLUMNS + (() if table == 'component_base' else category_columns(table)),
                       connection.execute(f'SELECT * FROM {table} WHERE lcsc=?', (lcsc,)).fetchone()))
        row['normalized'] = text
        if table != 'component_base':
            row.update({column: normalized.get(column.rsplit('_', 1)[0], {}).get(column.rsplit('_', 1)[1])
                        for column in category_columns(table)})
        connection.execute(f'DELETE FROM {table} WHERE lcsc=?', (lcsc,))
        connection.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in row)})', tuple(row.values()))


def repin(snapshot, edit_database=None, edit_manifest=None):
    """Return the new manifest SHA-256 after applying edits and recomputing pins."""
    database = snapshot / 'catalog.sqlite3'
    if edit_database is not None:
        with closing(sqlite3.connect(database)) as connection:
            edit_database(connection)
            connection.commit()
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    rows = decoded_rows(database)
    manifest['database'].update(sha256=digest(database), size_bytes=database.stat().st_size)
    manifest['rows_sha256'] = identity(rows)
    manifest['row_counts'] = {'component_base': len(rows), **{
        category: sum(row['category'] == category for row in rows) for category in CATEGORIES}}
    if edit_manifest is not None:
        edit_manifest(manifest)
    write_json(snapshot / 'manifest.json', manifest)
    return digest(snapshot / 'manifest.json')
