"""B12-02：参数校验明细不得回显密码/密钥原值。"""

from app.core.errors import _validation_detail_input


def _error(field: str, value):
    return {
        "type": "string_too_long",
        "loc": ("body", field),
        "msg": "String should have at most 72 characters",
        "input": value,
    }


def test_password_value_is_not_echoed():
    assert _validation_detail_input(_error("password", "my-secret-pass")) is None
    assert _validation_detail_input(_error("new_password", "x" * 100)) is None
    assert _validation_detail_input(_error("password_confirm", "y")) is None


def test_secret_and_token_values_are_not_echoed():
    assert _validation_detail_input(_error("token", "tok-123")) is None
    assert _validation_detail_input(_error("api_key", "sk-123")) is None
    assert _validation_detail_input(_error("client_secret", "s")) is None


def test_ordinary_fields_still_echo_input_for_troubleshooting():
    assert _validation_detail_input(_error("quantity", 0)) == "0"
    assert _validation_detail_input(_error("rate", None)) is None
