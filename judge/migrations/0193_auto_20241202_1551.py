# Generated by Django 3.2.25 on 2024-12-02 15:51

from django.db import migrations, models
import judge.models.problem


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0192_add_registration_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='organization_points',
            field=models.JSONField(default=dict, help_text='pre-calculate total points on each organization'),
        )
    ]
