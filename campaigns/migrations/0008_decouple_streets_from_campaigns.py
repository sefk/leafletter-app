"""
Migration: Decouple streets from individual campaigns (issue #128).

Schema changes:
  1. Add Street.city_name (populated from CityFetchJob.city_name in data migration).
  2. Create CampaignStreet through table (populated from existing Street.campaign + city_index).
  3. Add Campaign.streets M2M (via CampaignStreet through table).
  4. Remove Street.campaign FK and Street.city_index field.
  5. Update Street.unique_together from (campaign, osm_id, block_index)
     to (city_name, osm_id, block_index).

Data migration (forward):
  - Set city_name on all streets via chunked SQL JOINs against CityFetchJob.
  - For streets with no matching CityFetchJob, fall back to Campaign.cities JSON.
  - Populate CampaignStreet via chunked bulk INSERT.
  - Deduplicate Street rows: the old model allowed the same physical segment to
    exist once per campaign; after decoupling we keep only the min-id row and
    reroute campaigns_campaignstreet + campaigns_trip_streets references.

Orphaned streets (with no campaign) are NOT deleted.

atomic = False: the data migration operates on 4M+ rows; we chunk work to
avoid hitting MySQL net_read_timeout.  The schema DDL steps are auto-committed
by MySQL regardless.
"""

from django.db import migrations, models
import django.db.models.deletion

CHUNK_SIZE = 100_000


def _set_session_timeouts(cursor, seconds=3600):
    """Bump MySQL session timeouts so long-running chunks don't drop the connection."""
    cursor.execute(f"SET SESSION net_read_timeout  = {seconds}")
    cursor.execute(f"SET SESSION net_write_timeout = {seconds}")
    cursor.execute(f"SET SESSION wait_timeout       = {seconds}")


def populate_city_name_and_campaignstreet(apps, schema_editor):
    """
    Forward data migration using chunked SQL for performance (4M+ streets).

    Step 1: Set city_name from CityFetchJob via chunked SQL JOIN.
    Step 2: Python fallback for streets whose campaign+city_index has no job.
    Step 3: Bulk-insert CampaignStreet rows in chunks.
    """
    db = schema_editor.connection

    with db.cursor() as cursor:
        _set_session_timeouts(cursor)

        # ── Step 1: find ID range ──────────────────────────────────────────────
        cursor.execute("SELECT MIN(id), MAX(id) FROM campaigns_street WHERE campaign_id IS NOT NULL")
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return  # no streets to migrate
        min_id, max_id = row

    # ── Step 1: chunked UPDATE city_name from CityFetchJob ────────────────────
    chunk_start = min_id
    while chunk_start <= max_id:
        chunk_end = chunk_start + CHUNK_SIZE - 1
        with db.cursor() as cursor:
            _set_session_timeouts(cursor)
            cursor.execute("""
                UPDATE campaigns_street s
                JOIN campaigns_cityfetchjob j
                  ON j.campaign_id = s.campaign_id
                 AND j.city_index  = s.city_index
                SET s.city_name = j.city_name
                WHERE s.id BETWEEN %s AND %s
                  AND s.campaign_id IS NOT NULL
            """, [chunk_start, chunk_end])
        chunk_start += CHUNK_SIZE

    # ── Step 2: Python fallback for unmatched streets ─────────────────────────
    # Streets where no CityFetchJob row exists (old data, expected to be rare).
    Street = apps.get_model('campaigns', 'Street')
    Campaign = apps.get_model('campaigns', 'Campaign')

    unmatched = list(
        Street.objects.filter(campaign__isnull=False, city_name='')
        .values_list('campaign_id', 'city_index')
        .distinct()
    )

    if unmatched:
        campaign_ids = {r[0] for r in unmatched}
        campaigns_map = {
            c.pk: c.cities
            for c in Campaign.objects.filter(pk__in=campaign_ids)
        }

        for campaign_id, city_index in unmatched:
            cities = campaigns_map.get(campaign_id, [])
            if city_index is not None and 0 <= city_index < len(cities):
                city = cities[city_index]
                city_name = city if isinstance(city, str) else city.get('name', str(city))
            else:
                city_name = f'unknown-campaign-{campaign_id}'

            Street.objects.filter(
                campaign_id=campaign_id, city_index=city_index, city_name=''
            ).update(city_name=city_name)

    # Any remaining streets with no campaign or still empty
    with db.cursor() as cursor:
        _set_session_timeouts(cursor)
        cursor.execute("""
            UPDATE campaigns_street
            SET city_name = CONCAT('unknown-campaign-', CAST(campaign_id AS CHAR))
            WHERE campaign_id IS NOT NULL AND (city_name = '' OR city_name IS NULL)
        """)
        cursor.execute("""
            UPDATE campaigns_street
            SET city_name = 'orphaned'
            WHERE campaign_id IS NULL AND (city_name = '' OR city_name IS NULL)
        """)

    # ── Step 3: chunked INSERT into CampaignStreet ────────────────────────────
    with db.cursor() as cursor:
        _set_session_timeouts(cursor)
        cursor.execute("SELECT MIN(id), MAX(id) FROM campaigns_street WHERE campaign_id IS NOT NULL")
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return
        min_id, max_id = row

    chunk_start = min_id
    while chunk_start <= max_id:
        chunk_end = chunk_start + CHUNK_SIZE - 1
        with db.cursor() as cursor:
            _set_session_timeouts(cursor)
            cursor.execute("""
                INSERT IGNORE INTO campaigns_campaignstreet (campaign_id, street_id, city_index)
                SELECT campaign_id, id, city_index
                FROM campaigns_street
                WHERE campaign_id IS NOT NULL
                  AND id BETWEEN %s AND %s
            """, [chunk_start, chunk_end])
        chunk_start += CHUNK_SIZE


