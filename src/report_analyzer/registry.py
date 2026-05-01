from __future__ import annotations

from integrations.crypto.binance.futures_pnl_analyzer_definition import ANALYZER as BINANCE_FUTURES_ANALYZER
from integrations.crypto.coinbase.analyzer_definition import ANALYZER as COINBASE_ANALYZER
from integrations.crypto.kraken.analyzer_definition import ANALYZER as KRAKEN_ANALYZER
from integrations.fund.finexify.analyzer_definition import ANALYZER as FINEXIFY_ANALYZER
from integrations.ibkr.analyzer_definition import ANALYZER as IBKR_ANALYZER
from integrations.p2p.afranga.analyzer_definition import ANALYZER as AFRANGA_ANALYZER
from integrations.p2p.bondora_go_grow.analyzer_definition import ANALYZER as BONDORA_GO_GROW_ANALYZER
from integrations.p2p.estateguru.analyzer_definition import ANALYZER as ESTATEGURU_ANALYZER
from integrations.p2p.iuvo.analyzer_definition import ANALYZER as IUVO_ANALYZER
from integrations.p2p.lendermarket.analyzer_definition import ANALYZER as LENDERMARKET_ANALYZER
from integrations.p2p.robocash.analyzer_definition import ANALYZER as ROBOCASH_ANALYZER
from integrations.shared.contracts import AnalyzerDefinition


BUILTIN_ANALYZERS: list[AnalyzerDefinition] = [
    IBKR_ANALYZER,
    BINANCE_FUTURES_ANALYZER,
    COINBASE_ANALYZER,
    KRAKEN_ANALYZER,
    FINEXIFY_ANALYZER,
    AFRANGA_ANALYZER,
    BONDORA_GO_GROW_ANALYZER,
    ESTATEGURU_ANALYZER,
    IUVO_ANALYZER,
    LENDERMARKET_ANALYZER,
    ROBOCASH_ANALYZER,
]


def list_analyzers() -> list[str]:
    return sorted(analyzer.alias for analyzer in BUILTIN_ANALYZERS)
