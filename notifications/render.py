from django.conf import settings
from django.template import Context
from django.template.loader import get_template


def _render_text(template_name, context):
    # Plain-text email: no HTML escaping (names like "O'Brien & Co" must stay as typed).
    return get_template(template_name).template.render(Context(context, autoescape=False))


def render_email(name, context):
    """(subject, body) from templates/emails/<name>_subject.txt and <name>_body.txt."""
    subject = " ".join(_render_text(f"emails/{name}_subject.txt", context).split())
    body = _render_text(f"emails/{name}_body.txt", context)
    return subject, body


def absolute_url(path):
    return settings.SITE_URL.rstrip("/") + path
