You are anonymising an Interactive Brokers detailed Activity Statement CSV.

The attached/provided file is a real IBKR Activity Statement. Your task is to produce a new anonymised CSV file that keeps the statement structurally and mathematically valid, while hiding personal information, account identifiers, instrument identities, real position sizes, and real money amounts.

================================================================================
0. USER-EDITABLE CONFIGURATION
================================================================================

Input/output mode:
- If running in a web agent such as ChatGPT, Gemini, Claude web, or similar:
  - Use the attached CSV file as input.
  - Create a downloadable anonymised CSV file as output.
- If running in a local coding agent such as Codex CLI, Claude Code, Gemini CLI, or similar:
  - Use the input/output paths below.
  - If these paths are not valid, ask the user for the correct paths before processing.

Input file:
./real_ibkr_activity_statement.csv

Output file:
./ibkr_activity_statement_anonymised.csv

Configuration:
- SCALING_FACTOR:
  - If I provide a factor, use it.
  - Otherwise choose a random log-uniform factor between 0.0001 and 0.05.
  - The goal is to hide not only exact values, but also the approximate portfolio size / wealth level.
  - Use the same scaling factor across the entire file.
- ANONYMISE_PERSONAL_INFO: true
- ANONYMISE_INSTRUMENTS: true
- PRESERVE_DATES: true
- PRESERVE_CURRENCIES: true
- PRESERVE_EXCHANGES: true
- PRESERVE_ASSET_CATEGORIES: true
- PRESERVE_SECTION_NAMES: true
- PRESERVE_ROW_ORDER: true

================================================================================
1. EXECUTION RULES FOR ANY TOOL-ENABLED AI ENVIRONMENT
================================================================================

This prompt is intended to work in any tool-enabled AI environment, including ChatGPT with file/code execution, OpenAI Codex, Claude Code, Gemini CLI, Gemini Pro coding agents, or similar systems.

Do not manually rewrite the CSV.

Use Python or another deterministic file-processing method to:
- read the input CSV;
- inspect the actual rows and headers;
- parse section headers dynamically;
- build a section/header map from the actual file;
- build stable anonymisation mappings;
- transform the CSV;
- validate the generated output with code;
- write the anonymised CSV as a new file.

The generated anonymisation logic must be input-driven, not hardcoded to one known example file.

Before transforming values:
- parse all section headers dynamically;
- build a section/header map from the actual CSV;
- infer amount-like columns from the active section header and section semantics;
- build instrument mappings from all Financial Instrument Information rows and from any additional instrument-like references found elsewhere;
- validate against the actual input file after transformation.

Do not hardcode:
- row numbers;
- specific symbols;
- specific ISINs;
- specific CUSIPs;
- specific account ids;
- specific amounts;
- assumptions that only apply to one sample file.

If validation fails:
- fix the anonymisation logic;
- regenerate the anonymised CSV;
- rerun validation;
- only return the anonymised CSV after validation passes.

If you do not have access to file-processing/code execution tools in this environment, stop and say that this anonymisation cannot be performed safely here.

Main goal:
Create an anonymised CSV that can still be used to test an IBKR tax/reporting parser. The anonymised file must preserve:
- all sections;
- all headers;
- all row types;
- all currencies;
- all asset categories;
- all exchange / listing exchange values;
- all date and time values;
- all accounting relationships;
- all closed-lot relationships;
- all totals and subtotals;
- all instrument join relationships across IBKR sections.

Output files:
1. An anonymised CSV file.
2. A short validation summary in the chat.

Do not print the full CSV in the chat unless the file is very small. Prefer creating a downloadable CSV file.

================================================================================
2. Preserve CSV structure exactly
================================================================================

