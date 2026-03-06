from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0010_remove_street_addr_from_remove_street_addr_to'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='streets_geojson',
            field=models.TextField(blank=True, default=''),
        ),
    ]
