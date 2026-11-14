from pkg.core import add, multiply


def test_add_returns_the_sum():
    assert add(2, 3) == 5


def test_multiply_returns_the_product():
    assert multiply(2, 3) == 6


def test_multiply_short_circuits_on_zero():
    assert multiply(0, 3) == 0
    assert multiply(2, 0) == 0
