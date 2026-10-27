import os

from django.core.management.base import BaseCommand

from config.wsgi import application


class Command(BaseCommand):
    help = "Run the production web server (Waitress). Defaults come from WEB_HOST, WEB_PORT and WEB_THREADS."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=os.environ.get("WEB_HOST", "127.0.0.1"))
        parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8000")))
        parser.add_argument("--threads", type=int, default=int(os.environ.get("WEB_THREADS", "8")))

    def handle(self, host, port, threads, **options):
        import waitress

        self.stdout.write(f"Serving on http://{host}:{port} with {threads} threads")
        waitress.serve(
            application,
            host=host,
            port=port,
            threads=threads,
            # Caddy runs on the same machine and tells us the visitor used https.
            trusted_proxy="127.0.0.1",
            trusted_proxy_headers="x-forwarded-proto x-forwarded-for x-forwarded-host",
            clear_untrusted_proxy_headers=True,
            ident="invigilation",
        )
