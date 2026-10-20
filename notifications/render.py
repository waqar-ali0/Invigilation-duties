from django.conf import settings
from django.template.loader import render_to_string


def render_email(name, context):
    """(subject, body) from templates/emails/<name>_subject.txt and <name>_body.txt."""
    subject = " ".join(render_to_string(f"emails/{name}_subject.txt", context).split())
    body = render_to_string(f"emails/{name}_body.txt", context)
    return subject, body


def absolute_url(path):
    return settings.SITE_URL.rstrip("/") + path
