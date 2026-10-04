# 01 — System Architecture

## 1.1 ليه Django + HTMX (مش Next.js)

- نظام داخلي، مستخدمين قليلين، ومطوّر واحد بيبني ويصيّن → codebase واحد أهم من SPA.
- HTMX بيدّي إحساس SPA في الأماكن المهمة (بحث المنتجات، تحميل الـcontacts، الـpreview) من غير API layer منفصل.
- Django Admin متاح كـ"emergency panel" للـsuperuser بس، والواجهة اليومية مبنية custom.
- لو احتجنا mobile app أو public API بعدين: نضيف Django REST Framework endpoints جنب الـviews من غير ما نعيد البناء.

## 1.2 شكل النظام

```
                 Internet
                    │ HTTPS 443
          ┌─────────▼──────────┐
          │  Nginx (الموجود)    │  ← مفيش أي تعديل على البورتات
          └─────────┬──────────┘
                    │ proxy_pass 127.0.0.1:8100
┌───────────────────▼────────────────────────────────┐
│ docker compose (project: albarq)                   │
│                                                    │
│  web      Django + Gunicorn        127.0.0.1:8100  │
│  worker   Celery (PDF, sending, backups)           │
│  beat     Celery beat (expiry, reminders)          │
│  db       PostgreSQL 16            internal only   │
│  redis    Redis 7                  internal only   │
│  openwa   OpenWA gateway           internal only   │
│                                                    │
│  volumes: pgdata, media, openwa-data               │
└────────────────────────────────────────────────────┘
```

- الـOpenWA dashboard **مش مكشوف للإنترنت**. Django هو الوحيد اللي بيكلمه على الشبكة الداخلية بـAPI key.
- الـwebhooks من OpenWA لـDjango بتمشي على الشبكة الداخلية (`http://web:8000/integrations/whatsapp/webhook/`) مع HMAC secret.

## 1.3 هيكل المشروع

```
albarq/
├── config/                 settings (base/dev/prod), urls, celery, wsgi
├── apps/
│   ├── core/               BaseModel, TenantScopedModel, middleware, utils
│   ├── organizations/      Organization, Membership, OrgSettings, Branding
│   ├── accounts/           User, login, roles/permissions
│   ├── customers/          Customer, Contact, ContactChannel
│   ├── catalog/            Category, Unit, Product
│   ├── quotations/         Quotation, QuotationItem, numbering, builder views
│   ├── documents/          PDF rendering (WeasyPrint templates)
│   ├── messaging/          Email + WhatsApp providers, Delivery, templates
│   ├── audit/              AuditEvent
│   └── dashboard/          home stats
├── templates/              base.html, partials/ (HTMX fragments), pdf/
├── static/                 tailwind output, alpine, htmx, fonts (Cairo, Poppins)
├── seed/                   products.csv, brand.json
├── deploy/                 docker-compose.yml, nginx.conf.example, backup.sh
├── tests/
└── manage.py
```

## 1.4 Multi-Tenancy (مهم من اليوم الأول)

- **الـTenant اسمه `Organization`** (مش Company عشان ميتلخبطش مع العميل اللي نوعه "شركة").
- كل model تشغيلي بيورث من `TenantScopedModel` اللي فيه `organization = FK(Organization)`.
- `CurrentOrganizationMiddleware` بيحدد `request.organization` من الـMembership بتاعة المستخدم.
- كل queryset في الـviews بيعدّي على `Model.objects.for_org(request.organization)`. **ممنوع** `Model.objects.all()` في أي view.
- Test إجباري: مستخدم من org A ميقدرش يشوف أو يعدّل أي حاجة من org B (بالـID المباشر في الـURL).
- البراندنج (لوجو، ألوان، بيانات التواصل، الشروط الافتراضية) جزء من بيانات الـOrganization، مش hardcoded. البرق = Organization #1.
- حاليًا: org واحدة، مفيش شاشة تسجيل شركات. التوسع = إضافة org من الـsuperuser admin.

## 1.5 Stack Versions

| Component | Version |
|---|---|
| Python | 3.12 |
| Django | 5.2 LTS |
| PostgreSQL | 16 |
| Redis | 7 |
| Celery | 5.x |
| HTMX | 2.x |
| Alpine.js | 3.x |
| WeasyPrint | latest stable |
| OpenWA | latest release (pinned image tag) |

## 1.6 Python Packages الأساسية

`django`, `psycopg[binary]`, `django-environ`, `celery`, `redis`, `django-htmx`,
`weasyprint`, `phonenumbers`, `django-phonenumber-field`, `argon2-cffi`,
`django-axes`, `django-simple-history`, `cryptography`, `httpx`,
`pillow`, `gunicorn`, `whitenoise`, `sentry-sdk` (اختياري).
Dev: `pytest-django`, `factory-boy`, `ruff`, `pre-commit`.
