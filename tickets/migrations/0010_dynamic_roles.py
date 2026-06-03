"""
Migración 0010: Sistema de roles dinámicos.

Crea los modelos RolePermission y Role, agrega role_fk (FK nullable) a
UserProfile, y ejecuta la migración de datos: crea permisos y roles por
defecto, luego asigna el rol correcto a cada perfil existente según el
valor antiguo del campo role (CharField "IT"/"OMA"/"OPERADOR").

La migración 0011 finaliza quitando el CharField antiguo y renombrando
role_fk → role.
"""

from django.db import migrations, models
import django.db.models.deletion


# ---------------------------------------------------------------------------
# Datos por defecto
# ---------------------------------------------------------------------------

_DEFAULT_PERMISSIONS = [
    # (module, action, codename, label)
    ("tickets",  "ver",       "tickets.ver",        "Ver tickets"),
    ("tickets",  "crear",     "tickets.crear",      "Crear tickets"),
    ("tickets",  "editar",    "tickets.editar",     "Editar tickets"),
    ("tickets",  "eliminar",  "tickets.eliminar",   "Eliminar tickets"),
    ("tickets",  "responder", "tickets.responder",  "Responder tickets"),
    ("tickets",  "asignar",   "tickets.asignar",    "Asignar técnico"),
    ("tickets",  "exportar",  "tickets.exportar",   "Exportar a CSV"),
    ("reportes", "ver",       "reportes.ver",       "Ver estadísticas"),
    ("reportes", "gestionar", "reportes.gestionar", "Dashboard ejecutivo"),
    ("usuarios", "ver",       "usuarios.ver",       "Ver usuarios"),
    ("roles",    "ver",       "roles.ver",          "Ver roles"),
    ("roles",    "gestionar", "roles.gestionar",    "Gestionar roles"),
]

_DEFAULT_ROLES = [
    # (code, name, description, is_it_staff, ticket_area, permission_codenames)
    (
        "IT", "IT",
        "Personal del equipo de tecnología. Acceso completo al sistema.",
        True, "",
        [p[2] for p in _DEFAULT_PERMISSIONS],  # all permissions
    ),
    (
        "OMA", "OMA",
        "Personal del área OMA.",
        False, "OMA",
        ["tickets.ver", "tickets.crear"],
    ),
    (
        "OPERADOR", "Operador",
        "Personal operativo.",
        False, "OPERADOR",
        ["tickets.ver", "tickets.crear"],
    ),
]


def migrate_roles_forward(apps, schema_editor):
    UserProfile   = apps.get_model("tickets", "UserProfile")
    Role          = apps.get_model("tickets", "Role")
    RolePermission = apps.get_model("tickets", "RolePermission")

    # Crear permisos por defecto
    perms = {}
    for module, action, codename, label in _DEFAULT_PERMISSIONS:
        perm, _ = RolePermission.objects.get_or_create(
            codename=codename,
            defaults={"module": module, "action": action, "label": label},
        )
        perms[codename] = perm

    # Crear roles por defecto
    role_map = {}
    for code, name, description, is_it_staff, ticket_area, perm_codenames in _DEFAULT_ROLES:
        role, _ = Role.objects.get_or_create(
            code=code,
            defaults={
                "name":        name,
                "description": description,
                "is_active":   True,
                "is_it_staff": is_it_staff,
                "ticket_area": ticket_area,
            },
        )
        role.permissions.set([perms[c] for c in perm_codenames if c in perms])
        role_map[code] = role

    # Asignar rol dinámico a cada perfil según el valor antiguo del CharField
    for profile in UserProfile.objects.all():
        old_code = profile.role  # CharField: "IT", "OMA" o "OPERADOR"
        new_role = role_map.get(old_code)
        if new_role:
            profile.role_fk = new_role
            profile.save(update_fields=["role_fk"])


def migrate_roles_backward(apps, schema_editor):
    UserProfile = apps.get_model("tickets", "UserProfile")
    Role        = apps.get_model("tickets", "Role")

    code_map = {r.pk: r.code for r in Role.objects.filter(code__in=["IT", "OMA", "OPERADOR"])}

    for profile in UserProfile.objects.all():
        if profile.role_fk_id:
            profile.role = code_map.get(profile.role_fk_id, "OPERADOR")
            profile.save(update_fields=["role"])


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0009_phase5_routing_notes"),
    ]

    operations = [
        # 1. Crear RolePermission
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("module",      models.CharField(max_length=30, choices=[("tickets","Tickets"),("reportes","Reportes"),("usuarios","Usuarios"),("roles","Gestión de roles")])),
                ("action",      models.CharField(max_length=30, choices=[("ver","Ver"),("crear","Crear"),("editar","Editar"),("eliminar","Eliminar"),("responder","Responder tickets"),("asignar","Asignar técnico"),("exportar","Exportar datos"),("gestionar","Gestionar")])),
                ("codename",    models.SlugField(max_length=80, unique=True)),
                ("label",       models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
            ],
            options={
                "verbose_name":        "Permiso",
                "verbose_name_plural": "Permisos",
                "ordering":            ["module", "action"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="rolepermission",
            unique_together={("module", "action")},
        ),

        # 2. Crear Role
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id",          models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name",        models.CharField(max_length=80, unique=True, verbose_name="Nombre")),
                ("code",        models.SlugField(max_length=50, unique=True, verbose_name="Código", help_text="Identificador único. Solo letras, números y guiones. Ej: IT, OMA, SUPERVISOR.")),
                ("description", models.TextField(blank=True, default="", verbose_name="Descripción")),
                ("is_active",   models.BooleanField(default=True, verbose_name="Activo")),
                ("is_it_staff", models.BooleanField(default=False, verbose_name="Personal IT", help_text="Permite acceder al panel IT: responder tickets, dashboards, estadísticas.")),
                ("ticket_area", models.CharField(max_length=20, choices=[("OMA","OMA"),("OPERADOR","Operador"),("","N/A — Personal IT")], blank=True, default="", verbose_name="Área de tickets", help_text="Área para tickets creados por usuarios con este rol. Vacío para roles IT.")),
                ("created_at",  models.DateTimeField(auto_now_add=True)),
                ("updated_at",  models.DateTimeField(auto_now=True)),
                ("permissions", models.ManyToManyField(blank=True, related_name="roles", to="tickets.rolepermission", verbose_name="Permisos")),
            ],
            options={
                "verbose_name":        "Rol",
                "verbose_name_plural": "Roles",
                "ordering":            ["name"],
            },
        ),

        # 3. Agregar role_fk (nullable) a UserProfile
        migrations.AddField(
            model_name="userprofile",
            name="role_fk",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="users",
                to="tickets.role",
                verbose_name="Rol",
            ),
        ),

        # 4. Migración de datos
        migrations.RunPython(migrate_roles_forward, migrate_roles_backward),
    ]
