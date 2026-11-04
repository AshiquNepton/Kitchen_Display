import os
import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # True when running as the packaged KDS.exe
# Code, templates and static files live here (inside the exe bundle when frozen).
BASE_DIR = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent
# Writable files (kds_config.json) live here: C:\ProgramData\NeptonKDS when installed.
DATA_DIR = Path(os.environ.get("KDS_DATA_DIR") or
                (Path(os.environ.get("PROGRAMDATA", str(BASE_DIR))) / "NeptonKDS" if FROZEN else BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key():
    """Per-install random key (signs the admin cookie); generated once and kept in the data folder."""
    import secrets
    from kds_project import secure
    path = DATA_DIR / "secret.dat"  # encrypted
    try:
        return secure.unseal(path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        key = secrets.token_urlsafe(50)
        try:
            path.write_text(secure.seal(key), encoding="ascii")
        except OSError:
            pass
        return key


SECRET_KEY = os.environ.get("KDS_SECRET_KEY") or _secret_key()
DEBUG = os.environ.get("KDS_DEBUG", "0" if FROZEN else "1") == "1"
ALLOWED_HOSTS = ["*"]  # LAN tool; restrict this if exposed beyond the restaurant network

INSTALLED_APPS = ["django.contrib.staticfiles", "kds"]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "kds.middleware.ConfigRequiredMiddleware",
]
ROOT_URLCONF = "kds_project.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": []},
}]
# Django's own DB is unused (the POS database is accessed with pyodbc).
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3"}}
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# KDS tuning
KDS_REFRESH_SECONDS = 10   # board auto-refresh
KDS_WARN_MINUTES = 10      # timer turns yellow
KDS_LATE_MINUTES = 20      # timer turns red and pulses