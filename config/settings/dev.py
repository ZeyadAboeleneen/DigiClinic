from .base import *

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
