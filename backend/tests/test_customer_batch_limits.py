import pytest
from pydantic import ValidationError

from app.modules.customer.tags_schema import CustomerBatchTag, CustomerBatchTransfer


@pytest.mark.parametrize("model", [CustomerBatchTag, CustomerBatchTransfer])
def test_customer_batch_requests_limit_customer_ids(model):
    model(customer_ids=list(range(200)), **({"tag_ids": [1]} if model is CustomerBatchTag else {}))
    with pytest.raises(ValidationError):
        model(customer_ids=list(range(201)), **({"tag_ids": [1]} if model is CustomerBatchTag else {}))
