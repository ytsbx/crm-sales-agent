"""Money, literal PDF text and health checks that protect production flows."""

import io
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image

from app.core.money import line_amount
from app.modules.pricing.service import discount_percentage
from app.modules.quote.pdf import render_quote_pdf
from scripts import wait_backend


@pytest.mark.parametrize("quantity,price,amount", [
    ("1", "33.3333", "33.33"),
    ("1", "0.005", "0.01"),
    ("3", "0.005", "0.02"),
    ("1.235", "150", "185.25"),
])
def test_stored_line_amount_uses_cents_and_postgres_rounding(quantity, price, amount):
    assert line_amount(Decimal(quantity), Decimal(price)) == Decimal(amount)


@pytest.mark.parametrize("price,standard,discount", [
    (Decimal("50"), Decimal("100"), Decimal("50")),
    (Decimal("100"), Decimal("100"), Decimal(0)),
    (Decimal("120"), Decimal("100"), Decimal(0)),
    (Decimal("50"), None, Decimal(0)),
    (None, Decimal("100"), Decimal(0)),
])
def test_discount_missing_reference_does_not_invent_a_price(price, standard, discount):
    assert discount_percentage(price, standard) == discount


@pytest.mark.parametrize("field", [
    "customer_name", "company_name", "opportunity_title", "owner_name", "remark",
    "payment_terms", "delivery_terms", "valid_until", "quote_no", "status_label",
])
def test_pdf_user_text_cannot_load_local_images(tmp_path, field):
    private = tmp_path / "private.png"
    Image.new("RGB", (12, 12), (121, 15, 47)).save(private)
    data = {"quote_no": "Q-safe", "items": [], "charges": [], "total_amount": 100}
    data[field] = f'<img src="{private}" width="12" height="12"/>'
    result = render_quote_pdf(data)
    assert result.startswith(b"%PDF")
    assert b"/Subtype /Image" not in result


@pytest.mark.parametrize("payload,expected", [
    ({"code": 0, "data": {"status": "up", "database": "ok"}}, True),
    ({"code": 0, "data": {"status": "degraded", "database": "error"}}, False),
    ({"code": 0, "data": {"status": "up", "database": "error"}}, False),
    ({"code": 50001, "data": {"status": "up", "database": "ok"}}, False),
    ({"code": 0, "data": None}, False),
    ([], False),
])
def test_health_requires_healthy_database_even_when_http_is_200(monkeypatch, payload, expected):
    opener = SimpleNamespace(open=lambda *a, **kw: io.BytesIO(json.dumps(payload).encode()))
    monkeypatch.setattr(wait_backend.urllib.request, "build_opener", lambda *a: opener)
    assert wait_backend.is_ready("http://127.0.0.1:8069") is expected
