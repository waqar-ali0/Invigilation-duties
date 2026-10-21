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


def test_icon_tag_renders_sprite_reference():
    from django.template import Context, Template

    html = Template('{% load ui %}{% icon "calendar" %}').render(Context())
    assert html == '<svg class="icon" aria-hidden="true"><use href="#i-calendar"></use></svg>'


def test_icon_tag_rejects_unknown_names():
    from django.template import Context, Template

    assert Template('{% load ui %}{% icon "nope" %}').render(Context()) == ""


def test_initials_filter():
    from exams.templatetags.ui import initials

    assert initials("ali.raza@uni.edu") == "AR"
    assert initials("demo010@uni.edu") == "D"
    assert initials("") == "?"
