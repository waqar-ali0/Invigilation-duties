from django.conf import settings
from django.db import models


class BatchStatus(models.TextChoices):
    MAPPING = "mapping", "Choosing columns"
    PREVIEW = "preview", "Waiting for confirmation"
    DONE = "done", "Imported"
    CANCELLED = "cancelled", "Cancelled"


class ImportBatch(models.Model):
    """One uploaded spreadsheet on its way through mapping, preview and confirmation.

    Nothing reaches the real tables until the batch is confirmed; afterwards the batch stays as
    the import history (file, who, when, what happened)."""

    kind = models.CharField(max_length=20)
    filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    headers = models.JSONField(default=list)
    rows = models.JSONField(default=list)  # [[spreadsheet row number, [values...]], ...]
    mapping = models.JSONField(default=dict)  # field key -> column index, or absent
    options = models.JSONField(default=dict)
    period = models.ForeignKey("exams.ExamPeriod", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    source = models.ForeignKey("SheetSource", null=True, blank=True, on_delete=models.SET_NULL, related_name="batches")
    status = models.CharField(max_length=10, choices=BatchStatus.choices, default=BatchStatus.MAPPING)
    mode = models.CharField(max_length=10, blank=True)
    result = models.JSONField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "import batches"

    def __str__(self):
        return f"{self.kind}: {self.filename} ({self.created_at:%d %b %Y %H:%M})"


class SheetSource(models.Model):
    """A Google Sheet the exam office imports from (optional). Each sync makes an ordinary import
    batch that still has to be checked and confirmed; nothing changes without confirmation."""

    KINDS = (("faculty", "Faculty"), ("rooms", "Rooms"), ("courses", "Courses"))

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=KINDS)
    spreadsheet_id = models.CharField(
        max_length=200, help_text="The sheet's link or the ID between /d/ and /edit in its address."
    )
    worksheet = models.CharField(max_length=100, blank=True, help_text="Tab name; empty = the first tab.")
    sync_hours = models.PositiveIntegerField(
        "check every (hours)", default=0, help_text="0 = only when you press Sync now. Otherwise changes are prepared for review automatically."
    )
    active = models.BooleanField(default=True)
    last_hash = models.CharField(max_length=64, blank=True, editable=False)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.CharField(max_length=500, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
