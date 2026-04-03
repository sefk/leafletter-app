# Adds a unique index on auth_user.email so that email-based login (issue #125)
# cannot be ambiguous.  We use RunPython with vendor detection rather than a
# custom User model to keep the change minimal.
#
# Django's built-in User model stores email as VARCHAR with blank=True (default '').
# Multiple users can have no email set (email=''), which would violate a simple
# UNIQUE constraint.  We handle this differently per database:
#
#   MySQL / MariaDB  — no partial index support, so we skip indexing empty strings
#                      by leaving them as-is and only preventing *non-empty* duplicate
#                      emails via application logic in backends.py.  However we DO
#                      create the index because on Railway/MySQL any user without an
#                      email should be NULL, and MySQL unique indexes allow multiple NULLs.
#                      If empty-string emails exist, the migration converts them to NULL
#                      first so the unique index can be created cleanly.
#
#   SQLite           — partial unique index (WHERE email <> '') used in tests.
#
#   PostgreSQL       — partial unique index with LOWER() for case-insensitivity.

from django.db import migrations


def create_index(apps, schema_editor):
    vendor = schema_editor.connection.vendor

    if vendor == 'sqlite':
        schema_editor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS leafletter_unique_user_email
            ON auth_user (email)
            WHERE email <> ''
        """)

    elif vendor in ('mysql', 'mariadb'):
        # Convert any existing empty-string emails to NULL so the unique index
        # can be created without conflicts.  Users with no email should be NULL.
        schema_editor.execute("""
            UPDATE auth_user SET email = NULL WHERE email = ''
        """)
        schema_editor.execute("""
            CREATE UNIQUE INDEX leafletter_unique_user_email
            ON auth_user (email)
        """)

    else:
        # PostgreSQL: partial, case-insensitive
        schema_editor.execute("""
            CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS leafletter_unique_user_email
            ON auth_user (LOWER(email))
            WHERE email <> ''
        """)


def drop_index(apps, schema_editor):
    vendor = schema_editor.connection.vendor

    if vendor == 'sqlite':
        schema_editor.execute("DROP INDEX IF EXISTS leafletter_unique_user_email")

    elif vendor in ('mysql', 'mariadb'):
        schema_editor.execute("DROP INDEX leafletter_unique_user_email ON auth_user")

    else:
        # PostgreSQL
        schema_editor.execute("DROP INDEX IF EXISTS leafletter_unique_user_email")


class Migration(migrations.Migration):

    # atomic=False is required for PostgreSQL's CREATE INDEX CONCURRENTLY.
    # MySQL and SQLite are unaffected by this setting.
    atomic = False

    dependencies = [
        ('campaigns', '0009_add_cached_size_counts'),
    ]

    operations = [
        migrations.RunPython(create_index, reverse_code=drop_index),
    ]
