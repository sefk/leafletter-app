from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0007_add_addr_range_to_street'),
    ]

    operations = [
        migrations.AddField(
            model_name='street',
            name='city_index',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='CityFetchJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('city_index', models.IntegerField()),
                ('city_name', models.CharField(max_length=200)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('generating', 'Generating'), ('ready', 'Ready'), ('error', 'Error')],
                    default='pending',
                    max_length=20,
                )),
                ('error', models.TextField(blank=True, default='')),
                ('celery_task_id', models.CharField(blank=True, default='', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('campaign', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='city_fetch_jobs',
                    to='campaigns.campaign',
                )),
            ],
            options={
                'ordering': ['city_index'],
                'unique_together': {('campaign', 'city_index')},
            },
        ),
    ]
