def test_package_import() -> None:
    import tax_reporting

    assert tax_reporting.__version__


def test_main_import() -> None:
    import tax_reporting.main  # noqa: F401


def test_integrations_import() -> None:
    import tax_reporting.integrations  # noqa: F401


def test_binance_package_import() -> None:
    import tax_reporting.integrations.binance  # noqa: F401


def test_bnb_fx_package_import() -> None:
    import tax_reporting.services.bnb_fx  # noqa: F401
