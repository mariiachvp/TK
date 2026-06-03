from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0017_roles_permission_seed"),
    ]

    operations = [
        migrations.AlterField(
            model_name="systemauditlog",
            name="action",
            field=models.CharField(
                max_length=20,
                choices=[
                    ("LOGIN",             "Inicio de sesión"),
                    ("LOGOUT",            "Cierre de sesión"),
                    ("LOGIN_FAILED",      "Intento de login fallido"),
                    ("USER_CREATED",      "Usuario creado"),
                    ("USER_UPDATED",      "Usuario actualizado"),
                    ("USER_DEACTIVATED",  "Usuario desactivado"),
                    ("USER_ACTIVATED",    "Usuario activado"),
                    ("PASSWORD_CHANGED",  "Contraseña cambiada"),
                    ("NOTIFICATION_SENT", "Notificación enviada"),
                    ("ROLE_CREATED",      "Rol creado"),
                    ("ROLE_UPDATED",      "Rol actualizado"),
                    ("ROLE_DELETED",      "Rol eliminado"),
                    ("CATEGORY_CREATED",  "Categoría creada"),
                    ("CATEGORY_UPDATED",  "Categoría actualizada"),
                ],
            ),
        ),
    ]
