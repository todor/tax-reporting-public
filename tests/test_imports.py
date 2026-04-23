def test_main_import() -> None:
    import main  # noqa: F401


def test_integrations_import() -> None:
    import integrations  # noqa: F401


def test_binance_package_import() -> None:
    import integrations.crypto.binance  # noqa: F401


def test_coinbase_package_import() -> None:
    import integrations.crypto.coinbase  # noqa: F401


def test_kraken_package_import() -> None:
    import integrations.crypto.kraken  # noqa: F401


def test_finexify_package_import() -> None:
    import integrations.fund.finexify  # noqa: F401


def test_afranga_package_import() -> None:
    import integrations.p2p.afranga  # noqa: F401


def test_estateguru_package_import() -> None:
    import integrations.p2p.estateguru  # noqa: F401


def test_lendermarket_package_import() -> None:
    import integrations.p2p.lendermarket  # noqa: F401


def test_iuvo_package_import() -> None:
    import integrations.p2p.iuvo  # noqa: F401


def test_robocash_package_import() -> None:
    import integrations.p2p.robocash  # noqa: F401


def test_bondora_go_grow_package_import() -> None:
    import integrations.p2p.bondora_go_grow  # noqa: F401


def test_bnb_fx_package_import() -> None:
    import services.bnb_fx  # noqa: F401


def test_crypto_fx_package_import() -> None:
    import services.crypto_fx  # noqa: F401


def test_pdf_reader_import() -> None:
    import services.pdf_reader  # noqa: F401
