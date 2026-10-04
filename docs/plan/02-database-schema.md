# 02 — Database Schema

كل الجداول فيها `id` (BigAutoField)، `created_at`، `updated_at`.
كل الجداول المعلّمة **[T]** فيها `organization_id` (FK, indexed) وبتورث من `TenantScopedModel`.
الفلوس كلها `Decimal(12,2)` بالجنيه المصري. مفيش float نهائيًا.

---

## Organizations

### Organization
| Field | Type | Notes |
|---|---|---|
| name_ar / name_en | str | البرق للتجارة والتوريدات / Al-Barq for Trade and Supplies |
| slug | str unique | `albarq` |
| is_active | bool | |

### OrganizationSettings (1:1 Organization)
| Field | Type | Notes |
|---|---|---|
| logo | image | PNG/SVG عالي الجودة |
| primary_color / dark_color / neutral_color | str | #AE171C / #111111 / #F5F2EF |
| tagline_ar | str | السطر تحت الاسم في الـPDF |
| phones | JSON list | الأرقام اللي تظهر في الـPDF |
| email_display, address_ar | str | |
| quote_number_prefix | str | `AB` |
| quote_number_format | str | الافتراضي `{prefix}-{year}/{month:02d}-{seq:02d}` → AB-2026/09-01 |
| default_validity_days | int | 30 |
| vat_rate | decimal | 14.00 |
| prices_include_vat | bool | false (زي العرض الحالي) |
| default_intro_text | text | "يسعدنا أن نتقدم لسيادتكم..." |
| default_terms | JSON list | الشروط الخمسة الحالية |
| default_whatsapp_message / default_email_subject / default_email_body | text | فيها متغيرات |

### Membership
| Field | Type | Notes |
|---|---|---|
| user | FK User | |
| organization | FK Organization | |
| role | choice | owner / admin / manager / sales / viewer |
| is_active | bool | |
| unique | (user, organization) | |

### QuoteSequence [T]
| Field | Type | Notes |
|---|---|---|
| year | int | |
| month | int | الترقيم بيبدأ من 1 كل شهر |
| last_value | int | يتقفل بـ`select_for_update()` عند توليد رقم |
| unique | (organization, year, month) | |

---

## Customers

### Customer [T]
| Field | Type | Notes |
|---|---|---|
| name | str | Jaz Hotels |
| kind | choice | group / hotel / resort / restaurant / company / other |
| parent | FK self, null | Travco ← Jaz Hotels ← Jaz Aquamarine (اختياري) |
| city / area | str | |
| address | text | |
| tax_id | str, null | للمستقبل (فواتير) |
| notes | text | |
| is_active | bool | |
| index | (organization, name) + trigram على name للبحث |

العنوان في الـPDF بيتبني من الشجرة: "السادة / شركة Travco — سلسلة الفنادق Jaz Hotels".

### Contact [T]
| Field | Type | Notes |
|---|---|---|
| customer | FK Customer | |
| name | str | Ahmed |
| job_title | str | Purchasing Manager |
| department | str | إدارة المشتريات |
| salutation | str, null | أ. / م. |
| preferred_channel | choice | whatsapp / email / both |
| is_primary | bool | الافتراضي لما تختاري العميل |
| is_active | bool | |

### ContactChannel [T]
| Field | Type | Notes |
|---|---|---|
| contact | FK Contact | |
| type | choice | whatsapp / phone / email |
| value | str | الأرقام بتتخزن E.164 (`+201060777030`) |
| label | str, null | "شخصي" / "الشغل" |
| is_primary | bool | واحد primary لكل type |
| is_verified | bool | اتبعتله قبل كده بنجاح |
| unique | (contact, type, value) | |

---

## Catalog

### Category [T]
`name_ar`, `name_en`, `sort_order`, `is_active`
(أدوات المائدة الخشبية، الشاليموات، الأكواب الورقية، ...)

### Unit [T]
`name_ar` (باكت، كرتونة، رزمة، قطعة، رول، زجاجة، طبق، كيلو)، `name_en`, `is_active`

### Product [T]
| Field | Type | Notes |
|---|---|---|
| category | FK Category | |
| name_ar | str | سكينة خشب ×100 |
| name_en | str, null | لو احتجنا عرض بالإنجليزي |
| sku | str, null | كود داخلي اختياري |
| unit | FK Unit | الوحدة الافتراضية |
| reference_price | decimal, null | **مرجع بس**، مش السعر النهائي |
| image | image, null | |
| description | text, null | |
| sort_order | int | ترتيبه جوه القسم |
| is_active | bool | التعطيل بدل الحذف |
| index | (organization, is_active, category) + trigram على name_ar |

