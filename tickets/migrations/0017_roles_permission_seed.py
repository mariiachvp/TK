from django.db import migrations


def seed_roles_permission(apps, schema_editor):
    RolePermission = apps.get_model("tickets", "RolePermission")
    RolePermission.objects.get_or_create(
        codename="roles.gestionar",
        defaults={
            "module":      "roles",
            "action":      "gestionar",
            "label":       "Gestionar roles",
            "description": "Permite crear, editar, eliminar y activar/desactivar roles del sistema.",
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0016_category_management"),
    ]

    operations = [
        migrations.RunPython(seed_roles_permission, noop),
    ]
