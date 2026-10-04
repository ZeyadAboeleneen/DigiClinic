# DigiClinic — VPS deployment runbook (Phase 10)

Everything below is **for review first** (docs/plan/11: "اكتبلي الأوامر وراجعها معايا قبل التنفيذ").
Placeholders: `clinic.example.com` (the subdomain), `8110` (WEB_PORT), `/opt/digiclinic` (install path).
Rules: never edit another project's Nginx file, never touch what listens on 80/443 except adding our own site file,
never stop/restart other containers. Every step is reversible (see §10).

Layout: `db` (Postgres 16) · `web` (gunicorn, published on **127.0.0.1:8110 only**) · `scheduler` (`run_scheduler`)
· `whatsapp` (gateway, private network only). Data lives in 3 named volumes: `pgdata`, `appdata` (private media +
backups), `whatsapp` (the linked WhatsApp session).

---

## 0. What's needed before starting
- [ ] SSH access (user with sudo) and the server's IP.
- [ ] The subdomain, with a DNS **A record → the VPS IP** (check: `dig +short clinic.example.com`).
- [ ] SMTP account for system e-mail (alerts, password resets).
- [ ] An off-site target for backups configured in `rclone` (Google Drive / Backblaze / S3…).
- [ ] The repo reachable from the server: a read-only **deploy key** on GitHub (`ZeyadAboeleneen/DigiClinic`),
      or upload a bundle (`git bundle create digiclinic.bundle --all` locally, then `scp`).

## 1. Pre-flight (read-only — shows what's already there)
```bash
docker --version && docker compose version
ss -ltnp                                  # pick a free WEB_PORT (8110 if free)
ls -l /etc/nginx/sites-enabled/           # existing sites — we only ADD digiclinic.conf
sudo nginx -t                             # must already be OK before we change anything
df -h / && free -m                        # ≥ 3 GB free disk, ≥ 2 GB RAM recommended (2 Chromiums)
docker ps --format '{{.Names}}\t{{.Ports}}'   # other projects' containers, for the before/after comparison
for d in $(ls /etc/nginx/sites-enabled); do echo "$d"; done   # note their domains for §9 checks
```

## 2. Code
```bash
sudo mkdir -p /opt/digiclinic && sudo chown "$USER" /opt/digiclinic
git clone git@github.com:ZeyadAboeleneen/DigiClinic.git /opt/digiclinic
cd /opt/digiclinic && git log --oneline -1
```

## 3. Configuration
```bash
cd /opt/digiclinic/deploy
cp env.production.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"        # → SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"        # → POSTGRES_PASSWORD, WA_GATEWAY_KEY
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # → FIELD_ENCRYPTION_KEY
nano .env      # fill everything; set the subdomain in ALLOWED_HOSTS / CSRF_TRUSTED_ORIGINS / SITE_URL
```
**Save FIELD_ENCRYPTION_KEY in a password manager now** — it is needed to read any backup.

## 4. Build, migrate, seed
```bash
cd /opt/digiclinic/deploy
docker compose -f compose.yml --env-file .env build
docker compose -f compose.yml --env-file .env up -d db
docker compose -f compose.yml --env-file .env run --rm web python manage.py migrate
docker compose -f compose.yml --env-file .env run --rm web python manage.py seed_org            # from docs/plan/seed/clinic.json — edit it first with the real clinic, or fix in Settings later
docker compose -f compose.yml --env-file .env run --rm web python manage.py seed_notifications
docker compose -f compose.yml --env-file .env run --rm web python manage.py import_drugs
docker compose -f compose.yml --env-file .env run --rm web python manage.py create_user --email OWNER@EMAIL --name "اسم الدكتور" --role owner
docker compose -f compose.yml --env-file .env up -d
docker compose -f compose.yml --env-file .env ps
curl -s http://127.0.0.1:8110/healthz/        # {"db": true, "scheduler": true} after ~20 s
```