Preserve:
- row order;
- section names;
- header rows;
- number of columns per row;
- blank cells;
- row types such as Header, Data, SubTotal, Total;
- asset categories such as Stocks, CFDs, Forex, Treasury Bills, Bonds, Bills, Notes, etc.;
- currencies such as USD, EUR, GBP, BGN;
- exchange codes and listing exchange codes;
- dates and times;
- tax lot holding period markers such as ST/LT;
- all other non-sensitive classification fields.

The anonymised CSV must remain parseable as an IBKR Activity Statement.

================================================================================
3. Anonymise personal and account information
================================================================================

Replace all personally identifying information with stable dummy values.

Examples:
- real person name -> John Doe
- account id -> U00000000
- account alias -> DemoAccount
- address -> 123 Demo Street, Demo City
- email -> john.doe@example.com
- phone -> +10000000000
- external bank/account references -> DEMO-ACCOUNT-001
- tax identifiers -> DEMO-TAX-ID

Use stable pseudonymisation:
- the same original value must always become the same dummy value;
- different original values should become different dummy values where needed.

Search all free-text cells for possible personal/account data. Do not only check obvious columns.

Sensitive values include:
- names;
- account ids;
- account aliases;
- addresses;
- emails;
- phone numbers;
- tax ids;
- external account numbers;
- transfer references;
- user-specific identifiers.

IMPORTANT ORDERING / TOKEN PROTECTION RULE:
Personal-info anonymisation must not corrupt instrument identifiers.

Recommended safe approach:
1. First detect and build the full instrument mapping from the original file.
2. During personal-info anonymisation, protect original and dummy instrument tokens from generic regex replacement.
3. Then apply instrument anonymisation as the final text replacement pass.

Do NOT apply generic phone-number, account-number, or numeric-id regexes inside:
- valid ISIN tokens;
- CUSIP-like tokens;
- fixed-income symbols;
- dummy ISINs;
- dummy CUSIP-like symbols;
- dummy instrument symbols.

The following must never happen:
- US0000000019 -> US+10000000000
- IE0000000018 -> IE+10000000000
- 000000001 -> +10000000000
- STK001(US0000000019) -> STK001(US+10000000000)

Any output containing malformed pseudo-ISINs such as:
- two letters followed by a plus sign;
- country prefix + phone-like number;
- strings matching `[A-Z]{2}\+[0-9]+`;
must fail validation.

================================================================================
4. Anonymise instruments by default
================================================================================

Instrument anonymisation must be stable and consistent across the whole file.

For each unique instrument, replace:
- symbol;
- description;
- ISIN / security id where applicable;
- CUSIP-like fixed-income identifiers where applicable;
- conid / numeric instrument id where applicable;
- issuer/company/fund names where they appear as part of instrument identity;
- underlying symbols where they appear in descriptions or free-text fields.

Preserve:
- asset category;
- currency;
- exchange;
- listing exchange;
- multiplier;
- date fields;
- all row/section semantics.

Use a stable mapping internally:
- the same original instrument must always become the same dummy instrument everywhere;
- do not print the original-to-dummy mapping in the chat, because that would leak the original instruments.

Recommended dummy formats:
- Stocks: STK001, STK002, STK003...
- ETFs/Funds: ETF001, ETF002, ETF003...
- CFDs: CFD001, CFD002, CFD003...
- Forex pairs/cash-like instruments: FX001, FX002...
- Unknown/other instruments: INS001, INS002...

Important fixed-income exception:
- For Treasury Bills / Bonds / Bills / Notes, do NOT blindly use BOND001/BOND002 as the Symbol.
- Fixed-income Symbol values in IBKR are often not tickers. They may be:
  - full ISINs, e.g. US912797NP82;
  - CUSIP-like 9-character identifiers, e.g. 912797NP8;
  - ISIN-core identifiers, especially for US Treasury Bills, where Security ID is US + Symbol + check digit.
- In those cases, the dummy Symbol must preserve the same identifier shape and relationship, not become BOND001/BOND002.

Descriptions:
- DUMMY STOCK 001
- DUMMY ETF 001
- DUMMY CFD 001
- DUMMY TREASURY BILL 001
- DUMMY BOND 001
- DUMMY INSTRUMENT 001

