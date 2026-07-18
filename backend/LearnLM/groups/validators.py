"""
groups.validators — custom Django password validators.

Django's built-in validators (length, common-password, similarity,
all-numeric) say nothing about character-class complexity. This adds the
one the product requires: at least one uppercase letter, one digit, and
one symbol.
"""

import re

from django.core.exceptions import ValidationError


class PasswordComplexityValidator:
    UPPERCASE_RE = re.compile(r"[A-Z]")
    DIGIT_RE = re.compile(r"[0-9]")
    SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")

    def validate(self, password, user=None):
        missing = []
        if not self.UPPERCASE_RE.search(password):
            missing.append("one uppercase letter")
        if not self.DIGIT_RE.search(password):
            missing.append("one number")
        if not self.SYMBOL_RE.search(password):
            missing.append("one symbol")

        if missing:
            raise ValidationError(
                "Password must contain at least " + ", ".join(missing) + ".",
                code="password_missing_complexity",
            )

    def get_help_text(self):
        return "Your password must contain at least one uppercase letter, one number, and one symbol."
