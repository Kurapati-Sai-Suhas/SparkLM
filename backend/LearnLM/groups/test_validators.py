import pytest
from django.core.exceptions import ValidationError

from groups.validators import PasswordComplexityValidator


def test_valid_password_passes():
    PasswordComplexityValidator().validate("Str0ng!Pass")  # must not raise


@pytest.mark.parametrize("password", [
    "alllowercase123!",  # no uppercase
    "NoDigitsHere!",      # no digit
    "NoSymbols123",       # no symbol
])
def test_invalid_passwords_raise(password):
    with pytest.raises(ValidationError):
        PasswordComplexityValidator().validate(password)


def test_error_message_names_every_missing_class():
    with pytest.raises(ValidationError) as excinfo:
        PasswordComplexityValidator().validate("alllowercase")  # no upper, no digit, no symbol
    message = str(excinfo.value)
    assert "uppercase" in message and "number" in message and "symbol" in message
