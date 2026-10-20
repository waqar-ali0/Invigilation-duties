from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only record of who changed what. Never edited or deleted through the application."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=50)
    object_type = models.CharField(max_length=100)
    object_id = models.CharField(max_length=50)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.object_type}#{self.object_id}"
