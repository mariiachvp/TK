import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forward_data(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    TicketCounter = apps.get_model("tickets", "TicketCounter")

    # Migrate legacy PENDIENTE status to ABIERTO
    Ticket.objects.filter(status="PENDIENTE").update(status="ABIERTO")

    # Backfill ticket_numbers grouped by year, ordered by creation date
    tickets_by_year = {}
    for ticket in Ticket.objects.order_by("created_at"):
        year = ticket.created_at.year
        tickets_by_year.setdefault(year, []).append(ticket)

    for year, year_tickets in sorted(tickets_by_year.items()):
        for i, ticket in enumerate(year_tickets, 1):
            ticket.ticket_number = f"TK-{year}-{i:06d}"
            ticket.save(update_fields=["ticket_number"])
        TicketCounter.objects.update_or_create(
            year=year,
            defaults={"counter": len(year_tickets)},
        )

    # Ensure current year counter exists even if no tickets yet
    from django.utils import timezone
    current_year = timezone.now().year
    TicketCounter.objects.get_or_create(year=current_year, defaults={"counter": 0})


def backward_data(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    TicketCounter = apps.get_model("tickets", "TicketCounter")

    Ticket.objects.filter(status="ABIERTO").update(status="PENDIENTE")
    Ticket.objects.filter(status="REABIERTO").update(status="PENDIENTE")
    Ticket.objects.filter(status="RESUELTO").update(status="EN_PROCESO")
    Ticket.objects.update(ticket_number="")
    TicketCounter.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0012_phase1_users_auth"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. TicketCounter table
        migrations.CreateModel(
            name="TicketCounter",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("year", models.PositiveIntegerField(unique=True)),
                ("counter", models.PositiveIntegerField(default=0)),
            ],
            options={"verbose_name": "Contador de tickets"},
        ),
        # 2. New Ticket fields
        migrations.AddField(
            model_name="ticket",
            name="ticket_number",
            field=models.CharField(blank=True, db_index=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="ticket",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="ticket",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ticket",
            name="deleted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="deleted_tickets",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 3. TicketNote — public comments
        migrations.AddField(
            model_name="ticketnote",
            name="is_public",
            field=models.BooleanField(default=False, verbose_name="Visible al solicitante"),
        ),
        # 4. Update Ticket.status choices and default
        migrations.AlterField(
            model_name="ticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("ABIERTO", "Abierto"),
                    ("EN_PROCESO", "En progreso"),
                    ("RESUELTO", "Resuelto"),
                    ("CERRADO", "Cerrado"),
                    ("REABIERTO", "Reabierto"),
                ],
                default="ABIERTO",
                max_length=20,
            ),
        ),
        # 5. Data migration: backfill + migrate legacy statuses
        migrations.RunPython(forward_data, backward_data),
    ]
