from django.db import migrations, models
import django.contrib.gis.db.models.fields
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0006_campaignimage'),
    ]

    operations = [
        migrations.CreateModel(
            name='AddressPoint',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_index', models.IntegerField(null=True, blank=True)),
                ('location', django.contrib.gis.db.models.fields.PointField(srid=4326)),
                ('campaign', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='address_points',
                    to='campaigns.campaign',
                )),
            ],
        ),
        migrations.AddIndex(
            model_name='addresspoint',
            index=models.Index(fields=['campaign', 'city_index'], name='campaigns_addresspoint_idx'),
        ),
    ]
