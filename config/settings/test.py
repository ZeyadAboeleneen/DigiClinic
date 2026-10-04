import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "9X7OCXHJVDjSH7tYbRwxTMw2D2Oo9Vi5SCtgzuFm05Y=")
os.environ.setdefault("DATABASE_URL", "postgres://postgres@127.0.0.1:54329/marsool")
# Tests always use the production password policy, whatever the local .env says.
os.environ["PASSWORD_MIN_LENGTH"] = "10"
os.environ["STRICT_PASSWORDS"] = "True"

from .base import *

DEBUG = False
ALLOWED_HOSTS = ["testserver"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MESSAGING_EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MESSAGING_SYNC = True
MESSAGING_RETRY_DELAYS = (0, 0, 0)
WA_GATEWAY_URL = "http://gateway.test"
WA_GATEWAY_KEY = "test-gateway-key"
WA_WEBHOOK_SECRET = ""
WA_RATE_LIMIT_SECONDS = 0
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
MEDIA_ROOT = BASE_DIR / "tmp" / "test-media"
PRIVATE_MEDIA_ROOT = MEDIA_ROOT / "private"
STORAGES["default"]["OPTIONS"]["location"] = PRIVATE_MEDIA_ROOT
