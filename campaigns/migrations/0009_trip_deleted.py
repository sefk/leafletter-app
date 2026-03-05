from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0008_cityfetchjob_street_city_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='trip',
            name='deleted',
            field=models.BooleanField(default=False),
        ),
    ]
