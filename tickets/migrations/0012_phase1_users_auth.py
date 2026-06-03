"""
Migración 0012 — Fase 1: Usuarios y Autenticación.

1. Agrega campo `department` a UserProfile.
2. Crea modelo SystemAuditLog (login, logout, gestión de usuarios).
3. Siembra permisos faltantes para gestión de usuarios.
4. Asigna `usuarios.gestionar` al rol IT.
"""

from django.db import migrations, models
import django.db.models.deletion


_NEW_PERMISSIONS = [
    # (module, action, codename, label)
    ("usuarios", "gestionar", "usuarios.gestionar", "Gestionar usuarios"),
    ("usuarios", "crear",     "usuarios.crear",     "Crear usuarios"),
    ("usuarios", "editar",    "usuarios.editar",    "Editar usuarios"),
]


def seed_user_permissions(apps, schema_editor):
    RolePermission = apps.get_model("tickets", "RolePermission")
    Role           = apps.get_model("tickets", "Role")

    new_perms = []
    for module, action, codename, label in _NEW_PERMISSIONS:
        perm, _ = RolePermission.objects.get_or_create(
            module=module,
            action=action,
            defaults={"codename": codename, "label": label},
        )
        new_perms.append(perm)

    # El rol IT (is_it_staff=True, code="IT") recibe todos los permisos de usuario
    it_role = Role.objects.filter(code="IT").first()
    if it_role:
        for perm in new_perms:
            it_role.permissions.add(perm)


def reverse_seed(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0011_finalize_role_migration"),
    ]

    operations = [
        # 1. Campo department en UserProfile
        migrations.AddField(
            model_name="userprofile",
            name="department",
            field=models.CharField(
                blank=True, default="", max_length=100, verbose_name="Departamento"
            ),
        ),

        # 2. Modelo SystemAuditLog
        migrations.CreateModel(
            name="SystemAuditLog",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name="ID"
                )),
                ("action", models.CharField(
                    max_length=20,
                    choices=[
                        ("LOGIN",            "Inicio de sesión"),
                        ("LOGOUT",           "Cierre de sesión"),
                        ("LOGIN_FAILED",     "Intento de login fallido"),
                        ("USER_CREATED",     "Usuario creado"),
                        ("USER_UPDATED",     "Usuario actualizado"),
                        ("USER_DEACTIVATED", "Usuario desactivado"),
                        ("USER_ACTIVATED",   "Usuario activado"),
                        ("PASSWORD_CHANGED", "Contraseña cambiada"),
                    ],
                )),
                ("ip_address", models.GenericIPAddressField(
                    blank=True, null=True, verbose_name="IP"
                )),
                ("details", models.TextField(blank=True, default="", verbose_name="Detalles")),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="system_audit_actions",
                    to="auth.user",
                    verbose_name="Actor",
                )),
                ("target_user", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="system_audit_targets",
                    to="auth.user",
                    verbose_name="Usuario afectado",
                )),
            ],
            options={
                "verbose_name":        "Auditoría del sistema",
                "verbose_name_plural": "Auditorías del sistema",
                "ordering":            ["-timestamp"],
            },
        ),

        # 3. Siembra de permisos y asignación al rol IT
        migrations.RunPython(seed_user_permissions, reverse_seed),
    ]
