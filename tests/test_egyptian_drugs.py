# ruff: noqa: E501  -- sample CSV rows are kept one per line
import csv
import io
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.prescriptions import matching, services
from apps.prescriptions.eg_classes import KNOWN, categories_for
from apps.prescriptions.management.commands.import_egyptian_drugs import DEFAULT_CSV, form_for
from apps.prescriptions.models import Drug, DrugCategory

HEADER = "commercial_name_en,commercial_name_ar,scientific_name,manufacturer,drug_class,route,price_egp\n"
SAMPLE = HEADER + "\n".join([
    "AUGMENTIN 1 GM 14 F.C.TABS.,أوجمنتين,AMOXICILLIN+CLAVULANIC ACID,GSK,PENICILLINS.PENICILLIN WITH B-LACTAMASE INHIBITOR,ORAL.SOLID,120.0",
    "AUGMENTIN 156 MG/5 ML SUSP. 80 ML,أوجمنتين,AMOXICILLIN+CLAVULANIC ACID,GSK,PENICILLINS.PENICILLIN WITH B-LACTAMASE INHIBITOR,ORAL.LIQUID,60.0",
    "PANADOL EXTRA 24 F.C. TABS.,بانادول إكسترا,PARACETAMOL(ACETAMINOPHEN)+CAFFEINE,GSK,MILD ANALGESIC,ORAL.SOLID,40.0",
    "CATAFLAM 50 MG 20 SUGAR C.TABS.,كاتافلام,DICLOFENAC POTASSIUM,NOVARTIS,NSAID.ACETIC ACID DERIVATIVES,ORAL.SOLID,50.0",
    "ELIQUIS 5 MG 20 F.C.TABS.,إليكويس,APIXABAN,PFIZER,ANTIPLATLET.DIRECT FACTOR XA INHIBITOR,ORAL.SOLID,500.0",
    "NICE HAIR SHAMPOO 200 ML,نايس,SHAMPOO,X,HAIR CARE,UNKNOWN,30.0",
    "PANADOL EXTRA 24 F.C. TABS.,بانادول إكسترا,DUPLICATE ROW,GSK,MILD ANALGESIC,ORAL.SOLID,40.0",
])  # fmt: skip


@pytest.fixture
def sample_csv(tmp_path):
    p = tmp_path / "eg.csv"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def _import(org, path, *extra):
    out = io.StringIO()
    call_command("import_egyptian_drugs", "--csv", str(path), "--org", org.slug, *extra, stdout=out)
    return out.getvalue()


def test_bundled_file_is_present_and_has_the_expected_columns():
    with Path(DEFAULT_CSV).open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        assert set(reader.fieldnames) >= {"commercial_name_en", "commercial_name_ar", "scientific_name",
                                         "manufacturer", "drug_class", "route"}  # fmt: skip
        assert sum(1 for _ in reader) > 20000


def test_class_mapping_only_produces_the_clinics_category_names():
    with Path(DEFAULT_CSV).open(encoding="utf-8-sig") as fh:
        produced = {
            c for r in csv.DictReader(fh) for c in categories_for(r["drug_class"], r["route"], r["scientific_name"])
        }
    assert produced and produced <= KNOWN  # every button name a drug can land in exists in the presets
    assert categories_for("ANTI-HYPERTENSIVE.ACE INHIBITOR", "ORAL.SOLID")[:2] == [
        "Antihypertensives",
        "ACE Inhibitors",
    ]
    assert "ACE Inhibitors" not in categories_for("SKIN CARE.SURFACE CLEANSER", "TOPICAL")  # "ACE" as a word only


def test_dosage_form_from_name_then_route():
    assert form_for("AUGMENTIN 156 MG/5 ML SUSP. 80 ML", "ORAL.LIQUID") == "شراب"
    assert form_for("X OPHTHALMIC SOLUTION 5 ML", "EYE") == "نقط عين"  # not "syrup" because of "SOLUTION"
    assert form_for("CEFOTAX 1 GM VIAL", "INJECTION") == "حقن"
    assert form_for("SOMETHING", "RECTAL") == "لبوس"


def test_import_creates_drugs_with_categories_and_is_idempotent(org_a, sample_csv):
    assert "6 added" in _import(org_a, sample_csv)
    aug = Drug.objects.get(organization=org_a, name="AUGMENTIN 156 MG/5 ML SUSP. 80 ML")
    assert (aug.form, aug.manufacturer, aug.source) == ("شراب", "GSK", "eg-db")
    assert aug.aliases_ar == ["اوجمنتين"]  # normalized Arabic alias → Arabic search + voice
    assert {"Antibiotics", "Penicillins", "Pediatric Antibiotics"} <= set(aug.categories.values_list("name", flat=True))
    shampoo = Drug.objects.get(organization=org_a, name="NICE HAIR SHAMPOO 200 ML")
    assert not shampoo.categories.exists()  # cosmetics: searchable, but under no category button
    assert "0 added" in _import(org_a, sample_csv)