def deduplicate_streets(apps, schema_editor):
    """
    Before adding the new (city_name, osm_id, block_index) unique constraint,
    collapse duplicate Street rows (same city serves multiple campaigns → same
    physical segment duplicated under the old FK model).

    Strategy:
      - canonical = MIN(id) per (city_name, osm_id, block_index) group
      - Reroute campaigns_campaignstreet and campaigns_trip_streets via
        UPDATE IGNORE (duplicates become no-ops) then DELETE leftovers.
      - Delete non-canonical Street rows in chunks of CHUNK_SIZE.
      - Orphaned streets are NOT deleted.
    """
    db = schema_editor.connection

    # Fetch all non-canonical IDs in one query (avoids temp table scope issues).
    with db.cursor() as cur:
        _set_session_timeouts(cur)
        cur.execute("""
            SELECT s.id
            FROM campaigns_street s
            JOIN (
              SELECT city_name, osm_id, block_index, MIN(id) AS canonical_id
              FROM campaigns_street
              GROUP BY city_name, osm_id, block_index
              HAVING COUNT(*) > 1
            ) dups
              ON dups.city_name   = s.city_name
             AND dups.osm_id      = s.osm_id
             AND dups.block_index = s.block_index
            WHERE s.id != dups.canonical_id
        """)
        non_canonical_ids = [row[0] for row in cur.fetchall()]

    if not non_canonical_ids:
        return

    # Re-route references using UPDATE IGNORE (unique constraints on both
    # tables mean a duplicate update is silently skipped; we delete leftovers).
    with db.cursor() as cur:
        _set_session_timeouts(cur)
        cur.execute("""
            UPDATE IGNORE campaigns_campaignstreet cs
            JOIN campaigns_street s ON s.id = cs.street_id
            JOIN (
              SELECT city_name, osm_id, block_index, MIN(id) AS canonical_id
              FROM campaigns_street
              GROUP BY city_name, osm_id, block_index
              HAVING COUNT(*) > 1
            ) dups
              ON dups.city_name   = s.city_name
             AND dups.osm_id      = s.osm_id
             AND dups.block_index = s.block_index
            SET cs.street_id = dups.canonical_id
            WHERE cs.street_id != dups.canonical_id
        """)

    with db.cursor() as cur:
        _set_session_timeouts(cur)
        placeholders = ','.join(['%s'] * len(non_canonical_ids))
        cur.execute(
            f"DELETE FROM campaigns_campaignstreet WHERE street_id IN ({placeholders})",
            non_canonical_ids,
        )

    with db.cursor() as cur:
        _set_session_timeouts(cur)
        cur.execute("""
            UPDATE IGNORE campaigns_trip_streets ts
            JOIN campaigns_street s ON s.id = ts.street_id
            JOIN (
              SELECT city_name, osm_id, block_index, MIN(id) AS canonical_id
              FROM campaigns_street
              GROUP BY city_name, osm_id, block_index
              HAVING COUNT(*) > 1
            ) dups
              ON dups.city_name   = s.city_name
             AND dups.osm_id      = s.osm_id
             AND dups.block_index = s.block_index
            SET ts.street_id = dups.canonical_id
            WHERE ts.street_id != dups.canonical_id
        """)

    with db.cursor() as cur:
        _set_session_timeouts(cur)
        placeholders = ','.join(['%s'] * len(non_canonical_ids))
        cur.execute(
            f"DELETE FROM campaigns_trip_streets WHERE street_id IN ({placeholders})",
            non_canonical_ids,
        )

    # Delete non-canonical Street rows in chunks.
    for i in range(0, len(non_canonical_ids), CHUNK_SIZE):
        batch = non_canonical_ids[i:i + CHUNK_SIZE]
        placeholders = ','.join(['%s'] * len(batch))
        with db.cursor() as cur:
            _set_session_timeouts(cur)
            cur.execute(
                f"DELETE FROM campaigns_street WHERE id IN ({placeholders})",
                batch,
            )


