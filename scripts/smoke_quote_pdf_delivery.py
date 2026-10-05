# .venv\Scripts\Activate.ps1 ; python manage.py shell -c "exec(open('scripts/smoke_quote_pdf_delivery.py', encoding='utf-8').read())"

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from assets.models import AssetBrand, AssetModel
from companies.models import Company, CompanyUser, Division, Location
from products.models import ProductPrice
from quotations.models import Quotation, QuotationItem


def check(name, condition):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")


def run():
    suffix = uuid4().hex[:8]
    today = date.today()

    user_model = get_user_model()
    user = user_model.objects.filter(is_superuser=True).first() or user_model.objects.first()
    check("smoke user exists", user is not None)
    if user is None:
        return

    client = Client(HTTP_HOST="localhost")
    client.force_login(user)

    company = Company.objects.create(
        name=f"Smoke Company {suffix}",
        code=f"SMK{suffix[:4].upper()}",
        status=Company.CompanyStatus.ACTIVE,
    )
    division = Division.objects.create(company=company, name=f"Division {suffix}", code=f"DIV{suffix[:3].upper()}")
    location = Location.objects.create(
        company=company,
        division=division,
        name=f"Warehouse {suffix}",
        code=f"LOC{suffix[:4].upper()}",
        status=Location.LocationStatus.ACTIVE,
        location_type=Location.LocationType.WAREHOUSE,
        address_line1="Smoke Street 1",
        city="Shanghai",
        state_province="Shanghai",
    )

    company_user = CompanyUser.objects.create(
        user=user,
        company=company,
        role=CompanyUser.CompanyRole.ADMIN,
        location=location,
        status=CompanyUser.UserStatus.ACTIVE,
        work_phone="13800000000",
        work_email="smoke@example.com",
    )
    company.primary_contact_company_user = company_user
    company.save(update_fields=["primary_contact_company_user"])

    brand = AssetBrand.objects.create(name=f"Brand {suffix}", code=f"BR{suffix[:6].upper()}")
    model = AssetModel.objects.create(brand=brand, name=f"Model {suffix}", model_number=f"M-{suffix}")
    product_price = ProductPrice.objects.create(
        brand=brand,
        model=model,
        unit="PCS",
        price_without_tax=Decimal("100.00"),
        tax_rate=Decimal("13.00"),
        is_current=True,
    )

    quotation = Quotation.objects.create(
        customer=company,
        quotation_date=today,
        valid_until=today + timedelta(days=15),
        attn=company_user.user.get_display_name(),
        tel=company_user.work_phone,
        status=Quotation.QuotationStatus.DRAFT,
    )
    QuotationItem.objects.create(
        quotation=quotation,
        product_price=product_price,
        quantity=1,
        user_brand="Kering",
        user_name="Store A",
    )
    quotation.refresh_from_db()
    check("quotation created", quotation.total_with_tax > 0)

    pdf_response = client.get(reverse("quotations:pdf", args=[quotation.pk]))
    check("quotation pdf endpoint reachable", pdf_response.status_code in (200, 302))
    print(f"INFO: quotation pdf status={pdf_response.status_code}")

    confirm_response = client.get(reverse("quotations:confirm", args=[quotation.pk]))
    check("quotation confirmed", confirm_response.status_code == 302)

    convert_response = client.post(reverse("quotations:convert_to_purchase", args=[quotation.pk]))
    check("purchase conversion", convert_response.status_code == 302)

    from purchases.models import PurchaseOrder

    purchase_order = PurchaseOrder.objects.filter(quotation=quotation).first()
    check("purchase order exists", purchase_order is not None)
    if purchase_order is None:
        return

    po_item = purchase_order.items.first()
    check("purchase order item exists", po_item is not None)
    if po_item is None:
        return

    receive_payload = {
        "location": str(location.pk),
        "received_by": user.username or "smoke-user",
        "notes": "focused smoke receipt",
        f"qty_{po_item.pk}": "1",
        f"serials_{po_item.pk}": f"SN-{suffix}",
    }
    receive_response = client.post(reverse("purchases:receive", args=[purchase_order.pk]), data=receive_payload)
    check("purchase receive", receive_response.status_code == 302)

    from assets.models import Asset

    asset = Asset.objects.filter(source_quotation=quotation, status=Asset.AssetStatus.AVAILABLE).first()
    check("deliverable asset available", asset is not None)
    if asset is None:
        return

    delivery_payload = {
        "delivery_date": str(today),
        "receiver_name": company_user.user.get_display_name(),
        "receiver_phone": company_user.work_phone,
        "delivery_address": location.get_full_address(),
        "delivery_method": "Delivery",
        "remarks": "focused smoke delivery",
        "selected_assets": [str(asset.pk)],
    }

    delivery_response = client.post(reverse("deliveries:create_from_quotation", args=[quotation.pk]), data=delivery_payload)
    print(f"INFO: delivery create status={delivery_response.status_code}")
    check("delivery creation", delivery_response.status_code == 302)

    from deliveries.models import DeliveryOrder

    delivery = DeliveryOrder.objects.filter(quotation=quotation).order_by("-created_at").first()
    check("delivery record exists", delivery is not None)

    print("SUMMARY: focused smoke executed for quotation -> pdf endpoint -> delivery creation.")


run()
