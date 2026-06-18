from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0198_add_device_fingerprint_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='device_security_exempt',
            field=models.BooleanField(
                default=False,
                help_text='If enabled, this user can login from any device without restriction.',
                verbose_name='exempt from device security',
            ),
        ),
    ]
