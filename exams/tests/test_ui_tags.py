import pytest

from exams.templatetags.ui import tone


@pytest.mark.parametrize(
    "status,expected",
    [
        ("published", "ok"),
        ("approved", "ok"),
        ("completed", "ok"),
        ("sent", "ok"),
        ("pending", "warn"),
        ("pending_peer", "warn"),
        ("pending_admin", "warn"),
        ("availability_open", "info"),
        ("allocating", "info"),
        ("queued", "info"),
        ("assigned", "info"),
        ("rejected", "danger"),
        ("declined", "danger"),
        ("absent", "danger"),
        ("failed", "danger"),
        ("draft", "neutral"),
        ("closed", "neutral"),
        ("cancelled", "neutral"),
        ("withdrawn", "neutral"),
        ("expired", "neutral"),
        ("something-new", "neutral"),
        (None, "neutral"),
    ],
)
def test_tone(status, expected):
    assert tone(status) == expected