ISINs / Security IDs:
- Generate valid-looking dummy ISINs.
- Preserve the original country prefix where practical.
  Example:
  - US0378331005 -> US0000000019
  - IE00BK5BQT80 -> IE0000000018
  - DE000A0S9GB0 -> DE0000000016
- Prefer generating ISINs with a valid check digit.
- If an instrument has no ISIN, do not invent one unless the original field requires a security id.
- For CFDs, which may not have ISINs, dummy numeric ids such as 900000001, 900000002 are acceptable.

Critical ISIN validation rule:
Every generated dummy ISIN must remain a 12-character ISIN-like token:
- 2 uppercase letters;
- 9 uppercase alphanumeric characters;
- 1 check digit.

Valid-looking examples:
- US0000000019
- IE0000000018
- LU0000000017
- DE0000000016

Invalid examples:
- US+10000000000
- IE+10000000000
- LU+10000000000
- US000000001
- US000000001XX

Important:
Instrument identity may appear outside obvious instrument columns. Do not only replace the Symbol and Security ID columns.

When anonymising instruments, scan and transform all cells that may contain:
- original symbols;
- issuer/company/fund names;
- ISINs;
- CUSIP-like fixed-income identifiers;
- conids/security ids;
- underlying symbols;
- descriptions such as "USD AAPL", "USD FXI", "APPLE INC", "ISHARES...", etc.;
- dividend descriptions;
- withholding-tax descriptions;
- corporate-action descriptions;
- fee descriptions;
- transfer descriptions;
- stock-yield-enhancement descriptions;
- mark-to-market descriptions;
- realized/unrealized performance descriptions;
- any free-text field containing strings such as SYMBOL(ISIN).

Examples:
- AAPL -> STK001
- APPLE INC -> DUMMY STOCK 001
- US0378331005 -> US0000000019
- USD AAPL -> USD STK001
- USD FXI -> USD CFD001
- AAPL(US0378331005) Cash Dividend USD 0.24 per Share -> STK001(US0000000019) Cash Dividend USD 0.24 per Share

Instrument identity joins must remain valid across all sections, including:
- Financial Instrument Information;
- Trades;
- ClosedLot rows;
- Open Positions;
- Transfers;
- Dividends;
- Withholding Tax;
- Interest;
- Fees;
- Borrow Fee;
- Stock Yield Enhancement Program;
- Corporate Actions;
- Mark-to-Market Performance Summary;
- Realized & Unrealized Performance Summary;
- any other section referencing instruments.

================================================================================
4A. Treasury Bills / Bonds / fixed-income identifier rule
================================================================================

This section is mandatory and overrides the generic dummy-symbol rules.

In IBKR Activity Statements, Treasury Bills, Bonds, Bills, Notes, and other fixed-income instruments may use a security identifier as the Symbol.

Examples from IBKR:
- Symbol: 912797NP8
- Security ID / ISIN: US912797NP82
- Description: B 06/05/25

Here, `912797NP8` is not a normal ticker. It is the CUSIP-like 9-character core of the US ISIN `US912797NP82`.

Therefore:

DO NOT replace fixed-income symbols like this with BOND001, BOND002, INS001, etc.

Instead, detect the relationship between:
- Financial Instrument Information Symbol;
- Financial Instrument Information Security ID / ISIN;
- Trade description;
- ClosedLot symbol;
- Open Positions symbol;
- Transfers symbol;
- Corporate Actions symbol;
- SubTotal symbol;
- Mark-to-Market Performance Summary symbol/description;
- Realized & Unrealized Performance Summary symbol/description;
- any free-text reference.

For fixed-income instruments:

Case A: Symbol is a full ISIN-like token
- Original Symbol example: US912797NP82
- Dummy Security ID: US0000000556
- Dummy Symbol must be: US0000000556

