from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django_htmx",
    "simple_history",
    "axes",
    "apps.core",
    "apps.accounts",
    "apps.organizations",
    "apps.dashboard",
    "apps.documents",
    "apps.messaging",
    "apps.audit",
    "apps.doctors",
    "apps.scheduling",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.core.middleware.CurrentOrganizationMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.organization",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
# Strict by default (production). Local dev relaxes this via .env (STRICT_PASSWORDS=False, PASSWORD_MIN_LENGTH=6).
PASSWORD_MIN_LENGTH = env.int("PASSWORD_MIN_LENGTH", default=10)
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": PASSWORD_MIN_LENGTH},
    },
]
if env.bool("STRICT_PASSWORDS", default=True):
    AUTH_PASSWORD_VALIDATORS += [
        {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
        {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
        {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    ]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# django-axes: lock after 5 failures for 15 minutes, per username + IP.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "accounts/locked_out.html"
AXES_USERNAME_FORM_FIELD = "username"
AXES_USERNAME_CALLABLE = "apps.accounts.axes.get_username"

# Sessions: expire after 12 hours of inactivity.
SESSION_COOKIE_AGE = 12 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

INVITATION_VALID_HOURS = 48

LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default="") or BASE_DIR / "media")
PRIVATE_MEDIA_ROOT = MEDIA_ROOT / "private"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": PRIVATE_MEDIA_ROOT, "base_url": "/private/"},
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="DigiClinic <no-reply@localhost>")

# Outgoing email/WhatsApp (SMTP settings come from the settings UI, encrypted in the DB).
MESSAGING_EMAIL_BACKEND = env("MESSAGING_EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
MESSAGING_SYNC = False  # True = send inline (tests); False = background thread locally, Celery in prod
MESSAGING_RETRY_DELAYS = (60, 300, 900)  # seconds between automatic retries of temporary errors

# Local WhatsApp gateway (whatsapp-gateway/server.js). Only Django talks to it, over localhost.
WA_GATEWAY_URL = env("WA_GATEWAY_URL", default="http://127.0.0.1:3320")
WA_GATEWAY_KEY = env("WA_GATEWAY_KEY", default="")
WA_WEBHOOK_SECRET = env("WA_WEBHOOK_SECRET", default="")  # defaults to WA_GATEWAY_KEY
WA_RATE_LIMIT_SECONDS = 20  # at most one WhatsApp message every 20s from the same number

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
