from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import tickets.models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0013_phase2_ticket_core"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("CREATED",            "Creado"),
                    ("STATUS_CHANGE",      "Cambio de estado"),
                    ("RESPONDED",          "Respuesta registrada"),
                    ("FIELD_CHANGE",       "Campo modificado"),
                    ("ESCALATED",          "Escalado automáticamente"),
                    ("REOPENED",           "Reabierto por usuario"),
                    ("COMMENT_ADDED",      "Comentario agregado"),
                    ("ATTACHMENT_ADDED",   "Adjunto agregado"),
                    ("ATTACHMENT_DELETED", "Adjunto eliminado"),
                ],
                max_length=30,
            ),
        ),
        migrations.CreateModel(
            name="TicketAttachment",
            fields=[
                ("id",            models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file",          models.FileField(upload_to=tickets.models._attachment_upload_path)),
                ("original_name", models.CharField(max_length=255)),
                ("file_size",     models.PositiveIntegerField()),
                ("content_type",  models.CharField(blank=True, max_length=100)),
                ("created_at",    models.DateTimeField(auto_now_add=True)),
                ("ticket",        models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="tickets.ticket")),
                ("uploaded_by",   models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploaded_attachments", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name":        "Adjunto",
                "verbose_name_plural": "Adjuntos",
                "ordering":            ["created_at"],
            },
        ),
    ]
