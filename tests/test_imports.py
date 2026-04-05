def test_main_import() -> None:
    import main  # noqa: F401


def test_integrations_import() -> None:
    import integrations  # noqa: F401


def test_binance_package_import() -> None:
    import integrations.binance  # noqa: F401


def test_bnb_fx_package_import() -> None:
    import services.bnb_fx  # noqa: F401


def test_crypto_fx_package_import() -> None:
    import crypto_fx  # noqa: F401