Case B: Symbol is a CUSIP-like / ISIN-core token and Security ID is full ISIN
- Original Symbol example: 912797NP8
- Original Security ID example: US912797NP82
- Relationship: Security ID = country prefix + Symbol + check digit
- Dummy Security ID example: US0000000556
- Dummy Symbol must be the 9-character ISIN core from the dummy Security ID.
- Example:
  - Dummy Security ID: US0000000556
  - Dummy Symbol: 000000055

Correct anonymisation:
- Original Symbol: 912797NP8
- Original Security ID: US912797NP82
- Dummy Symbol: 000000055
- Dummy Security ID: US0000000556
- Trade text: `000000055 4.28601533%`
- Trade description: `DUMMY TREASURY BILL 001<br/>000000055 4.28601533%`
- ClosedLot symbol: `000000055`
- SubTotal symbol: `000000055`

Incorrect anonymisation:
- Original Symbol: 912797NP8
- Original Security ID: US912797NP82
- Dummy Symbol: BOND002
- Dummy Security ID: US0000000556
- Trade text: `BOND002 4.28601533%`
- ClosedLot symbol: `BOND002`

This is invalid because downstream tools may detect or reconstruct the bond/T-bill identifier from the Symbol field.

Fixed-income dummy labels:
- BOND001/BOND002 may be used only in descriptions if needed, e.g. `DUMMY TREASURY BILL 001`.
- BOND001/BOND002 must not be used as the Symbol when the original fixed-income Symbol was an ISIN-like, CUSIP-like, or ISIN-core identifier.

Detection rules:
- Treat these asset categories as fixed-income for this rule:
  - Treasury Bills
  - Bonds
  - Bills
  - Notes
  - Corporate Bonds
  - Government Bonds
  - Municipal Bonds
  - any similar fixed-income asset category
- If the original Symbol matches `[A-Z]{2}[A-Z0-9]{9}[0-9]`, treat it as a full ISIN.
- If the original Symbol matches `[A-Z0-9]{9}` and the Security ID matches `[A-Z]{2}[A-Z0-9]{9}[0-9]`, check whether the 9-character Symbol equals characters 3-11 of the Security ID.
  - For example: Symbol `912797NP8` equals the core of `US912797NP82`.
  - If yes, the dummy Symbol must equal characters 3-11 of the dummy Security ID.
- If the original Symbol is CUSIP-like but no Security ID exists, replace it with a stable dummy 9-character alphanumeric identifier, not BOND001.
- Preserve this mapping consistently across every section and every free-text field.

Mandatory replacement examples:
- `912797NP8` -> `000000055`
- `US912797NP82` -> `US0000000556`
- `912797NP8 4.28601533%` -> `000000055 4.28601533%`
- `United States Treasury B 06/05/25<br/>912797NP8 4.28601533%` -> `DUMMY TREASURY BILL 001<br/>000000055 4.28601533%`
- `912797NP8 - United States Treasury B 06/05/25` -> `000000055 - DUMMY TREASURY BILL 001`

Do not leave any original fixed-income identifiers in:
- Mark-to-Market Performance Summary;
- Realized & Unrealized Performance Summary;
- Open Positions;
- Trades;
- ClosedLot rows;
- SubTotal rows;
- Transfers;
- Corporate Actions;
- Financial Instrument Information;
- any other free-text field.

================================================================================
5. Scale quantities and money amounts
================================================================================

Use one global SCALING_FACTOR for the entire file.

If I did not provide a factor, choose a random log-uniform value between 0.0001 and 0.05.

The purpose of scaling is to hide:
- exact values;
- position sizes;
- portfolio size;
- approximate wealth level;
- real P/L magnitude;
- real income amounts.

