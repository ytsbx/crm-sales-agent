"""Regression checks for guarded payment transitions and formal quote pricing."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.modules.payment.model import PaymentRecord
from app.modules.payment.service import ensure_payment_pending
from app.modules.quote.schema import QuoteItemInput, QuoteItemUpdate
from app.modules.quote.service import quote_is_expired


@pytest.mark.parametrize("status", ["confirmed", "rejected", "unknown"])
def test_terminal_payment_cannot_be_operated_again(status):
    record = PaymentRecord(status=status)

    with pytest.raises(AppError) as exc:
        ensure_payment_pending(record)

    assert exc.value.http_status == 400
    assert "只有待财务确认" in exc.value.message


def test_pending_payment_can_be_operated():
    ensure_payment_pending(PaymentRecord(status="pending"))


def test_quote_validity_date_is_inclusive():
    today = date(2026, 10, 4)

    assert quote_is_expired(today, today=today) is False
    assert quote_is_expired(today - timedelta(days=1), today=today) is True
    assert quote_is_expired(None, today=today) is False


@pytest.mark.parametrize("schema", [QuoteItemInput, QuoteItemUpdate])
@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_quote_item_rejects_non_positive_manual_price(schema, price):
    with pytest.raises(ValidationError):
        schema(quoted_price=price)


def test_quote_item_accepts_positive_manual_price():
    assert QuoteItemInput(quoted_price=Decimal("0.01")).quoted_price == Decimal("0.01")
