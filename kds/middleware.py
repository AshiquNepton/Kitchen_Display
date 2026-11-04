from django.shortcuts import redirect
from . import db


class ConfigRequiredMiddleware:
    """Send everything to /setup/ until a working connection has been saved."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(("/setup/", "/static/")) and not db.load_config():
            return redirect("/setup/")
        return self.get_response(request)
