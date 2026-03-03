from django.db import migrations, models


def combine_fields(apps, schema_editor):
    Campaign = apps.get_model('campaigns', 'Campaign')
    for campaign in Campaign.objects.all():
        if campaign.materials_url:
            link_html = f'<p><a href="{campaign.materials_url}" target="_blank">Campaign Materials</a></p>'
            if campaign.instructions:
                campaign.instructions = f'<p>{campaign.instructions}</p>{link_html}'
            else:
                campaign.instructions = link_html
            campaign.save()


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0004_campaign_bbox'),
    ]

    operations = [
        migrations.RunPython(combine_fields, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='campaign',
            name='materials_url',
        ),
    ]