def reverse_populate(apps, schema_editor):
    """
    Reverse data migration: restore Street.campaign and Street.city_index
    from CampaignStreet rows.  Streets linked to multiple campaigns will be
    arbitrarily assigned to the first campaign found (acceptable for rollback).
    """
    db = schema_editor.connection
    with db.cursor() as cursor:
        _set_session_timeouts(cursor)
        cursor.execute("""
            UPDATE campaigns_street s
            JOIN campaigns_campaignstreet cs ON cs.street_id = s.id
            SET s.campaign_id = cs.campaign_id,
                s.city_index  = cs.city_index
            WHERE s.campaign_id IS NULL
        """)


class Migration(migrations.Migration):

    # Do NOT wrap in a single transaction: we operate on 4M+ rows and chunk
    # the work to avoid MySQL net_read_timeout (default 30 s).
    atomic = False

    dependencies = [
        ('campaigns', '0007_add_addresspoint'),
    ]

    operations = [
        # ── Step 1: add city_name to Street (blank default for data migration) ──
        migrations.AddField(
            model_name='street',
            name='city_name',
            field=models.CharField(max_length=200, blank=True, default='', db_index=True),
        ),

        # ── Step 2: create CampaignStreet through table ───────────────────────
        migrations.CreateModel(
            name='CampaignStreet',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('campaign', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='campaign_streets',
                    to='campaigns.campaign',
                )),
                ('street', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='campaign_streets',
                    to='campaigns.street',
                )),
                ('city_index', models.IntegerField(null=True, blank=True)),
            ],
            options={
                'unique_together': {('campaign', 'street')},
            },
        ),
        migrations.AddIndex(
            model_name='campaignstreet',
            index=models.Index(
                fields=['campaign', 'city_index'],
                name='campaigns_campaignstreet_idx',
            ),
        ),

        # ── Step 3: populate city_name and CampaignStreet rows ────────────────
        migrations.RunPython(
            populate_city_name_and_campaignstreet,
            reverse_code=reverse_populate,
        ),

        # ── Step 4: update city_name field definition (remove blank=True) ─────
        migrations.AlterField(
            model_name='street',
            name='city_name',
            field=models.CharField(max_length=200, db_index=True),
        ),

        # ── Step 5: add the Campaign.streets M2M (through CampaignStreet) ─────
        migrations.AddField(
            model_name='campaign',
            name='streets',
            field=models.ManyToManyField(
                related_name='campaigns',
                through='campaigns.CampaignStreet',
                to='campaigns.street',
            ),
        ),

        # ── Step 6: drop old unique_together and remove campaign FK + city_index
        migrations.AlterUniqueTogether(
            name='street',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='street',
            name='campaign',
        ),
        migrations.RemoveField(
            model_name='street',
            name='city_index',
        ),

        # ── Step 7: deduplicate streets before adding the new unique constraint ─
        # Under the old FK model the same physical street segment could exist
        # once per campaign.  After decoupling we merge them into one row.
        migrations.RunPython(
            deduplicate_streets,
            reverse_code=migrations.RunPython.noop,
        ),

        # ── Step 8: apply new unique_together ─────────────────────────────────
        migrations.AlterUniqueTogether(
            name='street',
            unique_together={('city_name', 'osm_id', 'block_index')},
        ),
    ]
