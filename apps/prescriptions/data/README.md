# Egyptian drug database (bundled)

`egyptian-drugs.csv` — 25,070 medicines and pharmacy products registered/sold in Egypt (data dated **June 2026**).

- **Source:** <https://github.com/karem505/egyptian-drug-database> (`data/egyptian-drugs.csv`), downloaded 2026-10-05.
- **License:** Creative Commons Zero v1.0 Universal (CC0-1.0) — public-domain dedication; copying, modifying and
  redistributing (including inside DigiClinic) needs no permission or attribution.
  <https://creativecommons.org/publicdomain/zero/1.0/>
- **Columns:** `commercial_name_en, commercial_name_ar, scientific_name, manufacturer, drug_class, route, price_egp`.
  `commercial_name_ar` is a phonetic transliteration of the English trade name (a search alias, not the officially
  registered Arabic mark).
- **Disclaimer (from the source):** for informational and software-development purposes only; verify against the
  Egyptian Drug Authority (EDA / هيئة الدواء المصرية) before clinical use. No warranty of accuracy or completeness.

Imported by `manage.py import_egyptian_drugs` (drugs get `source="eg-db"`; `drug_class`/`route` are mapped to the
clinic's categories by `apps/prescriptions/eg_classes.py`). Prices are not imported (they change too often to be
useful on a prescription).

## Updating
Download the newer CSV from the source repo over this file, check the columns are unchanged, then run
`manage.py import_egyptian_drugs --update`. Existing drugs are refreshed (name, ingredients, form, manufacturer,
Arabic alias) and get any new categories; categories the doctors added by hand are kept, and drugs added by the
clinic itself are never touched.
