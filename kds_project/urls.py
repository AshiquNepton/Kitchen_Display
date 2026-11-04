from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    # Serve CSS/JS ourselves so it works with DEBUG off inside the packaged exe.
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.BASE_DIR / "kds" / "static"}),
    path("", include("kds.urls")),
]
