# Sample file with Git merge conflicts, for trying out the CLI:
#   python -m src.cli examples/sample_conflict.py
# This file is intentionally not valid Python until the conflicts are resolved.


def greet(name):
<<<<<<< HEAD
    return f"Hello, {name}!"
=======
    return f"Hi, {name}! Welcome back."
>>>>>>> feature/greeting


def apply_discount(price, rate):
<<<<<<< HEAD
    if rate < 0 or rate > 1:
        raise ValueError("rate must be between 0 and 1")
    return price * (1 - rate)
=======
    discounted = price * (1 - rate)
    return round(discounted, 2)
>>>>>>> feature/rounding
