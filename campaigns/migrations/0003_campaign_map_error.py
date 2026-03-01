from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0002_alter_street_unique_together_street_block_index_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='map_error',
            field=models.TextField(blank=True, default=''),
        ),
    ]
