from calculator import divide


def test_divide() -> None:
    assert divide(9, 3) == 3


def test_divide_by_zero_returns_zero() -> None:
    assert divide(9, 0) == 0
