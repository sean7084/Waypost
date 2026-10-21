import io
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from accounts.models import User
from assets.models import Asset, AssetBrand, AssetCategory
from companies.models import Company, CompanyUser, Location


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def make_csv_file(name, headers, rows):
    csv_content = io.StringIO()
    csv_content.write(",".join(headers) + "\n")
    for row in rows:
        values = []
        for value in row:
            text = "" if value is None else str(value)
            if "," in text or '"' in text or "\n" in text:
                text = '"' + text.replace('"', '""') + '"'
            values.append(text)
        csv_content.write(",".join(values) + "\n")
    return SimpleUploadedFile(name, csv_content.getvalue().encode("utf-8"), content_type="text/csv")


def get_or_create_admin_user():
    user, created = User.objects.get_or_create(
        username="rollback_verify_admin",
        defaults={
            "email": "rollback_verify_admin@example.com",
            "is_superuser": True,
            "is_staff": True,
            "is_active": True,
        },
    )
    if created:
        user.set_password("Passw0rd!123")
        user.save()
    else:
        changed = False
        if not user.is_superuser:
            user.is_superuser = True
            changed = True
        if not user.is_staff:
            user.is_staff = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if changed:
            user.save(update_fields=["is_superuser", "is_staff", "is_active"])
    return user


def verify_company_import(client, suffix):
    before = Company.objects.count()
    code = f"VRF{suffix[:8].upper()}"
    upload = make_csv_file(
        "company_verify.csv",
        ["name", "code", "status"],
        [[f"Verify Company {suffix}", code, "active"]],
    )

    resp_preview = client.post(reverse("companies:company_import_csv"), {"file": upload})
    assert_true(resp_preview.status_code == 200, "Company import preview failed")
    assert_true(b"Confirm Import" in resp_preview.content, "Company preview page missing confirm button")

    resp_confirm = client.post(reverse("companies:company_import_csv"), {"confirm_import": "1"})
    assert_true(resp_confirm.status_code == 200, "Company import confirm failed")

    created = Company.objects.filter(code=code).exists()
    assert_true(created, "Company row was not created after confirm")
    after_import = Company.objects.count()
    assert_true(after_import == before + 1, f"Company count mismatch after import: {before} -> {after_import}")

    rollback_url = reverse("companies:company_import_rollback")
    resp_rollback = client.post(rollback_url)
    assert_true(resp_rollback.status_code in (302, 301), "Company rollback did not redirect")

    after_rollback = Company.objects.count()
    assert_true(after_rollback == before, f"Company count mismatch after rollback: expected {before}, got {after_rollback}")
    assert_true(not Company.objects.filter(code=code).exists(), "Company row still exists after rollback")

    return {
        "before": before,
        "after_import": after_import,
        "after_rollback": after_rollback,
    }


def verify_location_import(client, suffix, base_company):
    before = Location.objects.filter(company=base_company).count()
    location_code = f"LOC-{suffix[:6].upper()}"
    upload = make_csv_file(
        "location_verify.csv",
        ["company", "name", "code", "location_type", "status"],
        [[base_company.code, f"Verify Location {suffix}", location_code, "warehouse", "active"]],
    )

    resp_preview = client.post(reverse("companies:location_import_csv"), {"file": upload})
    assert_true(resp_preview.status_code == 200, "Location import preview failed")
    assert_true(b"Confirm Import" in resp_preview.content, "Location preview page missing confirm button")

    resp_confirm = client.post(reverse("companies:location_import_csv"), {"confirm_import": "1"})
    assert_true(resp_confirm.status_code == 200, "Location import confirm failed")

    created = Location.objects.filter(company=base_company, code=location_code).exists()
    assert_true(created, "Location row was not created after confirm")
    after_import = Location.objects.filter(company=base_company).count()
    assert_true(after_import == before + 1, f"Location count mismatch after import: {before} -> {after_import}")

    resp_rollback = client.post(reverse("companies:location_import_rollback"))
    assert_true(resp_rollback.status_code in (302, 301), "Location rollback did not redirect")

    after_rollback = Location.objects.filter(company=base_company).count()
    assert_true(after_rollback == before, f"Location count mismatch after rollback: expected {before}, got {after_rollback}")
    assert_true(not Location.objects.filter(company=base_company, code=location_code).exists(), "Location row still exists after rollback")

    return {
        "before": before,
        "after_import": after_import,
        "after_rollback": after_rollback,
    }


