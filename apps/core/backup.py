"""Encrypted backups (07 §7.5): `pg_dump` (custom format) + private media → one `.dcbak` file, Fernet-encrypted with
`BACKUP_KEY` (falls back to `FIELD_ENCRYPTION_KEY`). Restore goes into an **empty** database (and media folder) only,
so a mistyped command can never overwrite live data.

File layout (before encryption): a tar with `manifest.json`, `db.dump`, `media/...`.
Fernet works in memory: fine for one clinic (DB + scans of a few hundred MB). Revisit for very large media.
"""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone

SUFFIX = ".dcbak"
FORMAT_VERSION = 1


class BackupError(Exception):
    pass


def backup_dir() -> Path:
    path = Path(getattr(settings, "BACKUP_DIR", "") or Path(settings.MEDIA_ROOT).parent / "backups")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fernet() -> Fernet:
    key = getattr(settings, "BACKUP_KEY", "") or settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise BackupError("BACKUP_KEY / FIELD_ENCRYPTION_KEY is not set.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def _pg_bin(name: str) -> str:
    """`PG_BIN` setting → the embedded pgserver binaries (local dev) → PATH (servers)."""
    configured = getattr(settings, "PG_BIN", "")
    candidates = [Path(configured)] if configured else []
    try:
        import pgserver

        candidates.append(Path(pgserver.__file__).parent / "pginstall" / "bin")
    except ImportError:
        pass
    for folder in candidates:
        for exe in (folder / name, folder / f"{name}.exe"):
            if exe.exists():
                return str(exe)
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} not found (set PG_BIN).")
    return found


def _conn_args(db: dict, name: str | None = None):
    args = ["-h", db.get("HOST") or "127.0.0.1", "-p", str(db.get("PORT") or 5432), "-U", db.get("USER") or "postgres"]
    env = {**os.environ}
    if db.get("PASSWORD"):
        env["PGPASSWORD"] = db["PASSWORD"]
    return args, env, name or db["NAME"]


def _run(cmd, env):
    proc = subprocess.run(cmd, capture_output=True, env=env)  # noqa: S603 - fixed binaries, no shell
    if proc.returncode != 0:
        raise BackupError(proc.stderr.decode(errors="replace").strip()[-1000:])
    return proc


def _media_root() -> Path:
    return Path(settings.PRIVATE_MEDIA_ROOT)


def create_backup(*, dest_dir: Path | None = None, now=None) -> Path:
    now = now or timezone.now()
    db = settings.DATABASES["default"]
    args, env, name = _conn_args(db)
    with tempfile.TemporaryDirectory() as tmp:
        dump = Path(tmp) / "db.dump"
        _run([_pg_bin("pg_dump"), *args, "-Fc", "--no-owner", "-f", str(dump), name], env)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            manifest = {"format": FORMAT_VERSION, "created_at": now.isoformat(), "database": name}
            data = json.dumps(manifest).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            tar.add(dump, arcname="db.dump")
            if _media_root().exists():
                tar.add(_media_root(), arcname="media")
    target = (dest_dir or backup_dir()) / f"digiclinic-{timezone.localtime(now):%Y%m%d-%H%M%S}{SUFFIX}"
    target.write_bytes(_fernet().encrypt(buf.getvalue()))
    return target


def prune(keep: int = 14, folder: Path | None = None) -> list[Path]:
    files = sorted((folder or backup_dir()).glob(f"digiclinic-*{SUFFIX}"), reverse=True)
    removed = files[keep:]
    for f in removed:
        f.unlink()
    return removed


def latest(folder: Path | None = None) -> Path | None:
    files = sorted((folder or backup_dir()).glob(f"digiclinic-*{SUFFIX}"), reverse=True)
    return files[0] if files else None


def backed_up_today(now=None) -> bool:
    last = latest()
    if last is None:
        return False
    stamp = last.stem.removeprefix("digiclinic-")
    day = datetime.strptime(stamp, "%Y%m%d-%H%M%S").date()
    return day == timezone.localtime(now or timezone.now()).date()


def read_backup(path: Path) -> bytes:
    try:
        return _fernet().decrypt(Path(path).read_bytes())
    except InvalidToken as e:
        raise BackupError("الملف ده مش نسخة DigiClinic أو المفتاح غلط.") from e


def _database_is_empty(args, env, name) -> bool:
    proc = _run(
        [_pg_bin("psql"), *args, "-d", name, "-tAc",
         "select count(*) from information_schema.tables where table_schema='public'"],
        env,
    )  # fmt: skip
    return proc.stdout.decode().strip() == "0"


def restore_backup(path: Path, *, database: str, media_root: Path, create_db: bool = False) -> dict:
    """Restore into `database` (must be empty, or is created with `create_db`) and `media_root` (must be empty)."""
    raw = read_backup(path)
    db = settings.DATABASES["default"]
    args, env, name = _conn_args(db, database)
    media_root = Path(media_root)
    if media_root.exists() and any(media_root.iterdir()):
        raise BackupError(f"{media_root} مش فاضي — الاسترجاع بيتعمل في مكان فاضي بس.")
    if create_db:
        _run([_pg_bin("createdb"), *args, name], env)
    elif not _database_is_empty(args, env, name):
        raise BackupError(f"الداتابيز {name} مش فاضية — الاسترجاع بيتعمل في داتابيز فاضية بس.")
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")
        manifest = json.loads((Path(tmp) / "manifest.json").read_text())
        _run([_pg_bin("pg_restore"), *args, "--no-owner", "-d", name, str(Path(tmp) / "db.dump")], env)
        if (Path(tmp) / "media").exists():
            shutil.copytree(Path(tmp) / "media", media_root, dirs_exist_ok=True)
    return manifest


def count_rows(database: str, tables: list[str]) -> dict:
    db = settings.DATABASES["default"]
    args, env, name = _conn_args(db, database)
    out = {}
    for table in tables:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
            raise BackupError(f"bad table name: {table!r}")
        query = f'select count(*) from "{table}"'  # noqa: S608 - identifier validated just above
        proc = _run([_pg_bin("psql"), *args, "-d", name, "-tAc", query], env)
        out[table] = int(proc.stdout.decode().strip())
    return out


def drop_database(database: str):
    db = settings.DATABASES["default"]
    args, env, name = _conn_args(db, database)
    _run([_pg_bin("dropdb"), *args, "--if-exists", name], env)