Scale values that represent:
- quantities;
- shares;
- position sizes;
- proceeds;
- cost basis;
- realized P/L;
- unrealized P/L;
- commissions;
- fees;
- dividends;
- withholding tax;
- interest;
- borrow fees;
- accrual amounts;
- cash balances;
- NAV;
- deposits;
- withdrawals;
- collateral;
- market value;
- value;
- securities value;
- futures value;
- totals and subtotals that are money-like or quantity-like.

Preserve the sign:
- positive stays positive;
- negative stays negative;
- zero stays zero;
- blank stays blank.

Do not scale:
- trade prices;
- close prices;
- cost prices;
- transfer prices;
- FX rates;
- percentages;
- interest rates;
- multipliers;
- dates;
- times;
- currency codes;
- exchange codes;
- asset categories;
- row types;
- section names;
- ISIN check digits independently;
- account ids / conids / identifiers, because they should be pseudonymised instead.

Important accounting rule:
Do not blindly scale every numeric field. Prices must generally remain unchanged so that:

quantity × price ≈ amount

continues to hold after quantity and amount scaling.

Keep enough decimal precision so quantities, proceeds, basis, P/L, totals, and closed lots continue to reconcile.
Do not round scaled quantities to whole shares.

================================================================================
5A. Section-specific amount-column scaling rules
================================================================================

Do not scale only columns named `Total`.

IBKR Activity Statements contain many money-like columns whose names are not simply
`Amount` or `Total`. The anonymisation must scale all amount-like numeric columns
in each section, based on the section header.

Mandatory section-specific scaling rules:

1. Cash Report

For Cash Report rows with header:

Cash Report,Header,Currency Summary,Currency,Total,Securities,Futures,...

Scale all non-zero numeric values in these columns:
- Total
- Securities
- Futures

Do not scale:
- Currency Summary
- Currency
- row labels such as Starting Cash, Commissions, Account Transfers, Ending Cash

Important:
If original Cash Report row has:

Total = Securities + Futures

then the anonymised row must preserve:

scaled Total = scaled Securities + scaled Futures

within rounding tolerance.

If the original row has the same value in Total and Securities because Futures is zero,
then both Total and Securities must be scaled to the same anonymised value.

Example:

Original:
Cash Report,Data,Commissions,EUR,-79.5120056,-79.5120056,0

Correct:
Cash Report,Data,Commissions,EUR,-0.526870726,-0.526870726,0

Incorrect:
Cash Report,Data,Commissions,EUR,-0.526870726,-79.5120056,0

The incorrect example leaks the original amount in the Securities column and must fail validation.

2. Change in NAV

For Change in NAV rows with header:

Change in NAV,Header,Field Name,Field Value,...

Scale all non-zero numeric values in:
- Field Value

This includes, but is not limited to:
- Starting Value
- Mark-to-Market
- Deposits & Withdrawals
- Position Transfers
- Dividends
- Withholding Tax
- Change in Dividend Accruals
- Interest
- Change in Interest Accruals
- Commissions
- Other FX Translations
- Ending Value

Do not leave any original non-zero Field Value unchanged unless it is clearly a price, rate,
percentage, multiplier, date, time, or identifier. In Change in NAV, Field Value is money-like
and should normally be scaled.

3. Interest Accruals

For Interest Accruals rows with header:

Interest Accruals,Header,Currency,Field Name,Field Value,...

Scale all non-zero numeric values in:
- Field Value

This includes:
- Starting Accrual Balance
- Interest Accrued
- Accrual Reversal
- FX Translation
- Ending Accrual Balance
- Ending Accrual Balance in base currency

Do not scale:
- Currency
- Field Name

4. Mark-to-Market Performance Summary

For Mark-to-Market Performance Summary rows, scale all amount-like numeric columns such as:
- Position
- Transaction
- Commissions
- Other
- Total
- Realized P/L
- Unrealized P/L
- Mark-to-Market P/L
- Market Value
- any other value/P&L/amount-like column

Do not scale:
- Symbol
- Description
- Asset Category
- Currency
- Price
- Close Price
- FX Rate
- percentage/rate columns

