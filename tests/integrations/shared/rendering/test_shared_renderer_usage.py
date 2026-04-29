from __future__ import annotations

from integrations.crypto.shared import crypto_outputs
from integrations.fund.shared import fund_outputs
from integrations.ibkr.appendices import declaration_text
from integrations.p2p.shared import appendix6_renderer
from integrations.shared import aggregation
from integrations.shared.rendering import appendix5, appendix6, appendix13, appendix8, appendix9
from integrations.shared.rendering import common


def test_shared_appendix5_renderer_is_reused_across_outputs() -> None:
    assert crypto_outputs.render_appendix5_table2 is appendix5.render_appendix5_table2
    assert fund_outputs.render_appendix5_table2 is appendix5.render_appendix5_table2
    assert aggregation.render_appendix5_table2 is appendix5.render_appendix5_table2


def test_shared_appendix_renderers_are_reused_in_ibkr_and_p2p() -> None:
    assert declaration_text.render_appendix13_part2 is appendix13.render_appendix13_part2
    assert declaration_text.render_appendix6 is appendix6.render_appendix6
    assert declaration_text.render_appendix8 is appendix8.render_appendix8
    assert declaration_text.render_appendix9_part2 is appendix9.render_appendix9_part2
    assert appendix6_renderer.render_appendix6 is appendix6.render_appendix6


def test_common_document_helpers_are_reused_across_outputs() -> None:
    assert crypto_outputs.append_technical_details is common.append_technical_details
    assert fund_outputs.append_technical_details is common.append_technical_details
    assert aggregation.append_technical_details is common.append_technical_details
    assert declaration_text.append_technical_details is common.append_technical_details
    assert appendix6_renderer.append_technical_details is common.append_technical_details
