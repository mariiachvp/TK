from django.core.cache import cache
from django.http import HttpResponse


class LoginRateLimitMiddleware:
    """
    Bloquea IPs con demasiados intentos fallidos de login.

    El conteo de intentos lo realiza la señal `user_login_failed` en signals.py
    (via el signal oficial de Django), lo que elimina la dependencia frágil
    de inspeccionar el contenido de la respuesta HTTP.

    Este middleware solo lee el contador y decide si bloquear.
    """

    MAX_ATTEMPTS    = 10
    WINDOW_SECONDS  = 300   # 5 minutos

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path == "/accounts/login/":
            ip       = self._get_ip(request)
            attempts = cache.get(f"login_attempts_{ip}", 0)

            if attempts >= self.MAX_ATTEMPTS:
                return HttpResponse(
                    "Demasiados intentos de inicio de sesión. Intenta de nuevo en 5 minutos.",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                )

        return self.get_response(request)

    @staticmethod
    def _get_ip(request) -> str:
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")