5. Realized & Unrealized Performance Summary

For Realized & Unrealized Performance Summary rows, scale all amount-like numeric columns such as:
- Realized Total
- Realized Short-Term
- Realized Long-Term
- Unrealized Total
- Unrealized Short-Term
- Unrealized Long-Term
- Cost Basis
- Proceeds
- Value
- Total

Do not scale:
- Symbol
- Description
- Asset Category
- Currency
- Price
- Close Price
- FX Rate
- percentage/rate columns

6. Dividend Accruals / Change in Dividend Accruals

Scale all amount-like numeric columns, including:
- Gross Amount
- Tax
- Net Amount
- Accrual
- Accrual Reversal
- FX Translation
- Ending Accrual Balance
- Field Value where used as an amount

Do not scale:
- Currency
- Symbol
- Description
- Date
- percentage/rate columns

7. Stock Yield Enhancement Program / SYEP

Scale all amount-like numeric columns, including:
- Interest
- Fee
- Collateral
- Value
- Amount
- Tax
- Payment
- Total

Do not scale:
- Symbol
- Description
- Security ID
- Quantity multiplier
- Rate
- percentage/rate columns
- Date
- Currency

8. Generic header-driven rule

For every section, inspect the active Header row and identify amount-like columns by name.

Columns with the following names or name fragments are normally money-like and must be scaled
unless the section-specific logic clearly marks them as price/rate/identifier fields:

- Total
- Securities
- Futures
- Field Value
- Amount
- Value
- Balance
- Cash
- Settled Cash
- Collateral
- Accrual
- Proceeds
- Basis
- Cost Basis
- Realized P/L
- Unrealized P/L
- MTM
- Mark-to-Market
- Commission
- Fee
- Tax
- Withholding
- Dividend
- Interest
- Deposit
- Withdrawal
- Transfer
- Net Cash
- Market Value
- Debit
- Credit
- Position
- Transaction
- Other
- P/L
- P&L

Columns with the following names or name fragments are normally NOT scaled:

- Price
- Close Price
- Cost Price
- T. Price
- C. Price
- Xfer Price
- FX Rate
- Rate
- %
- Percentage
- Multiplier
- Mult
- Quantity multiplier
- Date
- Time
- Currency
- Symbol
- Security ID
- ISIN
- Conid
- Code
- Account
- Description
- Exchange
- Listing Exchange

Important:
A column named `Field Value` must be interpreted based on the section and row label.
For sections such as Change in NAV and Interest Accruals, Field Value is money-like and must be scaled.

9. Unchanged non-zero amount validation

After anonymisation, compare the original and anonymised CSV cell by cell.

For every non-zero numeric cell in a column classified as amount-like:
- the anonymised value must not equal the original value;
- the anonymised value should equal original value × SCALING_FACTOR, within rounding tolerance;
- if totals/subtotals are recomputed, the value may differ slightly due to reconciliation adjustment, but it must not remain the original value.

If a non-zero amount-like value remains exactly unchanged, validation must fail.

This validation must specifically check:
- Cash Report: Total, Securities, Futures
- Change in NAV: Field Value
- Interest Accruals: Field Value
- Dividend Accruals / Change in Dividend Accruals: amount-like columns
- Deposits & Withdrawals: amount-like columns
- Fees / Borrow Fees / SYEP: amount-like fee, interest, collateral, and income columns
- NAV / MTM summary sections: amount-like value and P/L columns
- Realized & Unrealized Performance Summary: amount-like value and P/L columns

================================================================================
6. Preserve closed-lot consistency
================================================================================

Closed-lot math must remain valid.

For every closing trade and its related ClosedLot rows:
- the sum of the anonymised ClosedLot quantities should equal the anonymised closing trade quantity;
- the sum of anonymised proceeds, basis, commission, and realized P/L should reconcile to the relevant trade/subtotal/total rows;
- any subtotal/total rows should remain consistent after anonymisation.

