from django.urls import reverse

from exams.tests.helpers import make_xlsx
from imports.models import ImportBatch


def run_import(client, kind, rows, period=None, mode=None, **options):
    """Upload `rows` (first row = headings) through the import wizard with the guessed columns,
    confirm every row that can be imported, and return the finished batch."""
    url = reverse("import_start_period", args=[kind, period.pk]) if period else reverse("import_start", args=[kind])
    response = client.post(url, {"file": make_xlsx(rows)})
    assert response.status_code == 302, response.content.decode()[:500]
    batch = ImportBatch.objects.latest("pk")
    data = {f"map_{k}": v for k, v in batch.mapping.items()}
    data.update({f"opt_{k}": v for k, v in options.items()})
    for key, value in batch.options.items():
        if value is True and key not in options:
            data[f"opt_{key}"] = "on"
    response = client.post(reverse("import_columns", args=[batch.pk]), data)
    assert response.status_code == 302, response.content.decode()[:800]
    data = {"row": [str(n) for n, _ in batch.rows]}
    if mode:
        data["mode"] = mode
    client.post(reverse("import_batch", args=[batch.pk]), data)
    batch.refresh_from_db()
    return batch
