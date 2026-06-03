"""
Cliente de Microsoft Graph API — Client Credentials Flow (OAuth 2.0).

Permisos requeridos en Azure App Registration (Application, no Delegated):
  - Mail.Send

Flujo:
  1. POST /oauth2/v2.0/token → access_token (válido ~1h)
  2. POST /v1.0/users/{sender}/sendMail con Bearer token

El token se cachea en memoria con renovación automática.
"""

import logging
import threading
import time

import requests
from django.conf import settings

logger = logging.getLogger("tickets.graph")

# ---------------------------------------------------------------------------
# Token cache thread-safe
# ---------------------------------------------------------------------------

_token_lock  = threading.Lock()
_token_cache = {"value": None, "expires_at": 0.0}


class GraphAPIError(Exception):
    """Error al comunicarse con Microsoft Graph API."""


def _get_access_token() -> str:
    """
    Devuelve un access token válido.
    Renueva el token automáticamente si expira en menos de 60 segundos.
    Thread-safe: usa un Lock para que varios hilos no provoquen requests simultáneos.
    """
    with _token_lock:
        now = time.monotonic()
        if _token_cache["value"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["value"]

        tenant = settings.AZURE_TENANT_ID
        url    = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

        try:
            resp = requests.post(
                url,
                data={
                    "client_id":     settings.AZURE_CLIENT_ID,
                    "client_secret": settings.AZURE_CLIENT_SECRET,
                    "scope":         "https://graph.microsoft.com/.default",
                    "grant_type":    "client_credentials",
                },
                timeout=15,
            )
            resp.raise_for_status()
        except requests.Timeout:
            raise GraphAPIError("Timeout obteniendo token OAuth de Azure AD")
        except requests.HTTPError as exc:
            raise GraphAPIError(f"Error HTTP al obtener token: {exc.response.status_code} — {exc.response.text[:200]}")
        except requests.RequestException as exc:
            raise GraphAPIError(f"Error de red al obtener token: {exc}")

        data = resp.json()
        expires_in = int(data.get("expires_in", 3600))

        _token_cache["value"]      = data["access_token"]
        _token_cache["expires_at"] = now + expires_in

        logger.debug("Token OAuth renovado (expira en %ds)", expires_in)
        return _token_cache["value"]


# ---------------------------------------------------------------------------
# Envío de correo
# ---------------------------------------------------------------------------

def send_mail(to_email: str, subject: str, body_html: str) -> None:
    """
    Envía un correo a través de Microsoft Graph API.

    Args:
        to_email:  Dirección de destino.
        subject:   Asunto del correo.
        body_html: Cuerpo HTML del correo.

    Raises:
        GraphAPIError: Si la llamada a la API falla.
    """
    token  = _get_access_token()
    sender = settings.GRAPH_SENDER_EMAIL
    url    = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body_html,
            },
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": False,
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
    except requests.Timeout:
        raise GraphAPIError(f"Timeout enviando correo a {to_email}")
    except requests.HTTPError as exc:
        status = exc.response.status_code
        body   = exc.response.text[:300]

        # Token expirado por reloj de servidor — limpiar cache para forzar renovación
        if status == 401:
            with _token_lock:
                _token_cache["value"]      = None
                _token_cache["expires_at"] = 0.0

        raise GraphAPIError(f"Graph API {status} enviando a {to_email}: {body}")
    except requests.RequestException as exc:
        raise GraphAPIError(f"Error de red enviando a {to_email}: {exc}")

    logger.info("Correo enviado → %s | %s", to_email, subject)
