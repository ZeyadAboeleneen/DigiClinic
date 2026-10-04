"""Local PostgreSQL 16 for development (no Docker).

Uses the PostgreSQL binaries bundled with the `pgserver` package. Data lives outside
OneDrive in %LOCALAPPDATA%/marsool-albarq/pgdata so sync never touches live DB files.

    python scripts/devdb.py start | stop | status
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pgserver

PORT = int(os.environ.get("DEV_DB_PORT", "54329"))
DB_NAME = "marsool"
BIN = Path(pgserver.__file__).parent / "pginstall" / "bin"
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "marsool-albarq"
PGDATA = DATA_ROOT / "pgdata"
LOGFILE = DATA_ROOT / "postgres.log"


def _run(exe: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN / exe), *args], capture_output=True, text=True, check=check)  # noqa: S603


def is_running() -> bool:
    return _run("pg_isready", "-h", "127.0.0.1", "-p", str(PORT), check=False).returncode == 0


def _ensure_timezone_data() -> None:
    """The Windows build in pgserver ships without share/timezone; borrow IANA files from `tzdata`."""
    target = BIN.parent / "share" / "postgresql" / "timezone"
    if (target / "UTC").exists():
        return
    import tzdata

    shutil.copytree(
        Path(tzdata.__file__).parent / "zoneinfo",
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__init__.py", "__pycache__", "*.pyc", "*.tab", "*.zi", "tzdata.zi"),
    )


def start() -> None:
    _ensure_timezone_data()
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not (PGDATA / "PG_VERSION").exists():
        _run("initdb", "-D", str(PGDATA), "-U", "postgres", "-A", "trust", "-E", "UTF8", "--locale=C")
    if not is_running():
        # The server inherits pg_ctl's handles, so never pipe them or the caller blocks forever.
        subprocess.run(  # noqa: S603
            [
                str(BIN / "pg_ctl"),
                "-D",
                str(PGDATA),
                "-l",
                str(LOGFILE),
                "-o",
                f"-p {PORT} -c listen_addresses=127.0.0.1",
                "-w",
                "start",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    exists = _run(
        "psql",
        "-h",
        "127.0.0.1",
        "-p",
        str(PORT),
        "-U",
        "postgres",
        "-tAc",
        f"SELECT 1 FROM pg_database WHERE datname='{DB_NAME}'",  # noqa: S608 constant name
    ).stdout.strip()
    if exists != "1":
        _run("createdb", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", DB_NAME)


def stop() -> None:
    if is_running():
        _run("pg_ctl", "-D", str(PGDATA), "-m", "fast", "-w", "stop")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start()
        print(f"PostgreSQL running on 127.0.0.1:{PORT} (db: {DB_NAME}, data: {PGDATA})")
    elif cmd == "stop":
        stop()
        print("PostgreSQL stopped")
    else:
        print("running" if is_running() else "stopped")