قاعدة: المنتج **مش بيتحذف فعليًا** لو ظهر في أي عرض. بس بيتعطّل.

---

## Quotations

### Quotation [T]
| Field | Type | Notes |
|---|---|---|
| number | str | AB-2026/09-01 — بيتولّد عند الـfinalize مش عند إنشاء المسودة |
| revision | int | 0، 1، 2 ... |
| parent_quotation | FK self, null | لو ده تعديل على عرض اتبعت |
| status | choice | draft → finalized → sent → accepted / rejected / expired / cancelled |
| reviewed_by / reviewed_at | FK User / datetime, null | آخر مراجعة للـPDF النهائي (شرط للإرسال، راجعي 06) |
| customer | FK Customer | PROTECT |
| contact | FK Contact | PROTECT |
| issue_date | date | |
| valid_until | date | issue_date + default_validity_days |
| currency | str | EGP |
| prices_include_vat | bool | snapshot من الإعدادات |
| vat_rate | decimal | snapshot |
| intro_text | text | قابل للتعديل لكل عرض |
| terms | JSON list | قابل للتعديل لكل عرض |
| internal_notes | text | مش بتظهر للعميل |
| items_count | int | للعرض في القوائم |
| customer_snapshot / contact_snapshot | JSON | أسماء وقت الإصدار |
| pdf_file | file, null | |
| pdf_generated_at | datetime, null | |
| created_by / finalized_by | FK User | |
| finalized_at / sent_at | datetime, null | |
| unique | (organization, number, revision) | |
| index | (organization, status, -issue_date), (organization, customer) |

**قاعدة الـimmutability:** أول ما العرض يبقى `finalized` محدش يعدّل عليه. أي تعديل = "إنشاء نسخة معدّلة" → Revision جديدة بنفس الرقم + `-R1`.

### QuotationItem [T]
| Field | Type | Notes |
|---|---|---|
| quotation | FK Quotation CASCADE | |
| product | FK Product, null | null = صنف خاص مش في الكتالوج |
| category_name | str | snapshot للتجميع في الـPDF |
| description | str | snapshot لاسم المنتج (قابل للتعديل في العرض ده بس) |
| unit_name | str | snapshot |
| unit_price | decimal | سعر الوحدة المعروضة (الكرتونة/الباكت/...) في العرض ده بس |
| sort_order | int | |

**مفيش كميات ولا إجماليات:** عرض السعر = قائمة أسعار. كل صنف بسعر وحدته فقط.

الـsnapshot fields بتضمن إن تغيير اسم منتج بعد سنة مش هيغيّر عروض قديمة.

---

## Messaging

### SendingChannelConfig [T]
| Field | Type | Notes |
|---|---|---|
| type | choice | email / whatsapp |
| is_active | bool | |
| display_name | str | |
| config_encrypted | text | SMTP host/port/user/password أو OpenWA session id — **متشفرة بـFernet** |
| sender_identity | str | الإيميل أو الرقم اللي بيظهر (للعرض بس) |
| status | choice | connected / disconnected / needs_qr / error |
| last_checked_at | datetime | |

### Delivery [T]
| Field | Type | Notes |
|---|---|---|
| quotation | FK Quotation | |
| channel | choice | email / whatsapp |
| recipient | str | الرقم أو الإيميل الفعلي |
| contact_channel | FK ContactChannel, null | |
| message_text | text | الرسالة اللي اتبعتت فعلًا |
| status | choice | queued → sending → sent → delivered → read / failed |
| provider_message_id | str, null | |
| error_message | text, null | |
| attempts | int | |
| sent_by | FK User | |
| review_confirmed | bool | المرسل أكّد "راجعت العرض" قبل الإرسال |
| queued_at / sent_at / delivered_at / read_at | datetime | |

---

## Audit

### AuditEvent [T]
`actor` (FK User, null)، `action` (login, login_failed, quote.finalized, quote.sent, settings.changed, ...)،
`target_type`، `target_id`، `summary`، `metadata` (JSON)، `ip_address`، `user_agent`، `created_at`.
Append-only: مفيش update أو delete من الواجهة.

بالإضافة لـ`django-simple-history` على: Customer, Contact, ContactChannel, Product, OrganizationSettings
(عشان نعرف مين غيّر إيه وإمتى، ونرجّع لو حصل غلط).
