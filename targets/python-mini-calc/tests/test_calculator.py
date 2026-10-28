"""Unit tests for the python-mini-calc benchmark target."""

import pytest
from src.calculator import Calculator


def test_add():
    calc = Calculator()
    assert calc.add(2, 3) == 5


def test_divide():
    calc = Calculator()
    assert calc.divide(10, 2) == 5
    with pytest.raises(ValueError):
        calc.divide(10, 0)
