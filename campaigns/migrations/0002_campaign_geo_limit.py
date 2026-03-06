import django.contrib.gis.db.models.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0001_beta'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='geo_limit',
            field=django.contrib.gis.db.models.fields.PolygonField(blank=True, null=True, srid=4326),
        ),
    ]
