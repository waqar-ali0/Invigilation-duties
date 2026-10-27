"""Minimal client for the WhatsApp Cloud API (graph.facebook.com). The token is sent only in the
Authorization header and never logged, stored or shown."""

import hashlib
import hmac
import json
import urllib.error
import urllib.request

from django.conf import settings

GRAPH = "https://graph.facebook.com"
TIMEOUT = 20
# Meta error codes that will not succeed on retry (bad number, template mismatch, policy, auth).
PERMANENT_CODES = {100, 131008, 131009, 131021, 131026, 131030, 131031, 131045, 131047, 131051, 132000, 132001, 132005,
                   132007, 132012, 132015, 132016, 133010, 190, 10, 200}


class ApiError(Exception):
    def __init__(self, message, code=None, retry=False):
        super().__init__(message)
        self.code = code
        self.retry = retry


def _request(method, url, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            err = body.get("error", {})
        except (ValueError, AttributeError):
            err = {}
        code = err.get("code")
        message = err.get("error_user_msg") or err.get("message") or f"HTTP {e.code}"
        retry = e.code == 429 or e.code >= 500 or code in (4, 80007, 130429, 131000, 131016, 131048, 131056)
        if code in PERMANENT_CODES:
            retry = False
        raise ApiError(f"{message} (code {code or e.code})", code=code or e.code, retry=retry) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(f"Could not reach WhatsApp: {e}", retry=True) from None


def send_template(cfg, to, template_name, language, params):
    """Submit a template message; returns the provider message id (wamid…)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": str(p)[:1000]} for p in params]}] if params else [],
        },
    }
    result = _request("POST", f"{GRAPH}/{cfg.api_version}/{cfg.phone_number_id}/messages", cfg.token, payload)
    try:
        return result["messages"][0]["id"]
    except (KeyError, IndexError, TypeError):
        raise ApiError("WhatsApp did not return a message id", retry=True) from None


def check_connection(cfg):
    """The phone number's display number and verified name, or ApiError."""
    return _request("GET", f"{GRAPH}/{cfg.api_version}/{cfg.phone_number_id}?fields=display_phone_number,verified_name,quality_rating", cfg.token)


def valid_signature(body, header):
    """Check Meta's X-Hub-Signature-256 against WHATSAPP_APP_SECRET."""
    secret = getattr(settings, "WHATSAPP_APP_SECRET", "")
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])