def test_update_refreshes_imported_drugs_but_never_clinic_ones(org_a, sample_csv):
    Drug.objects.create(organization=org_a, name="CATAFLAM 50 MG 20 SUGAR C.TABS.", generic_name="my own text")
    _import(org_a, sample_csv)
    eliquis = Drug.objects.get(organization=org_a, name="ELIQUIS 5 MG 20 F.C.TABS.")
    manual = DrugCategory.objects.create(organization=org_a, name="Stroke-related medications")
    eliquis.categories.add(manual)
    Drug.objects.filter(pk=eliquis.pk).update(manufacturer="old")
    _import(org_a, sample_csv, "--update")
    eliquis.refresh_from_db()
    assert eliquis.manufacturer == "PFIZER"
    assert set(eliquis.categories.values_list("name", flat=True)) == {"Anticoagulants", "Stroke-related medications"}
    assert Drug.objects.get(organization=org_a, name="CATAFLAM 50 MG 20 SUGAR C.TABS.").generic_name == "my own text"


def test_search_and_voice_matching_find_imported_drugs(org_a, sample_csv):
    _import(org_a, sample_csv)
    nsaids = DrugCategory.objects.get(organization=org_a, name="NSAIDs")
    assert [d.name for d in services.search_drugs(org_a, "cat", category=nsaids)] == ["CATAFLAM 50 MG 20 SUGAR C.TABS."]
    # medicines rank above cosmetics for the same prefix
    Drug.objects.create(organization=org_a, name="AUG COSMETIC CREAM", source="eg-db")
    names = [d.name for d in services.search_drugs(org_a, "aug")]
    assert names.index("AUG COSMETIC CREAM") > names.index("AUGMENTIN 1 GM 14 F.C.TABS.")
    [line] = matching.match_text(org_a, "كاتافلام بعد الأكل")
    assert line.candidates[0].drug.name == "CATAFLAM 50 MG 20 SUGAR C.TABS." and line.instructions == "بعد الأكل"


def test_voice_index_is_rebuilt_when_the_catalog_changes(org_a, sample_csv):
    _import(org_a, sample_csv)
    assert not matching.match_text(org_a, "زيثروماكس")[0].candidates
    Drug.objects.create(organization=org_a, name="ZITHROMAX 500 MG 3 TABS.", aliases_ar=["زيثروماكس"])
    assert matching.match_text(org_a, "زيثروماكس")[0].candidates[0].drug.name == "ZITHROMAX 500 MG 3 TABS."


@pytest.mark.parametrize(
    ("drug_class", "route", "must_have", "must_not_have"),
    [
        ("ANTIPLATLET.DIRECT FACTOR XA INHIBITOR", "ORAL.SOLID", {"Anticoagulants"}, {"Antiplatelets"}),
        ("ANTIPLATLET.ADP RECEPTOR BLOCKER", "ORAL.SOLID", {"Antiplatelets"}, {"Anticoagulants"}),
        ("ANALGESIC.NON OPIOID", "ORAL.SOLID", {"Analgesics"}, {"Opioids"}),
        ("ANTIDOTE.OPIOID TOXICITY.OPIOID ANTAGONIST", "INJECTION", set(), {"Opioids"}),
        ("ANTIDOTE.BENZODIAZEPINE OVERDOSES.GABA MODULATORS", "INJECTION", set(), {"Benzodiazepines"}),
        ("ANTI-DIABETIC.INSULIN SENSITIZERS WITH DPP-4 INHIBITOR", "ORAL.SOLID", {"DPP-4 Inhibitors"}, {"Insulins"}),
        ("PEPTIC ULCER.ANTI-HISTAMINE.H2 ANTAGONIST", "ORAL.SOLID", {"H2 Blockers"}, {"Antihistamines"}),
        ("HYPOTHALMIC HORMONES.SOMATOSTATIN ANALOGUES", "INJECTION", set(), {"Statins"}),
        ("ANTIANDROGEN", "ORAL.SOLID", {"Hormonal Therapy"}, {"Testosterone"}),
        ("ESTROGEN ANTAGONIST", "ORAL.SOLID", set(), {"Estrogen", "HRT"}),
        ("ANTITHYROID", "ORAL.SOLID", {"Antithyroid Drugs"}, {"Thyroid Hormones"}),
        ("COD LIVER OIL", "ORAL.LIQUID", set(), {"Liver medications"}),
        ("URINARY INCONTINENCE.ANTISPASMODICS.MUSCARINIC ANTAGONISTS", "ORAL.SOLID",
         {"Urinary Antispasmodics"}, {"Antispasmodics", "GI medications"}),
        ("BRONCHODILATOR.GLUCOCORTICOID STEROID AND  LONG-ACTING B2 AGONIST", "SPRAY",
         {"Inhaled Corticosteroids"}, {"Nasal Steroids"}),
        ("ANTI-DIABETIC.SENSITIZERS.GLITAZONE", "ORAL.SOLID", {"Diabetes medications"}, {"Biguanides"}),
    ],
)  # fmt: skip
def test_clinically_sensitive_mappings(drug_class, route, must_have, must_not_have):
    got = set(categories_for(drug_class, route, ""))
    assert must_have <= got and not (must_not_have & got), got


def test_recategorize_replaces_imported_categories(org_a, sample_csv):
    _import(org_a, sample_csv)
    eliquis = Drug.objects.get(organization=org_a, name="ELIQUIS 5 MG 20 F.C.TABS.")
    wrong = DrugCategory.objects.create(organization=org_a, name="Antiplatelets")
    eliquis.categories.add(wrong)
    _import(org_a, sample_csv, "--recategorize")
    assert set(eliquis.categories.values_list("name", flat=True)) == {"Anticoagulants"}
