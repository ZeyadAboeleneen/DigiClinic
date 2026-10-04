# DigiClinic

نظام إدارة العيادات. الخطة الكاملة في [`docs/plan`](docs/plan/README.md) (تحتاج تحديث لدومين العيادات).

## التشغيل على الجهاز (Windows، من غير Docker)

دبل كليك على **`run.bat`** أو من الترمينال:

```
run.bat
```

وافتح http://127.0.0.1:8000

أول مرة بيحمّل Python 3.12 والمكتبات وPostgreSQL تلقائيًا (محتاج إنترنت). الداتابيز والملفات الخاصة بتتخزن في
`%LOCALAPPDATA%\marsool-albarq` برّه OneDrive.

## إنشاء المستخدمين

```
.venv\Scripts\python manage.py create_user --email you@example.com --name "الاسم" --role owner
```

الأدوار: `owner` · `admin` · `manager` · `sales` · `viewer`. كلمة المرور بتتسأل في الترمينال (10 حروف على الأقل على السيرفر، و6 على الجهاز).
بعد كده الـowner يقدر يدعو باقي المستخدمين من صفحة **المستخدمين**.

## الفحوصات

```
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check .
```

## إيقاف الداتابيز

```
.venv\Scripts\python scripts\devdb.py stop
```
