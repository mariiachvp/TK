"""
Migración 0011: Elimina el CharField 'role' antiguo de UserProfile y
renombra 'role_fk' → 'role'. Después de esta migración, UserProfile.role
es un ForeignKey al modelo Role dinámico.
"""

from django.db import migrations
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0010_dynamic_roles"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="userprofile",
            name="role",
        ),
        migrations.RenameField(
            model_name="userprofile",
            old_name="role_fk",
            new_name="role",
        ),
    ]
