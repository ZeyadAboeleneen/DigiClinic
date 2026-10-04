"""The single background process (no Celery/Redis): sends due messages and runs periodic jobs (06 §6.2).

manage.py run_scheduler           # loop forever (run.bat starts it in its own window)
manage.py run_scheduler --once    # one tick, then exit
"""

import os
import socket
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from apps.notifications import dispatcher as dispatch
from apps.notifications.models import SchedulerHeartbeat
from apps.notifications.services import HEARTBEAT_NAME
from apps.scheduling import services as booking

ADVISORY_LOCK_ID = 0x44_43_4C_4E  # "DCLN" — one scheduler per database, ever


def beat(now=None):
    SchedulerHeartbeat.objects.update_or_create(
        name=HEARTBEAT_NAME,
        defaults={"beat_at": now or timezone.now(), "pid": os.getpid(), "host": socket.gethostname()[:100]},
    )


# (name, interval, callable(now)).
JOBS = [
    ("mark_no_shows", timedelta(minutes=2), lambda now: booking.mark_no_shows(now)),
    ("dispatch", timedelta(seconds=0), lambda now: dispatch.dispatcher.run_once(now)),
    ("cleanup", timedelta(days=1), lambda now: dispatch.cleanup(now)),
]


def tick(last_run: dict, now=None):
    now = now or timezone.now()
    beat(now)
    for name, interval, job in JOBS:
        if name not in last_run or now - last_run[name] >= interval:
            job(now)
            last_run[name] = now


class Command(BaseCommand):
    help = "Send scheduled WhatsApp/email messages and run periodic jobs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def _lock(self):
        # Session-level lock: it lives as long as this DB connection, so it's re-taken after any reconnect.
        with connection.cursor() as c:
            c.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_LOCK_ID])
            if not c.fetchone()[0]:
                raise CommandError("Another run_scheduler is already running on this database — not starting twice.")

    def handle(self, *args, **opts):
        self._lock()
        recovered = dispatch.recover_interrupted()
        if recovered:
            self.stdout.write(self.style.WARNING(f"{recovered} message(s) were interrupted mid-send → marked failed."))
        last_run = {}
        self.stdout.write(self.style.SUCCESS("DigiClinic scheduler running. Keep this window open."))
        try:
            while True:
                try:
                    tick(last_run)
                except Exception as e:  # a DB hiccup shouldn't kill the process
                    self.stderr.write(f"tick failed: {e.__class__.__name__}: {e}")
                    if opts["once"]:
                        raise
                    connection.close()
                    time.sleep(settings.SCHEDULER_TICK_SECONDS)
                    self._lock()
                    continue
                if opts["once"]:
                    break
                time.sleep(settings.SCHEDULER_TICK_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            with connection.cursor() as c:
                c.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_LOCK_ID])
