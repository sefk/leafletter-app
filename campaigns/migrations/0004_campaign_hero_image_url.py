from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0003_add_rendering_map_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='hero_image_url',
            field=models.URLField(blank=True, default=''),
        ),
    ]