If rounding creates small differences:
- adjust the last row in the affected group, not the first;
- keep the adjustment minimal;
- prefer preserving internal totals over preserving exact decimal formatting.

================================================================================
7. Recompute totals and subtotals where needed
================================================================================

After scaling row-level values:
- recompute SubTotal and Total rows where the section semantics are clear;
- otherwise scale them and validate against the sum of child rows;
- if there is a mismatch caused by rounding, correct the total/subtotal or the final child row consistently.

This applies especially to:
- Trades;
- Closed Lots;
- Dividends;
- Withholding Tax;
- Interest;
- Fees;
- Borrow Fees;
- Cash Report;
- NAV;
- Deposits and Withdrawals;
- Open Positions;
- Stock Yield Enhancement Program;
- Mark-to-Market Performance Summary;
- Realized & Unrealized Performance Summary;
- Interest Accruals;
- Dividend Accruals;
- any other section with visible totals/subtotals.

================================================================================
8. Rounding rules
================================================================================

Preserve the original numeric formatting where practical.

Rules:
- keep blank cells blank;
- keep zero values zero;
- preserve negative signs;
- preserve the approximate number of decimal places from the original field;
- do not round aggressively;
- use enough decimals to avoid reconciliation errors;
- do not round scaled quantities to whole shares;
- if a section uses high precision, keep high precision.

When in doubt, prefer mathematical consistency over cosmetic formatting.

================================================================================
9. Validation
================================================================================

Before returning the anonymised CSV, validate the result.

Required validation checks:
1. The output CSV is parseable.
2. The output has the same number of rows as the input.
3. Each output row has the same number of columns as the corresponding input row.
4. Section names are preserved.
5. Header rows are preserved structurally.
6. Row order is preserved.
7. Currencies are preserved.
8. Asset categories are preserved.
9. Exchange/listing exchange values are preserved.
10. Dates and times are preserved.
11. No original account id remains.
12. No original account alias remains.
13. No obvious original name, email, phone, address, or tax id remains.
14. Instrument symbols/descriptions/ISINs/conids are pseudonymised consistently.
15. Original instrument symbols, ISINs, issuer names, and descriptions do not remain in free-text fields.
16. Closed-lot quantities reconcile to their related closing trades where the relationship can be identified.
17. Subtotals and totals reconcile where section semantics are clear.
18. No price fields were incorrectly scaled.
19. No FX rate fields were incorrectly scaled.
20. No percentage/rate fields were incorrectly scaled.

Additional mandatory validation for ISIN/security-id corruption:
21. All generated dummy ISINs must match a valid ISIN-like pattern: `[A-Z]{2}[A-Z0-9]{9}[0-9]`.
22. No cell may contain malformed pseudo-ISINs such as `[A-Z]{2}\+[0-9]+`.
23. No cell may contain strings like `STK001(US+10000000000)`, `ETF001(IE+10000000000)`, or similar.
24. Dividend, withholding-tax, corporate-action, transfer, fee, and SYEP descriptions must be scanned specifically for malformed ISIN/security-id tokens.
25. Personal-info regex replacement must not modify dummy instrument symbols, dummy ISINs, dummy security IDs, dummy CUSIP-like identifiers, or dummy descriptions.

Mandatory fixed-income validation:
26. For every fixed-income instrument whose original Symbol is a full ISIN-like token, the anonymised Symbol must also be a valid dummy ISIN and must not be BOND001/BOND002/etc.
27. For every fixed-income instrument whose original Symbol is a CUSIP-like / ISIN-core token and whose original Security ID is a full ISIN, validate the relationship:
    - original Symbol must equal characters 3-11 of original Security ID, when applicable;
    - dummy Symbol must equal characters 3-11 of dummy Security ID;
    - dummy Symbol must not be BOND001/BOND002/INS001/etc.
