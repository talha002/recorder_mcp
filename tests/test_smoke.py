import importlib


def test_src_package_importable() -> None:
    module = importlib.import_module("src")
    assert module is not None
