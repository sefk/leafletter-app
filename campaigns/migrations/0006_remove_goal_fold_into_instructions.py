from django.db import migrations, models


def fold_goal_into_instructions(apps, schema_editor):
    Campaign = apps.get_model('campaigns', 'Campaign')
    for campaign in Campaign.objects.all():
        if campaign.goal:
            goal_html = f'<p><strong>Goal:</strong> {campaign.goal}</p>'
            if campaign.instructions:
                campaign.instructions = goal_html + campaign.instructions
            else:
                campaign.instructions = goal_html
            campaign.save()


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0005_merge_materials_into_instructions'),
    ]

    operations = [
        migrations.RunPython(fold_goal_into_instructions, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='campaign',
            name='goal',
        ),
    ]