## 5. Nginx + HTTPS (only a new site file)
```bash
sudo cp /opt/digiclinic/deploy/nginx/digiclinic.conf /etc/nginx/sites-available/digiclinic.conf
sudo sed -i 's/clinic.example.com/REAL.SUBDOMAIN/g; s/8110/REAL_PORT/g' /etc/nginx/sites-available/digiclinic.conf
sudo mkdir -p /var/www/certbot
sudo ln -s /etc/nginx/sites-available/digiclinic.conf /etc/nginx/sites-enabled/digiclinic.conf
sudo nginx -t && sudo systemctl reload nginx                       # step 1: port-80 block only
sudo certbot certonly --webroot -w /var/www/certbot -d REAL.SUBDOMAIN   # certonly: certbot edits NO nginx file
sudo nano /etc/nginx/sites-available/digiclinic.conf               # uncomment the 443 block
sudo nginx -t && sudo systemctl reload nginx                       # step 2
curl -sI https://REAL.SUBDOMAIN/healthz/ | head -1                 # HTTP/2 200
```
(If this Nginx predates 1.25, replace `http2 on;` with `listen 443 ssl http2;`.)

## 6. Link WhatsApp
Log in at `https://REAL.SUBDOMAIN` → الإعدادات → واتساب → اربط → scan the QR with the clinic's phone.
Then "ابعت تجربة" to your own number. The session survives restarts (volume `whatsapp`).

## 7. Cron (alerts + off-site backups)
```bash
rclone config                                   # create the remote, e.g. "gdrive"
sudo cp /opt/digiclinic/deploy/cron /etc/cron.d/digiclinic
sudo sed -i 's/CHANGE-ME/gdrive/' /etc/cron.d/digiclinic
chmod +x /opt/digiclinic/deploy/offsite-backup.sh
sudo /opt/digiclinic/deploy/offsite-backup.sh    # (after the first daily backup exists — or run §8's backup first)
```

## 8. Restore drill on the server (DoD — into a throw-away DB, never the live one)
```bash
cd /opt/digiclinic/deploy
docker compose -f compose.yml --env-file .env exec web python manage.py backup
docker compose -f compose.yml --env-file .env exec web sh -c 'ls -t /data/backups | head -1'
docker compose -f compose.yml --env-file .env exec web python manage.py restore /data/backups/<FILE> \
  --database digiclinic_drill --media-root /tmp/drill-media --create-db --check
# compare the printed counts with the live ones, then remove the drill copy:
docker compose -f compose.yml --env-file .env exec web python manage.py shell -c \
  "from apps.core import backup; backup.drop_database('digiclinic_drill')"
```
Also restore one off-site copy (download it with `rclone copy`, then the same `restore`): that proves the key + remote.

## 9. Definition of Done checks
- [ ] `https://REAL.SUBDOMAIN` works; login, booking, reception, doctor desk, prescription print.
- [ ] A **real reminder** arrives from the server: book yourself for ~2 h ahead → the 1-hour reminder arrives.
- [ ] Backup made + restored (§8), off-site copy present (`rclone ls REMOTE:`).
- [ ] Other projects unaffected: same `docker ps` list as in §1, and each of their domains still answers:
      `curl -sI https://OTHER.DOMAIN | head -1` (before and after).
- [ ] Alerts: `docker compose ... stop scheduler`, wait 20 min → owner e-mail arrives; `start scheduler` again.

## 10. Updates & rollback
```bash
cd /opt/digiclinic && git pull
cd deploy && docker compose -f compose.yml --env-file .env exec web python manage.py backup   # always first
docker compose -f compose.yml --env-file .env build
docker compose -f compose.yml --env-file .env run --rm web python manage.py migrate
docker compose -f compose.yml --env-file .env up -d
```
Rollback code: `git checkout <previous-commit>` + build + up (restore the pre-update backup if a migration ran).
Remove everything: `sudo rm /etc/nginx/sites-enabled/digiclinic.conf && sudo systemctl reload nginx`,
`sudo rm /etc/cron.d/digiclinic`, `docker compose -f compose.yml --env-file .env down` (add `-v` only to also
delete the data volumes — irreversible).
