# Generated by Django 2.0.3 on 2018-05-01 22:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('borme', '0002_document_added_index'),
    ]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='slug',
            field=models.SlugField(max_length=260, primary_key=True, serialize=False),
        ),
        migrations.AlterField(
            model_name='person',
            name='slug',
            field=models.SlugField(max_length=200, primary_key=True, serialize=False),
        ),
    ]
