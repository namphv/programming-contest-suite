# Generated by Django 3.2.16 on 2023-02-19 20:00

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0187_submission_index_refactor'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='submission',
            index=models.Index(fields=['result', 'problem'], name='judge_submi_result_a77e42_idx'),
        ),
        migrations.AddIndex(
            model_name='submission',
            index=models.Index(fields=['language', 'problem', 'result'], name='judge_submi_languag_380ab4_idx'),
        ),
        migrations.AddIndex(
            model_name='submission',
            index=models.Index(fields=['problem', 'result'], name='judge_submi_problem_49f8ec_idx'),
        ),
        migrations.AddIndex(
            model_name='submission',
            index=models.Index(fields=['user', 'problem', 'result'], name='judge_submi_user_id_650db3_idx'),
        ),
        migrations.AddIndex(
            model_name='submission',
            index=models.Index(fields=['user', 'result'], name='judge_submi_user_id_ec9a4b_idx'),
        ),
    ]
