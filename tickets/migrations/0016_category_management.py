from django.db import migrations, models

_INITIAL_CATEGORIES = [
    ("M365",       "Microsoft 365",              0),
    ("OUTLOOK",    "Outlook / Correo",            1),
    ("REDES",      "Redes",                       2),
    ("EQUIPOS",    "Equipos / Hardware",          3),
    ("SEGURIDAD",  "Seguridad",                   4),
    ("VPN",        "VPN / Acceso Remoto",         5),
    ("SHAREPOINT", "SharePoint",                  6),
    ("AZURE",      "Azure",                       7),
    ("APPS",       "Aplicaciones Corporativas",   8),
    ("OTRO",       "Otro",                        9),
]


def seed_categories(apps, schema_editor):
    TicketCategory = apps.get_model("tickets", "TicketCategory")
    for code, name, order in _INITIAL_CATEGORIES:
        TicketCategory.objects.get_or_create(
            code=code,
            defaults={"name": name, "order": order, "is_active": True, "description": ""},
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0015_phase7_audit_actions"),
    ]

    operations = [
        migrations.CreateModel(
            name="TicketCategory",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code",        models.SlugField(max_length=20, unique=True, verbose_name="Código")),
                ("name",        models.CharField(max_length=100, verbose_name="Nombre")),
                ("description", models.TextField(blank=True, default="", verbose_name="Descripción")),
                ("is_active",   models.BooleanField(default=True, verbose_name="Activa")),
                ("order",       models.PositiveSmallIntegerField(default=0, verbose_name="Orden")),
            ],
            options={
                "verbose_name":        "Categoría",
                "verbose_name_plural": "Categorías",
                "ordering":            ["order", "name"],
            },
        ),
        migrations.RunPython(seed_categories, noop),
        migrations.AlterField(
            model_name="ticket",
            name="category",
            field=models.CharField(default="OTRO", max_length=20),
        ),
        migrations.AlterField(
            model_name="ticketroutingrule",
            name="category",
            field=models.CharField(max_length=20),
        ),
        migrations.AlterField(
            model_name="systemauditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("LOGIN",             "Inicio de sesión"),
                    ("LOGOUT",            "Cierre de sesión"),
                    ("LOGIN_FAILED",      "Intento de login fallido"),
                    ("USER_CREATED",      "Usuario creado"),
                    ("USER_UPDATED",      "Usuario actualizado"),
                    ("USER_DEACTIVATED",  "Usuario desactivada"),
                    ("USER_ACTIVATED",    "Usuario activado"),
                    ("PASSWORD_CHANGED",  "Contraseña cambiada"),
                    ("NOTIFICATION_SENT", "Notificación enviada"),
                ],
                max_length=20,
            ),
        ),
    ]