28. For every fixed-income instrument, scan all sections and ensure that:
    - the original Symbol no longer appears;
    - the original Security ID no longer appears;
    - the dummy Symbol appears everywhere the original Symbol was used as the join key;
    - BOND001/BOND002 is not used as the Symbol if the original Symbol was identifier-like.
29. Specifically validate fixed-income identifiers in:
    - Financial Instrument Information;
    - Trades;
    - ClosedLot rows;
    - SubTotal rows;
    - Open Positions;
    - Mark-to-Market Performance Summary;
    - Realized & Unrealized Performance Summary;
    - Transfers;
    - Corporate Actions;
    - any free-text field that references the bond/T-bill identifier.

Mandatory amount-scaling validation:
30. Cash Report amount-column validation:
    For every Cash Report data row, all non-zero numeric values in Total, Securities, and Futures must be scaled consistently. If Total = Securities + Futures in the original row, the same relationship must hold after scaling. No original non-zero value may remain in Securities or Futures.

31. Change in NAV Field Value validation:
    Every non-zero numeric Field Value in Change in NAV must be scaled unless it is explicitly proven to be a price, rate, percentage, multiplier, date, time, or identifier. For Change in NAV, Field Value is normally money-like.

32. Interest Accruals Field Value validation:
    Every non-zero numeric Field Value in Interest Accruals must be scaled, including Interest Accrued, Accrual Reversal, FX Translation, and Ending Accrual Balance rows.

33. Generic unchanged amount leak validation:
    For all columns classified as amount-like by section/header rules, no non-zero original numeric value may remain exactly unchanged in the anonymised output. If such a value is found, report the row number, section, column name, original value, and anonymised value, then regenerate the file.

34. Summary-section amount validation:
    In Mark-to-Market Performance Summary and Realized & Unrealized Performance Summary, all amount-like value/P&L/cost/proceeds/commission/other/total columns must be scaled. Symbol, description, price, rate, currency, asset category, and exchange fields must not be scaled.

If any of checks 21-34 fail:
- do not return the anonymised CSV as valid;
- fix the anonymisation logic and regenerate;
- only return the file after these checks pass.

If some validations cannot be performed reliably because the statement structure is ambiguous, include this in the validation summary.

================================================================================
10. Validation summary
================================================================================

After creating the anonymised CSV, provide a short summary with:

- the scaling factor used;
- whether personal/account data was anonymised;
- whether instruments were anonymised;
- number of instrument mappings created, without revealing original symbols;
- whether validation passed;
- any warnings;
- the output filename.

Do not reveal the original-to-dummy mapping.

Example summary format:

Anonymisation complete.

Scaling factor used: 0.00372814
Personal/account data anonymised: yes
Instruments anonymised: yes
Instrument mappings created:
- Stocks: 14
- ETFs/Funds: 3
- CFDs: 4
- Bonds/Treasury Bills: 1
- Other: 2

Validation:
- CSV structure: passed
- Row/column counts: passed
- Personal data scan: passed
- Instrument pseudonymisation: passed
- Free-text instrument scan: passed
- Dummy ISIN/security-id integrity: passed
- Dividend/withholding-tax description scan: passed
- Treasury Bills / Bonds identifier-symbol validation: passed
- Cash Report amount-column validation: passed
- Change in NAV Field Value validation: passed
- Interest Accruals Field Value validation: passed
- Generic unchanged amount leak validation: passed
- Closed-lot reconciliation: passed
- Totals/subtotals: passed with 2 rounding adjustments

Warnings:
- Some section totals could not be independently recomputed because their semantics were not clear.
- No original-to-dummy mapping is printed to avoid leaking original instruments.

================================================================================
11. If something is unsafe or impossible
================================================================================

If you cannot safely anonymise the file while preserving structure and math, do not guess.

Instead:
- explain which validation failed;
- return no anonymised CSV, or clearly mark it as failed validation;
- suggest what additional information or capability is needed.

Remember:
The anonymised file must be safe to share and still useful for parser testing.