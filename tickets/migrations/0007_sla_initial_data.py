from django.db import migrations


SLA_DEFAULTS = [
    # (priority_key, resolution_hours)
    ("CRITICAL", 4),    # Incidente crítico → 4 horas
    ("HIGH",     8),    # Alta prioridad   → 8 horas (1 día laboral)
    ("MEDIUM",   24),   # Media prioridad  → 24 horas
    ("LOW",      72),   # Baja prioridad   → 72 horas (3 días)
]


def create_sla_policies(apps, schema_editor):
    SLAPolicy = apps.get_model("tickets", "SLAPolicy")
    for priority, hours in SLA_DEFAULTS:
        SLAPolicy.objects.get_or_create(
            priority=priority,
            defaults={"resolution_hours": hours},
        )


def remove_sla_policies(apps, schema_editor):
    SLAPolicy = apps.get_model("tickets", "SLAPolicy")
    SLAPolicy.objects.filter(priority__in=[p for p, _ in SLA_DEFAULTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0006_phase2_priority_category_sla_worksession"),
    ]

    operations = [
        migrations.RunPython(create_sla_policies, reverse_code=remove_sla_policies),
    ]
