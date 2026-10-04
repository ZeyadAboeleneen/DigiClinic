from .base import *

DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# Container-to-container calls on the private compose network are plain HTTP and must not be redirected:
# the WhatsApp gateway's webhook and the health check.
SECURE_REDIRECT_EXEMPT = [r"^integrations/whatsapp/webhook/$", r"^healthz/$"]
# System e-mail (password resets, owner alerts): EMAIL_URL=smtp+tls://user:password@smtp.example.com:587
vars().update(env.email("EMAIL_URL", default="consolemail://"))
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "SAMEORIGIN"  # same-origin print iframe (05 §5.4)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SILENCED_SYSTEM_CHECKS = [
    "security.W019",  # X_FRAME_OPTIONS=SAMEORIGIN on purpose: the prescription print iframe (05 §5.4)
    "security.W021",  # no HSTS preload: it would bind the whole parent domain (shared with other projects)
]