def verify_contact_import(client, suffix, base_company):
    before = CompanyUser.objects.filter(company=base_company).count()
    name = f"Verify Contact {suffix}"
    email = f"verify.contact.{suffix}@example.com"
    upload = make_csv_file(
        "contact_verify.csv",
        ["company", "name", "role", "status", "work_email"],
        [[base_company.code, name, "employee", "active", email]],
    )

    resp_preview = client.post(reverse("companies:company_contact_import_csv"), {"file": upload})
    assert_true(resp_preview.status_code == 200, "Contact import preview failed")
    assert_true(b"Confirm Import" in resp_preview.content, "Contact preview page missing confirm button")

    resp_confirm = client.post(reverse("companies:company_contact_import_csv"), {"confirm_import": "1"})
    assert_true(resp_confirm.status_code == 200, "Contact import confirm failed")

    created = CompanyUser.objects.filter(company=base_company, name=name, work_email=email).exists()
    assert_true(created, "Company contact row was not created after confirm")
    after_import = CompanyUser.objects.filter(company=base_company).count()
    assert_true(after_import == before + 1, f"Contact count mismatch after import: {before} -> {after_import}")

    resp_rollback = client.post(reverse("companies:company_contact_import_rollback"))
    assert_true(resp_rollback.status_code in (302, 301), "Contact rollback did not redirect")

    after_rollback = CompanyUser.objects.filter(company=base_company).count()
    assert_true(after_rollback == before, f"Contact count mismatch after rollback: expected {before}, got {after_rollback}")
    assert_true(not CompanyUser.objects.filter(company=base_company, name=name, work_email=email).exists(), "Company contact row still exists after rollback")

    return {
        "before": before,
        "after_import": after_import,
        "after_rollback": after_rollback,
    }


def verify_asset_import(client, suffix, base_company):
    category_name = f"VerifyCategory{suffix[:6]}"
    brand_name = f"VerifyBrand{suffix[:6]}"
    category = AssetCategory.objects.create(name=category_name)
    brand = AssetBrand.objects.create(name=brand_name)

    before = Asset.objects.filter(company=base_company).count()
    serial = f"VRF-SN-{suffix[:10]}"

    upload = make_csv_file(
        "asset_verify.csv",
        ["category", "brand", "serial_number", "description", "status", "condition"],
        [[category.name, brand.name, serial, f"Rollback verify asset {suffix}", "available", "good"]],
    )

    resp_import = client.post(
        reverse("assets:asset_import"),
        {
            "file": upload,
            "company": str(base_company.id),
            "asset_number_mode": "auto",
            "asset_number_prefix": "",
            "duplicate_handling": "skip",
        },
    )
    assert_true(resp_import.status_code == 200, "Asset import POST failed")

    created = Asset.objects.filter(company=base_company, serial_number=serial).exists()
    assert_true(created, "Asset row was not created")
    after_import = Asset.objects.filter(company=base_company).count()
    assert_true(after_import == before + 1, f"Asset count mismatch after import: {before} -> {after_import}")

    resp_rollback = client.post(reverse("assets:asset_import_rollback"))
    assert_true(resp_rollback.status_code in (302, 301), "Asset rollback did not redirect")

    after_rollback = Asset.objects.filter(company=base_company).count()
    assert_true(after_rollback == before, f"Asset count mismatch after rollback: expected {before}, got {after_rollback}")
    assert_true(not Asset.objects.filter(company=base_company, serial_number=serial).exists(), "Asset row still exists after rollback")

    category.delete()
    brand.delete()

    return {
        "before": before,
        "after_import": after_import,
        "after_rollback": after_rollback,
    }


def main():
    suffix = uuid.uuid4().hex[:12]
    user = get_or_create_admin_user()

    client = Client(HTTP_HOST='localhost')
    logged_in = client.force_login(user)
    assert_true(logged_in is None, "Failed to authenticate test client")

    base_company = Company.objects.create(
        name=f"Verify Base Company {suffix}",
        code=f"VBC{suffix[:8].upper()}",
        status=Company.CompanyStatus.ACTIVE,
    )

    results = {}
    try:
        results["company"] = verify_company_import(client, suffix)
        results["location"] = verify_location_import(client, suffix, base_company)
        results["company_contact"] = verify_contact_import(client, suffix, base_company)
        results["asset"] = verify_asset_import(client, suffix, base_company)
    finally:
        CompanyUser.objects.filter(company=base_company).delete()
        Location.objects.filter(company=base_company).delete()
        Asset.objects.filter(company=base_company).delete()
        Company.objects.filter(id=base_company.id).delete()

    print("VERIFICATION PASSED")
    for module, stats in results.items():
        print(f"- {module}: before={stats['before']}, after_import={stats['after_import']}, after_rollback={stats['after_rollback']}")


main()
