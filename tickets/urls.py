from django.urls import path

from . import views

urlpatterns = [
    # --- Usuarios OMA / OPERADOR ---
    path("",                              views.home,               name="home"),
    path("crear/",                        views.create_ticket,      name="create_ticket"),
    path("mis-tickets/",                  views.my_tickets,         name="my_tickets"),
    path("mis-tickets/<int:pk>/",         views.user_ticket_detail, name="user_ticket_detail"),
    path("mis-tickets/<int:pk>/reabrir/", views.reopen_ticket,      name="reopen_ticket"),
    path("mis-tickets/<int:pk>/comentar/",views.add_user_comment,   name="add_user_comment"),
    path("ticket-enviado/<int:pk>/",      views.ticket_success,     name="ticket_success"),

    # --- Adjuntos ---
    path("ticket-adjunto/subir/<int:pk>/",               views.upload_attachment,   name="upload_attachment"),
    path("ticket-adjunto/<int:attachment_pk>/eliminar/", views.delete_attachment,   name="delete_attachment"),
    path("ticket-adjunto/<int:attachment_pk>/descargar/",views.download_attachment, name="download_attachment"),

    # --- Equipo IT ---
    path("it/tickets/",              views.ticket_list,           name="ticket_list"),
    path("it/tickets/exportar/",     views.export_tickets_csv,    name="export_tickets_csv"),
    path("it/tickets/bulk/",         views.bulk_action_tickets,   name="bulk_action_tickets"),
    path("it/respond/<int:pk>/",     views.respond_ticket,        name="respond_ticket"),
    path("it/dashboard/",            views.technician_dashboard,  name="technician_dashboard"),
    path("it/estadisticas/",         views.it_stats,              name="it_stats"),
    path("it/ejecutivo/",            views.executive_dashboard,   name="executive_dashboard"),

    # --- Gestión de usuarios ---
    path("it/usuarios/",                             views.user_list,            name="user_list"),
    path("it/usuarios/crear/",                       views.user_create,          name="user_create"),
    path("it/usuarios/<int:pk>/editar/",             views.user_edit,            name="user_edit"),
    path("it/usuarios/<int:pk>/toggle/",             views.user_toggle_active,   name="user_toggle_active"),
    path("it/usuarios/<int:pk>/cambiar-clave/",      views.user_change_password, name="user_change_password"),

    # --- Gestión de roles ---
    path("it/roles/",                views.role_list,    name="role_list"),
    path("it/roles/crear/",          views.role_create,  name="role_create"),
    path("it/roles/<int:pk>/editar/",  views.role_edit,  name="role_edit"),
    path("it/roles/<int:pk>/eliminar/", views.role_delete, name="role_delete"),
    path("it/roles/<int:pk>/toggle/",  views.role_toggle, name="role_toggle"),

    # --- Auditoría global ---
    path("it/auditoria/", views.audit_log, name="audit_log"),

    # --- Reglas de enrutamiento automático ---
    path("it/reglas/",                     views.routing_rule_list,   name="routing_rule_list"),
    path("it/reglas/crear/",               views.routing_rule_create, name="routing_rule_create"),
    path("it/reglas/<int:pk>/editar/",     views.routing_rule_edit,   name="routing_rule_edit"),
    path("it/reglas/<int:pk>/toggle/",     views.routing_rule_toggle, name="routing_rule_toggle"),
    path("it/reglas/<int:pk>/eliminar/",   views.routing_rule_delete, name="routing_rule_delete"),

    # --- Categorías de ticket ---
    path("it/categorias/",                   views.category_list,   name="category_list"),
    path("it/categorias/crear/",             views.category_create, name="category_create"),
    path("it/categorias/<int:pk>/editar/",   views.category_edit,   name="category_edit"),
    path("it/categorias/<int:pk>/toggle/",   views.category_toggle, name="category_toggle"),
]
