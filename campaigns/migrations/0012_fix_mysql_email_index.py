"""
On MySQL, the leafletter_unique_user_email index should not exist — migration
0010 intentionally skipped it because MySQL can't do partial unique indexes and
auth_user.email is NOT NULL (default ''), so multiple no-email users would
collide.  If the index was created anyway (e.g. via a prior migration version
or manual setup), drop it so that creating users without an email works again.
"""
from django.db import migrations


def drop_mysql_email_index(apps, schema_editor):
    if schema_editor.connection.vendor not in ('mysql', 'mariadb'):
        return
    db_name = schema_editor.connection.settings_dict['NAME']
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.statistics
            WHERE table_schema = %s
              AND table_name = 'auth_user'
              AND index_name = 'leafletter_unique_user_email'
        """, [db_name])
        if cursor.fetchone()[0]:
            cursor.execute("ALTER TABLE auth_user DROP INDEX leafletter_unique_user_email")


class Migration(migrations.Migration):

    atomic = False  # DDL inside RunPython requires non-atomic on SQLite

    dependencies = [
        ('campaigns', '0011_campaign_owner'),
    ]

    operations = [
        migrations.RunPython(drop_mysql_email_index, reverse_code=migrations.RunPython.noop),
    ]
