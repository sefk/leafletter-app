from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0002_campaign_geo_limit'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='campaign',
            name='streets_geojson',
        ),
    ]
