"""Simple calculator module used for fast Agent Forge harness benchmarking."""


class Calculator:
    """Provides basic arithmetic operations."""

    def add(self, a: float, b: float) -> float:
        return a + b

    def divide(self, a: float, b: float) -> float:
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b
