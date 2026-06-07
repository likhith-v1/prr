"""Sample buggy file for testing prr."""

import os


def div(a, b):
    """Divide a by b — missing zero-guard."""
    return a / b


def greet(name):
    """Greet someone — typo in variable name."""
    print("hi " + nam)


def read_secret():
    """Read a 'secret' via hardcoded path — style smell."""
    path = "/etc/secret_key"
    with open(path) as f:
        return f.read()


def risky(data: list):
    """Mutable default argument smell — actually uses None pattern correctly."""
    result = []
    for item in data:
        result.append(str(item))
    return result


def catch_all():
    """Bare except — swallows everything including KeyboardInterrupt."""
    try:
        x = int("abc")
    except:
        pass
