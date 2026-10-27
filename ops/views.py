from django.db import connection
from django.http import HttpResponse


def healthz(request):
    """Liveness check for monitoring: 200 "ok" when the app and its database answer."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")
