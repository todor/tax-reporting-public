from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Literal

from config import OUTPUT_DIR
from services.bnb_fx import get_exchange_rate

logger = logging.getLogger(__name__)

SUPPORTED_ASSET_CATEGORIES = {"Stocks", "Treasury Bills"}
FOREX_ASSET_CATEGORY = "Forex"

EU_REGULATED_MARKETS = {
    "IBIS",
    "IBIS2",
    "FWB",
    "AEB",
    "SBF",
    "ENEXT.BE",
    "ENEXT.PT",
    "ISE",
    "ENEXT.IR",
    "BVME",
    "BVME.ETF",
    "VSE",
    "WSE",
    "PSE",
    "OMXCP",
    "OMXSTO",
    "OMXHEX",
    "BME",
    "SIBE",
    "BM",
    "BVL",
    "PRA",
    "CPH",
    "OMXNO",
    "OSE",
}

EU_NON_REGULATED = {
    "EUIBSI",
    "EUDARK",
    "IBDARK",
    "CHIXEN",
    "CHIXES",
    "CHIXUK",
    "BATS",
    "TRQX",
    "AQUIS",
    "GETTEX",
    "GETTEX2",
    "TGATE",
    "SWB",
}

EXCHANGE_ALIASES = {
    "ISE": "ENEXT.IR",
    "BME": "SIBE",
    "BM": "SIBE",
}

ADDED_TRADES_COLUMNS = [
    "Fx Rate",
    "Comm/Fee (EUR)",
    "Proceeds (EUR)",
    "Basis (EUR)",
    "Sale Price (EUR)",
    "Purchase Price (EUR)",
    "Realized P/L (EUR)",
    "Realized P/L Wins (EUR)",
    "Realized P/L Losses (EUR)",
    "Normalized Symbol",
    "Listing Exchange",
    "Symbol Listed On EU Regulated Market",
    "Execution Exchange Classification",
    "Tax Exempt Mode",
    "Appendix Target",
    "Tax Treatment Reason",
    "Review Required",
    "Review Notes",
]

DECIMAL_TWO = Decimal("0.01")
DECIMAL_EIGHT = Decimal("0.00000001")
ZERO = Decimal("0")
QTY_RECONCILIATION_EPSILON = DECIMAL_EIGHT
APPENDIX_9_ALLOWABLE_CREDIT_RATE = Decimal("0.10")
DIVIDEND_TAX_RATE = Decimal("0.05")

TAX_MODE_LISTED_SYMBOL = "listed_symbol"
TAX_MODE_EXECUTION_EXCHANGE = "execution_exchange"
APPENDIX8_LIST_MODE_COMPANY = "company"
APPENDIX8_LIST_MODE_COUNTRY = "country"
APPENDIX8_COUNTRY_MODE_PAYER_LABEL = "Различни чуждестранни дружества (чрез Interactive Brokers)"

APPENDIX_5 = "APPENDIX_5"
APPENDIX_13 = "APPENDIX_13"
APPENDIX_REVIEW = "REVIEW_REQUIRED"
APPENDIX_IGNORED = "IGNORED"

EXCHANGE_CLASS_EU_REGULATED = "EU_REGULATED"
EXCHANGE_CLASS_EU_NON_REGULATED = "EU_NON_REGULATED"
EXCHANGE_CLASS_UNKNOWN = "UNKNOWN"
REVIEW_STATUS_TAXABLE = "TAXABLE"
REVIEW_STATUS_NON_TAXABLE = "NON-TAXABLE"

INTEREST_TYPE_CREDIT = "Credit Interest"
INTEREST_TYPE_SYEP = "IBKR Managed Securities (SYEP) Interest"
INTEREST_TYPE_DEBIT = "Debit Interest"
INTEREST_TYPE_BORROW = "Borrow Fees"

INTEREST_STATUS_TAXABLE = "TAXABLE"
INTEREST_STATUS_NON_TAXABLE = "NON-TAXABLE"
INTEREST_STATUS_UNKNOWN = "UNKNOWN"

INTEREST_DECLARED_TYPES = {INTEREST_TYPE_CREDIT, INTEREST_TYPE_SYEP}
INTEREST_NON_DECLARED_TYPES = {INTEREST_TYPE_DEBIT, INTEREST_TYPE_BORROW}

DIVIDEND_APPENDIX_8 = "Appendix 8"
DIVIDEND_APPENDIX_6 = "Appendix 6"
DIVIDEND_APPENDIX_UNKNOWN = "UNKNOWN"
APPENDIX_9_DEFAULT_COUNTRY_ISO = "IE"

DIVIDEND_REVIEW_REQUIRED = "REVIEW_REQUIRED"
REVIEW_REASON_OPEN_POSITION_TRADE_QTY_MISMATCH = "OPEN_POSITION_TRADE_QTY_MISMATCH"
REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT = "OPEN_POSITION_UNMATCHED_INSTRUMENT"
REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT = "TRADE_UNMATCHED_INSTRUMENT"
REVIEW_REASON_OPEN_POSITION_UNSUPPORTED_ASSET = "OPEN_POSITION_UNSUPPORTED_ASSET"

COUNTRY_NAME_BY_ISO: dict[str, tuple[str, str]] = {
    "AD": ("Andorra", "Андора"),
    "AE": ("United Arab Emirates", "Обединени арабски емирства"),
    "AF": ("Afghanistan", "Афганистан"),
    "AG": ("Antigua and Barbuda", "Антигуа и Барбуда"),
    "AI": ("Anguilla", "Ангуила"),
    "AL": ("Albania", "Албания"),
    "AM": ("Armenia", "Армения"),
    "AO": ("Angola", "Ангола"),
    "AQ": ("Antarctica", "Антарктика"),
    "AR": ("Argentina", "Аржентина"),
    "AS": ("American Samoa", "Американска Самоа"),
    "AT": ("Austria", "Австрия"),
    "AU": ("Australia", "Австралия"),
    "AW": ("Aruba", "Аруба"),
    "AX": ("Åland Islands", "Оландски острови"),
    "AZ": ("Azerbaijan", "Азербайджан"),
    "BA": ("Bosnia and Herzegovina", "Босна и Херцеговина"),
    "BB": ("Barbados", "Барбадос"),
    "BD": ("Bangladesh", "Бангладеш"),
    "BE": ("Belgium", "Белгия"),
    "BF": ("Burkina Faso", "Буркина Фасо"),
    "BG": ("Bulgaria", "България"),
    "BH": ("Bahrain", "Бахрейн"),
    "BI": ("Burundi", "Бурунди"),
    "BJ": ("Benin", "Бенин"),
    "BL": ("Saint Barthélemy", "Сен Бартелеми"),
    "BM": ("Bermuda", "Бермуда"),
    "BN": ("Brunei Darussalam", "Бруней Даруссалам"),
    "BO": ("Bolivia, Plurinational State of", "Боливия"),
    "BQ": ("Bonaire, Sint Eustatius and Saba", "Карибска Нидерландия"),
    "BR": ("Brazil", "Бразилия"),
    "BS": ("Bahamas", "Бахами"),
    "BT": ("Bhutan", "Бутан"),
    "BV": ("Bouvet Island", "остров Буве"),
    "BW": ("Botswana", "Ботсвана"),
    "BY": ("Belarus", "Беларус"),
    "BZ": ("Belize", "Белиз"),
    "CA": ("Canada", "Канада"),
    "CC": ("Cocos (Keeling) Islands", "Кокосови острови (острови Кийлинг)"),
    "CD": ("Congo, Democratic Republic of the", "Конго (Киншаса)"),
    "CF": ("Central African Republic", "Централноафриканска република"),
    "CG": ("Congo", "Конго (Бразавил)"),
    "CH": ("Switzerland", "Швейцария"),
    "CI": ("Côte d'Ivoire", "Кот д’Ивоар"),
    "CK": ("Cook Islands", "острови Кук"),
    "CL": ("Chile", "Чили"),
    "CM": ("Cameroon", "Камерун"),
    "CN": ("China", "Китай"),
    "CO": ("Colombia", "Колумбия"),
    "CR": ("Costa Rica", "Коста Рика"),
    "CU": ("Cuba", "Куба"),
    "CV": ("Cabo Verde", "Кабо Верде"),
    "CW": ("Curaçao", "Кюрасао"),
    "CX": ("Christmas Island", "остров Рождество"),
    "CY": ("Cyprus", "Кипър"),
    "CZ": ("Czechia", "Чехия"),
    "DE": ("Germany", "Германия"),
    "DJ": ("Djibouti", "Джибути"),
    "DK": ("Denmark", "Дания"),
    "DM": ("Dominica", "Доминика"),
    "DO": ("Dominican Republic", "Доминиканска република"),
    "DZ": ("Algeria", "Алжир"),
    "EC": ("Ecuador", "Еквадор"),
    "EE": ("Estonia", "Естония"),
    "EG": ("Egypt", "Египет"),
    "EH": ("Western Sahara", "Западна Сахара"),
    "ER": ("Eritrea", "Еритрея"),
    "ES": ("Spain", "Испания"),
    "ET": ("Ethiopia", "Етиопия"),
    "FI": ("Finland", "Финландия"),
    "FJ": ("Fiji", "Фиджи"),
    "FK": ("Falkland Islands (Malvinas)", "Фолклендски острови"),
    "FM": ("Micronesia, Federated States of", "Микронезия"),
    "FO": ("Faroe Islands", "Фарьорски острови"),
    "FR": ("France", "Франция"),
    "GA": ("Gabon", "Габон"),
    "GB": ("United Kingdom of Great Britain and Northern Ireland", "Обединеното кралство"),
    "GD": ("Grenada", "Гренада"),
    "GE": ("Georgia", "Грузия"),
    "GF": ("French Guiana", "Френска Гвиана"),
    "GG": ("Guernsey", "Гърнзи"),
    "GH": ("Ghana", "Гана"),
    "GI": ("Gibraltar", "Гибралтар"),
    "GL": ("Greenland", "Гренландия"),
    "GM": ("Gambia", "Гамбия"),
    "GN": ("Guinea", "Гвинея"),
    "GP": ("Guadeloupe", "Гваделупа"),
    "GQ": ("Equatorial Guinea", "Екваториална Гвинея"),
    "GR": ("Greece", "Гърция"),
    "GS": ("South Georgia and the South Sandwich Islands", "Южна Джорджия и Южни Сандвичеви острови"),
    "GT": ("Guatemala", "Гватемала"),
    "GU": ("Guam", "Гуам"),
    "GW": ("Guinea-Bissau", "Гвинея-Бисау"),
    "GY": ("Guyana", "Гаяна"),
    "HK": ("Hong Kong", "Хонконг, САР на Китай"),
    "HM": ("Heard Island and McDonald Islands", "остров Хърд и острови Макдоналд"),
    "HN": ("Honduras", "Хондурас"),
    "HR": ("Croatia", "Хърватия"),
    "HT": ("Haiti", "Хаити"),
    "HU": ("Hungary", "Унгария"),
    "ID": ("Indonesia", "Индонезия"),
    "IE": ("Ireland", "Ирландия"),
    "IL": ("Israel", "Израел"),
    "IM": ("Isle of Man", "остров Ман"),
    "IN": ("India", "Индия"),
    "IO": ("British Indian Ocean Territory", "Британска територия в Индийския океан"),
    "IQ": ("Iraq", "Ирак"),
    "IR": ("Iran, Islamic Republic of", "Иран"),
    "IS": ("Iceland", "Исландия"),
    "IT": ("Italy", "Италия"),
    "JE": ("Jersey", "Джърси"),
    "JM": ("Jamaica", "Ямайка"),
    "JO": ("Jordan", "Йордания"),
    "JP": ("Japan", "Япония"),
    "KE": ("Kenya", "Кения"),
    "KG": ("Kyrgyzstan", "Киргизстан"),
    "KH": ("Cambodia", "Камбоджа"),
    "KI": ("Kiribati", "Кирибати"),
    "KM": ("Comoros", "Коморски острови"),
    "KN": ("Saint Kitts and Nevis", "Сейнт Китс и Невис"),
    "KP": ("Korea, Democratic People's Republic of", "Северна Корея"),
    "KR": ("Korea, Republic of", "Южна Корея"),
    "KW": ("Kuwait", "Кувейт"),
    "KY": ("Cayman Islands", "Кайманови острови"),
    "KZ": ("Kazakhstan", "Казахстан"),
    "LA": ("Lao People's Democratic Republic", "Лаос"),
    "LB": ("Lebanon", "Ливан"),
    "LC": ("Saint Lucia", "Сейнт Лусия"),
    "LI": ("Liechtenstein", "Лихтенщайн"),
    "LK": ("Sri Lanka", "Шри Ланка"),
    "LR": ("Liberia", "Либерия"),
    "LS": ("Lesotho", "Лесото"),
    "LT": ("Lithuania", "Литва"),
    "LU": ("Luxembourg", "Люксембург"),
    "LV": ("Latvia", "Латвия"),
    "LY": ("Libya", "Либия"),
    "MA": ("Morocco", "Мароко"),
    "MC": ("Monaco", "Монако"),
    "MD": ("Moldova, Republic of", "Молдова"),
    "ME": ("Montenegro", "Черна гора"),
    "MF": ("Saint Martin (French part)", "Сен Мартен"),
    "MG": ("Madagascar", "Мадагаскар"),
    "MH": ("Marshall Islands", "Маршалови острови"),
    "MK": ("North Macedonia", "Северна Македония"),
    "ML": ("Mali", "Мали"),
    "MM": ("Myanmar", "Мианмар (Бирма)"),
    "MN": ("Mongolia", "Монголия"),
    "MO": ("Macao", "Макао, САР на Китай"),
    "MP": ("Northern Mariana Islands", "Северни Мариански острови"),
    "MQ": ("Martinique", "Мартиника"),
    "MR": ("Mauritania", "Мавритания"),
    "MS": ("Montserrat", "Монтсерат"),
    "MT": ("Malta", "Малта"),
    "MU": ("Mauritius", "Мавриций"),
    "MV": ("Maldives", "Малдиви"),
    "MW": ("Malawi", "Малави"),
    "MX": ("Mexico", "Мексико"),
    "MY": ("Malaysia", "Малайзия"),
    "MZ": ("Mozambique", "Мозамбик"),
    "NA": ("Namibia", "Намибия"),
    "NC": ("New Caledonia", "Нова Каледония"),
    "NE": ("Niger", "Нигер"),
    "NF": ("Norfolk Island", "остров Норфолк"),
    "NG": ("Nigeria", "Нигерия"),
    "NI": ("Nicaragua", "Никарагуа"),
    "NL": ("Netherlands, Kingdom of the", "Нидерландия"),
    "NO": ("Norway", "Норвегия"),
    "NP": ("Nepal", "Непал"),
    "NR": ("Nauru", "Науру"),
    "NU": ("Niue", "Ниуе"),
    "NZ": ("New Zealand", "Нова Зеландия"),
    "OM": ("Oman", "Оман"),
    "PA": ("Panama", "Панама"),
    "PE": ("Peru", "Перу"),
    "PF": ("French Polynesia", "Френска Полинезия"),
    "PG": ("Papua New Guinea", "Папуа-Нова Гвинея"),
    "PH": ("Philippines", "Филипини"),
    "PK": ("Pakistan", "Пакистан"),
    "PL": ("Poland", "Полша"),
    "PM": ("Saint Pierre and Miquelon", "Сен Пиер и Микелон"),
    "PN": ("Pitcairn", "Острови Питкерн"),
    "PR": ("Puerto Rico", "Пуерто Рико"),
    "PS": ("Palestine, State of", "Палестински територии"),
    "PT": ("Portugal", "Португалия"),
    "PW": ("Palau", "Палау"),
    "PY": ("Paraguay", "Парагвай"),
    "QA": ("Qatar", "Катар"),
    "RE": ("Réunion", "Реюнион"),
    "RO": ("Romania", "Румъния"),
    "RS": ("Serbia", "Сърбия"),
    "RU": ("Russian Federation", "Русия"),
    "RW": ("Rwanda", "Руанда"),
    "SA": ("Saudi Arabia", "Саудитска Арабия"),
    "SB": ("Solomon Islands", "Соломонови острови"),
    "SC": ("Seychelles", "Сейшели"),
    "SD": ("Sudan", "Судан"),
    "SE": ("Sweden", "Швеция"),
    "SG": ("Singapore", "Сингапур"),
    "SH": ("Saint Helena, Ascension and Tristan da Cunha", "Света Елена"),
    "SI": ("Slovenia", "Словения"),
    "SJ": ("Svalbard and Jan Mayen", "Свалбард и Ян Майен"),
    "SK": ("Slovakia", "Словакия"),
    "SL": ("Sierra Leone", "Сиера Леоне"),
    "SM": ("San Marino", "Сан Марино"),
    "SN": ("Senegal", "Сенегал"),
    "SO": ("Somalia", "Сомалия"),
    "SR": ("Suriname", "Суринам"),
    "SS": ("South Sudan", "Южен Судан"),
    "ST": ("Sao Tome and Principe", "Сао Томе и Принсипи"),
    "SV": ("El Salvador", "Салвадор"),
    "SX": ("Sint Maarten (Dutch part)", "Синт Мартен"),
    "SY": ("Syrian Arab Republic", "Сирия"),
    "SZ": ("Eswatini", "Свазиленд"),
    "TC": ("Turks and Caicos Islands", "острови Търкс и Кайкос"),
    "TD": ("Chad", "Чад"),
    "TF": ("French Southern Territories", "Френски южни територии"),
    "TG": ("Togo", "Того"),
    "TH": ("Thailand", "Тайланд"),
    "TJ": ("Tajikistan", "Таджикистан"),
    "TK": ("Tokelau", "Токелау"),
    "TL": ("Timor-Leste", "Източен Тимор"),
    "TM": ("Turkmenistan", "Туркменистан"),
    "TN": ("Tunisia", "Тунис"),
    "TO": ("Tonga", "Тонга"),
    "TR": ("Türkiye", "Турция"),
    "TT": ("Trinidad and Tobago", "Тринидад и Тобаго"),
    "TV": ("Tuvalu", "Тувалу"),
    "TW": ("Taiwan, Province of China", "Тайван"),
    "TZ": ("Tanzania, United Republic of", "Танзания"),
    "UA": ("Ukraine", "Украйна"),
    "UG": ("Uganda", "Уганда"),
    "UM": ("United States Minor Outlying Islands", "Отдалечени острови на САЩ"),
    "US": ("United States", "САЩ"),
    "UY": ("Uruguay", "Уругвай"),
    "UZ": ("Uzbekistan", "Узбекистан"),
    "VA": ("Holy See", "Ватикан"),
    "VC": ("Saint Vincent and the Grenadines", "Сейнт Винсънт и Гренадини"),
    "VE": ("Venezuela, Bolivarian Republic of", "Венецуела"),
    "VG": ("Virgin Islands (British)", "Британски Вирджински острови"),
    "VI": ("Virgin Islands (U.S.)", "Американски Вирджински острови"),
    "VN": ("Viet Nam", "Виетнам"),
    "VU": ("Vanuatu", "Вануату"),
    "WF": ("Wallis and Futuna", "Уолис и Футуна"),
    "WS": ("Samoa", "Самоа"),
    "YE": ("Yemen", "Йемен"),
    "YT": ("Mayotte", "Майот"),
    "ZA": ("South Africa", "Южна Африка"),
    "ZM": ("Zambia", "Замбия"),
    "ZW": ("Zimbabwe", "Зимбабве"),
}

def _normalize_country_lookup_key(value: str) -> str:
    return re.sub(r"[^A-Za-zА-Яа-я0-9]+", " ", value, flags=re.UNICODE).strip().upper()


def _build_country_reverse_lookup() -> dict[str, str]:
    by_key: dict[str, set[str]] = {}
    for iso, (english, bulgarian) in COUNTRY_NAME_BY_ISO.items():
        for candidate in (iso, english, bulgarian):
            key = _normalize_country_lookup_key(candidate)
            if key == "":
                continue
            by_key.setdefault(key, set()).add(iso)

    resolved: dict[str, str] = {}
    for key, isos in by_key.items():
        if len(isos) == 1:
            resolved[key] = next(iter(isos))
    return resolved


COUNTRY_NAME_TO_ISO = _build_country_reverse_lookup()

DEFAULT_OUTPUT_DIR = OUTPUT_DIR / "ibkr" / "activity_statement"

ADDED_INTEREST_COLUMNS = [
    "Amount (EUR)",
    "Status",
]

ADDED_DIVIDENDS_COLUMNS = [
    "Country",
    "Amount (EUR)",
    "ISIN",
    "Appendix",
    "Status",
    "Review Status",
]

ADDED_WITHHOLDING_COLUMNS = [
    "Country",
    "Amount (EUR)",
    "ISIN",
    "Appendix",
    "Status",
    "Review Status",
]

ADDED_OPEN_POSITIONS_COLUMNS = [
    "Country",
    "Cost Basis (EUR)",
]

FxRateProvider = Callable[[str, date], Decimal]


class IbkrAnalyzerError(Exception):
    """Base error for IBKR analyzer failures."""


class CsvStructureError(IbkrAnalyzerError):
    """Raised when required sections/columns are missing."""


class FxConversionError(IbkrAnalyzerError):
    """Raised when FX conversion cannot be performed."""


@dataclass(slots=True)
class InstrumentListing:
    symbol: str
    canonical_symbol: str
    listing_exchange: str
    listing_exchange_normalized: str
    listing_exchange_class: str
    is_eu_listed: bool
    description: str
    isin: str


@dataclass(slots=True)
class BucketTotals:
    sale_price_eur: Decimal = ZERO
    purchase_eur: Decimal = ZERO
    wins_eur: Decimal = ZERO
    losses_eur: Decimal = ZERO
    rows: int = 0


@dataclass(slots=True)
class ReviewEntry:
    row_number: int
    symbol: str
    trade_date: str
    listing_exchange: str
    execution_exchange: str
    reason: str
    proceeds_eur: Decimal
    basis_eur: Decimal
    pnl_eur: Decimal


@dataclass(slots=True)
class Appendix8CountryTotals:
    country_iso: str
    country_english: str
    country_bulgarian: str
    gross_dividend_eur: Decimal = ZERO
    withholding_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8CompanyTotals:
    country_iso: str
    country_english: str
    country_bulgarian: str
    company_name: str
    gross_dividend_eur: Decimal = ZERO
    withholding_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8ComputedRow:
    payer_name: str
    country_iso: str
    country_english: str
    country_bulgarian: str
    method_code: str
    gross_dividend_eur: Decimal = ZERO
    foreign_tax_paid_eur: Decimal = ZERO
    bulgarian_tax_eur: Decimal = ZERO
    allowable_credit_eur: Decimal = ZERO
    recognized_credit_eur: Decimal = ZERO
    tax_due_bg_eur: Decimal = ZERO
    company_rows_count: int = 1


@dataclass(slots=True)
class Appendix8CountryDebugComputed:
    country_iso: str
    country_english: str
    country_bulgarian: str
    aggregated_gross_eur: Decimal = ZERO
    aggregated_foreign_tax_paid_eur: Decimal = ZERO
    bulgarian_tax_aggregated_eur: Decimal = ZERO
    credit_correct_eur: Decimal = ZERO
    credit_wrong_rowwise_eur: Decimal = ZERO
    delta_correct_minus_rowwise_eur: Decimal = ZERO
    tax_due_correct_eur: Decimal = ZERO
    tax_due_wrong_rowwise_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix8Part1Row:
    country_iso: str
    country_english: str
    country_bulgarian: str
    quantity: Decimal = ZERO
    acquisition_date: date = date.min
    cost_basis_original: Decimal = ZERO
    cost_basis_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix9CountryTotals:
    country_iso: str
    country_english: str
    country_bulgarian: str
    gross_interest_eur: Decimal = ZERO
    withholding_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class Appendix9CountryComputed:
    country_iso: str
    country_english: str
    country_bulgarian: str
    aggregated_gross_eur: Decimal = ZERO
    aggregated_foreign_tax_paid_eur: Decimal = ZERO
    allowable_credit_aggregated_eur: Decimal = ZERO
    recognized_credit_correct_eur: Decimal = ZERO
    recognized_credit_wrong_rowwise_eur: Decimal = ZERO
    delta_correct_minus_rowwise_eur: Decimal = ZERO


@dataclass(slots=True)
class _CountryCreditComponent:
    gross_eur: Decimal = ZERO
    foreign_tax_paid_eur: Decimal = ZERO


@dataclass(slots=True)
class AnalysisSummary:
    tax_year: int
    tax_exempt_mode: str
    appendix_5: BucketTotals = field(default_factory=BucketTotals)
    appendix_13: BucketTotals = field(default_factory=BucketTotals)
    review: BucketTotals = field(default_factory=BucketTotals)
    processed_trades_in_tax_year: int = 0
    trades_outside_tax_year: int = 0
    forex_ignored_rows: int = 0
    forex_ignored_abs_proceeds_eur: Decimal = ZERO
    ignored_non_closing_trade_rows: int = 0
    review_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    exchanges_used: set[str] = field(default_factory=set)
    review_exchanges: set[str] = field(default_factory=set)
    review_entries: list[ReviewEntry] = field(default_factory=list)
    review_required_rows: int = 0
    review_status_overrides_rows: int = 0
    unknown_review_status_rows: int = 0
    unknown_review_status_values: set[str] = field(default_factory=set)
    interest_processed_rows: int = 0
    interest_total_rows_skipped: int = 0
    interest_taxable_rows: int = 0
    interest_non_taxable_rows: int = 0
    interest_unknown_rows: int = 0
    interest_unknown_types: set[str] = field(default_factory=set)
    interest_unknown_descriptions: list[str] = field(default_factory=list)
    appendix_6_code_603_eur: Decimal = ZERO
    appendix_6_credit_interest_eur: Decimal = ZERO
    appendix_6_syep_interest_eur: Decimal = ZERO
    appendix_6_other_taxable_eur: Decimal = ZERO
    appendix_9_credit_interest_eur: Decimal = ZERO
    appendix_9_withholding_paid_eur: Decimal = ZERO
    appendix_9_withholding_source_found: bool = False
    appendix_9_by_country: dict[str, Appendix9CountryTotals] = field(default_factory=dict)
    appendix_9_country_results: dict[str, Appendix9CountryComputed] = field(default_factory=dict)
    appendix_6_lieu_received_eur: Decimal = ZERO
    dividend_tax_rate: Decimal = DIVIDEND_TAX_RATE
    dividends_processed_rows: int = 0
    dividends_total_rows_skipped: int = 0
    dividends_cash_rows: int = 0
    dividends_lieu_rows: int = 0
    dividends_unknown_rows: int = 0
    dividends_country_errors_rows: int = 0
    withholding_processed_rows: int = 0
    withholding_total_rows_skipped: int = 0
    withholding_dividend_rows: int = 0
    withholding_non_dividend_rows: int = 0
    withholding_country_errors_rows: int = 0
    appendix8_dividend_list_mode: str = APPENDIX8_LIST_MODE_COMPANY
    appendix_8_by_country: dict[str, Appendix8CountryTotals] = field(default_factory=dict)
    appendix_8_by_company: dict[tuple[str, str], Appendix8CompanyTotals] = field(default_factory=dict)
    appendix_8_company_results: list[Appendix8ComputedRow] = field(default_factory=list)
    appendix_8_output_rows: list[Appendix8ComputedRow] = field(default_factory=list)
    appendix_8_country_debug: dict[str, Appendix8CountryDebugComputed] = field(default_factory=dict)
    appendix_8_part1_rows: list[Appendix8Part1Row] = field(default_factory=list)
    open_positions_summary_rows: int = 0
    open_positions_part1_rows: int = 0
    tax_credit_debug_report_path: str = ""
    trades_data_rows_total: int = 0
    trade_discriminator_rows: int = 0
    closedlot_discriminator_rows: int = 0
    order_discriminator_rows: int = 0
    closing_trade_candidates: int = 0
    sanity_passed: bool = False
    sanity_checked_closing_trades: int = 0
    sanity_checked_closedlots: int = 0
    sanity_checked_subtotals: int = 0
    sanity_checked_totals: int = 0
    sanity_forex_ignored_rows: int = 0
    sanity_debug_artifacts_dir: str = ""
    sanity_debug_csv_path: str = ""
    sanity_report_path: str = ""
    sanity_failures_count: int = 0
    sanity_failure_messages: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisResult:
    input_csv_path: Path
    output_csv_path: Path
    declaration_txt_path: Path
    report_alias: str
    summary: AnalysisSummary


@dataclass(slots=True)
class _ActiveHeader:
    section: str
    row_number: int
    headers: list[str]


@dataclass(slots=True)
class _SanityFailure:
    check_type: str
    row_number: int | None
    row_kind: str
    asset_category: str
    symbol: str
    field_name: str
    expected: str
    actual: str
    difference: str
    details: str

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "check_type": self.check_type,
            "row_number": self.row_number,
            "row_kind": self.row_kind,
            "asset_category": self.asset_category,
            "symbol": self.symbol,
            "field_name": self.field_name,
            "expected": self.expected,
            "actual": self.actual,
            "difference": self.difference,
            "details": self.details,
        }

    def to_message(self) -> str:
        row = f"row {self.row_number}" if self.row_number is not None else "row n/a"
        symbol = self.symbol or "-"
        asset = self.asset_category or "-"
        return (
            f"{self.check_type}: {row} kind={self.row_kind} asset={asset} symbol={symbol} "
            f"field={self.field_name} expected={self.expected} actual={self.actual} "
            f"diff={self.difference} details={self.details}"
        )


@dataclass(slots=True)
class _SanityCheckResult:
    passed: bool
    checked_closing_trades: int
    checked_closedlots: int
    checked_subtotals: int
    checked_totals: int
    forex_ignored_rows: int
    debug_dir: Path
    debug_csv_path: Path
    report_path: Path
    failures: list[_SanityFailure]


def _fmt(value: Decimal, *, quant: Decimal | None = None) -> str:
    if quant is not None:
        value = value.quantize(quant, rounding=ROUND_HALF_UP)
    return format(value, "f")


def _parse_decimal(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        raise IbkrAnalyzerError(f"row {row_number}: missing {field_name}")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def _parse_decimal_or_zero(raw: str, *, row_number: int, field_name: str) -> Decimal:
    text = raw.strip()
    if text == "":
        return ZERO
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def _parse_trade_datetime(raw: str, *, row_number: int) -> datetime:
    text = raw.strip()
    for fmt in ("%Y-%m-%d, %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise IbkrAnalyzerError(f"row {row_number}: invalid Trade date/time format: {raw!r}")


def _normalize_report_alias(raw: str | None) -> str:
    if raw is None:
        return ""
    alias = raw.strip()
    if alias == "":
        return ""
    alias = re.sub(r"\s+", "_", alias)
    alias = re.sub(r"[^A-Za-z0-9._-]+", "", alias)
    alias = alias.strip("._-")
    if alias == "":
        raise IbkrAnalyzerError("report alias must contain at least one alphanumeric character")
    return alias


def _parse_closedlot_date(raw: str, *, row_number: int) -> date:
    text = raw.strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid ClosedLot date format: {raw!r}") from exc


def _normalize_exchange(raw: str) -> str:
    normalized = raw.strip().upper()
    if not normalized:
        return ""
    if normalized.startswith("EUIBSI"):
        return "EUIBSI"
    return EXCHANGE_ALIASES.get(normalized, normalized)


def _split_symbol_aliases(raw: str) -> list[str]:
    aliases = [part.strip().upper() for part in raw.split(",")]
    return [alias for alias in aliases if alias]


def _activate_header(section: str, row: list[str], *, row_number: int) -> _ActiveHeader:
    return _ActiveHeader(section=section, row_number=row_number, headers=row[2:])


def _build_active_headers(
    rows: list[list[str]],
) -> tuple[dict[int, _ActiveHeader], set[str]]:
    active_by_section: dict[str, _ActiveHeader] = {}
    active_for_row: dict[int, _ActiveHeader] = {}
    seen_headers: set[str] = set()

    for row_idx, row in enumerate(rows):
        if len(row) < 2:
            continue
        section = row[0]
        row_type = row[1]
        if row_type == "Header":
            active = _activate_header(section, row, row_number=row_idx + 1)
            active_by_section[section] = active
            seen_headers.add(section)
            continue
        active = active_by_section.get(section)
        if active is not None:
            active_for_row[row_idx] = active

    return active_for_row, seen_headers


def _classify_exchange(raw: str) -> str:
    normalized = _normalize_exchange(raw)
    if normalized in EU_REGULATED_MARKETS:
        return EXCHANGE_CLASS_EU_REGULATED
    if normalized in EU_NON_REGULATED:
        return EXCHANGE_CLASS_EU_NON_REGULATED
    return EXCHANGE_CLASS_UNKNOWN


def _index_for(headers: list[str], *candidates: str, section_name: str) -> int:
    normalized = {name.strip(): i for i, name in enumerate(headers)}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    raise CsvStructureError(f"{section_name}: missing required column; expected one of {candidates}")


def _optional_index(headers: list[str], *candidates: str) -> int | None:
    normalized = {name.strip(): i for i, name in enumerate(headers)}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


@dataclass(slots=True)
class _TradeFieldIndexes:
    asset: int
    currency: int
    symbol: int
    date_time: int
    exchange: int | None
    code: int
    proceeds: int
    basis: int | None
    discriminator: int
    commission: int | None
    review_status: int | None


@dataclass(slots=True)
class _InterestFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int
    review_status: int | None


@dataclass(slots=True)
class _OpenPositionsFieldIndexes:
    asset: int
    symbol: int
    quantity: int
    discriminator: int
    currency: int | None
    cost_basis: int | None
    country: int | None
    cost_basis_eur: int | None


@dataclass(slots=True)
class _TradeOrderFieldIndexes:
    asset: int
    symbol: int
    quantity: int
    discriminator: int


@dataclass(slots=True)
class _DividendsFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int
    country: int | None
    amount_eur: int | None
    isin: int | None
    appendix: int | None
    status: int | None
    review_status: int | None


@dataclass(slots=True)
class _WithholdingFieldIndexes:
    currency: int
    date: int
    description: int
    amount: int
    country: int | None
    amount_eur: int | None
    isin: int | None
    appendix: int | None
    status: int | None
    review_status: int | None


def _trade_indexes(active_header: _ActiveHeader) -> _TradeFieldIndexes:
    section_name = f"Trades header at row {active_header.row_number}"
    return _TradeFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        date_time=_index_for(active_header.headers, "Date/Time", section_name=section_name),
        exchange=_optional_index(active_header.headers, "Exchange", "Exch", "Execution Exchange"),
        code=_index_for(active_header.headers, "Code", section_name=section_name),
        proceeds=_index_for(active_header.headers, "Proceeds", section_name=section_name),
        basis=_optional_index(active_header.headers, "Basis", "Cost Basis", "CostBasis"),
        discriminator=_index_for(active_header.headers, "DataDiscriminator", section_name=section_name),
        commission=_optional_index(active_header.headers, "Comm/Fee", "Commission"),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _dividends_indexes(active_header: _ActiveHeader) -> _DividendsFieldIndexes:
    section_name = f"Dividends header at row {active_header.row_number}"
    return _DividendsFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
        country=_optional_index(active_header.headers, "Country"),
        amount_eur=_optional_index(active_header.headers, "Amount (EUR)"),
        isin=_optional_index(active_header.headers, "ISIN"),
        appendix=_optional_index(active_header.headers, "Appendix"),
        status=_optional_index(active_header.headers, "Status"),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _withholding_indexes(active_header: _ActiveHeader) -> _WithholdingFieldIndexes:
    section_name = f"Withholding Tax header at row {active_header.row_number}"
    return _WithholdingFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
        country=_optional_index(active_header.headers, "Country"),
        amount_eur=_optional_index(active_header.headers, "Amount (EUR)"),
        isin=_optional_index(active_header.headers, "ISIN"),
        appendix=_optional_index(active_header.headers, "Appendix"),
        status=_optional_index(active_header.headers, "Status"),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _interest_indexes(active_header: _ActiveHeader) -> _InterestFieldIndexes:
    section_name = f"Interest header at row {active_header.row_number}"
    return _InterestFieldIndexes(
        currency=_index_for(active_header.headers, "Currency", section_name=section_name),
        date=_index_for(active_header.headers, "Date", section_name=section_name),
        description=_index_for(active_header.headers, "Description", section_name=section_name),
        amount=_index_for(active_header.headers, "Amount", section_name=section_name),
        review_status=_optional_index(active_header.headers, "Review Status"),
    )


def _open_positions_indexes(active_header: _ActiveHeader) -> _OpenPositionsFieldIndexes:
    section_name = f"Open Positions header at row {active_header.row_number}"
    return _OpenPositionsFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        quantity=_index_for(active_header.headers, "Summary Quantity", "Quantity", section_name=section_name),
        discriminator=_index_for(active_header.headers, "DataDiscriminator", "Data Discriminator", section_name=section_name),
        currency=_optional_index(active_header.headers, "Currency", "Position Currency"),
        cost_basis=_optional_index(
            active_header.headers,
            "Cost Basis",
            "Cost Basis Money",
            "CostBasis",
            "Cost Basis Amount",
        ),
        country=_optional_index(active_header.headers, "Country"),
        cost_basis_eur=_optional_index(active_header.headers, "Cost Basis (EUR)"),
    )


def _trade_order_indexes(active_header: _ActiveHeader) -> _TradeOrderFieldIndexes:
    section_name = f"Trades header at row {active_header.row_number}"
    return _TradeOrderFieldIndexes(
        asset=_index_for(active_header.headers, "Asset Category", section_name=section_name),
        symbol=_index_for(active_header.headers, "Symbol", section_name=section_name),
        quantity=_index_for(active_header.headers, "Quantity", "Qty", section_name=section_name),
        discriminator=_index_for(active_header.headers, "DataDiscriminator", "Data Discriminator", section_name=section_name),
    )


def _normalize_review_status(raw: str) -> str:
    normalized = raw.strip().upper().replace("_", "-")
    normalized = re.sub(r"\s+", "-", normalized)
    if normalized == "NONTAXABLE":
        return REVIEW_STATUS_NON_TAXABLE
    return normalized


def _is_interest_total_row(currency: str) -> bool:
    return currency.strip().upper().startswith("TOTAL")


def _extract_isin(description: str) -> tuple[str | None, str | None]:
    matches = re.findall(r"\(([A-Za-z0-9]{12})\)", description)
    if not matches:
        return None, "missing ISIN in description"
    normalized = [item.upper() for item in matches if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", item.upper())]
    if len(normalized) == 1:
        return normalized[0], None
    if len(normalized) > 1:
        return None, "multiple ISIN candidates in description"
    return None, "invalid ISIN format in description"


def _extract_isin_from_text(raw: str) -> str:
    candidates = re.findall(r"\b([A-Z]{2}[A-Z0-9]{10})\b", raw.upper())
    if not candidates:
        return ""
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            return candidate
    return ""


def _extract_symbol_from_security_description(description: str) -> str | None:
    match = re.match(r"\s*([A-Za-z0-9._-]+)\s*\([A-Za-z0-9]{12}\)", description)
    if match is None:
        return None
    return match.group(1).upper()


def _resolve_country_from_isin(isin: str) -> tuple[str, str, str] | None:
    iso = isin[:2].upper()
    names = COUNTRY_NAME_BY_ISO.get(iso)
    if names is None:
        return None
    english, bulgarian = names
    return iso, english, bulgarian


def _resolve_country_from_text(country_text: str) -> tuple[str, str, str]:
    text = country_text.strip()
    if text == "":
        raise IbkrAnalyzerError("empty country value")
    normalized_text = _normalize_country_lookup_key(text)
    resolved_iso = COUNTRY_NAME_TO_ISO.get(normalized_text)
    if resolved_iso is not None:
        english, bulgarian = COUNTRY_NAME_BY_ISO[resolved_iso]
        return resolved_iso, english, bulgarian
    upper = text.upper()
    manual_iso = f"MANUAL:{upper}"
    return manual_iso, text, text


def _appendix9_default_country() -> tuple[str, str, str]:
    english, bulgarian = COUNTRY_NAME_BY_ISO[APPENDIX_9_DEFAULT_COUNTRY_ISO]
    return APPENDIX_9_DEFAULT_COUNTRY_ISO, english, bulgarian


def _parse_optional_decimal(raw: str, *, row_number: int, field_name: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise IbkrAnalyzerError(f"row {row_number}: invalid {field_name}: {raw!r}") from exc


def _classify_dividend_description(description: str) -> str:
    lowered = description.lower()
    if "cash dividend" in lowered:
        return DIVIDEND_APPENDIX_8
    if "lieu received" in lowered:
        return DIVIDEND_APPENDIX_6
    return DIVIDEND_APPENDIX_UNKNOWN


def _classify_status_from_description(description: str) -> str:
    lowered = description.lower()
    if "cash dividend" in lowered or "credit interest" in lowered or "lieu received" in lowered:
        return INTEREST_STATUS_TAXABLE
    return INTEREST_STATUS_UNKNOWN


def _extract_period_key_from_description(description: str, *, fallback: str) -> str:
    match = re.search(r"\bfor\s+(.+)$", description, flags=re.IGNORECASE)
    if match is None:
        return fallback
    period = re.sub(r"\s+", " ", match.group(1).strip())
    return period.upper() if period else fallback


def _country_component(
    components: dict[str, dict[str, _CountryCreditComponent]],
    *,
    country_iso: str,
    component_key: str,
) -> _CountryCreditComponent:
    country_components = components.get(country_iso)
    if country_components is None:
        country_components = {}
        components[country_iso] = country_components
    component = country_components.get(component_key)
    if component is None:
        component = _CountryCreditComponent()
        country_components[component_key] = component
    return component


def _sum_rowwise_wrong_credit(
    components: dict[str, _CountryCreditComponent],
    *,
    rate: Decimal,
) -> Decimal:
    return sum(
        (min(component.foreign_tax_paid_eur, component.gross_eur * rate) for component in components.values()),
        ZERO,
    )


def _determine_appendix8_method_code(*, foreign_withholding_paid_eur: Decimal | None) -> str:
    if foreign_withholding_paid_eur is None or foreign_withholding_paid_eur <= ZERO:
        return "3"
    return "1"


def _resolve_dividend_company_name(
    *,
    description: str,
    listings: dict[str, InstrumentListing],
) -> tuple[str | None, str | None]:
    symbol = _extract_symbol_from_security_description(description)
    if symbol is None:
        return None, "missing symbol token in description"
    instrument, normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
        asset_category="Stocks",
        trade_symbol=symbol,
        listings=listings,
    )
    if instrument is not None:
        description_value = instrument.description.strip()
        if description_value:
            return description_value, None
        return instrument.canonical_symbol, None
    if normalized_symbol:
        return normalized_symbol, forced_reason or "symbol was normalized without instrument mapping"
    return symbol, forced_reason or "symbol was not resolved via Financial Instrument Information"


def _compute_appendix8_company_results(
    *,
    totals_by_company: dict[tuple[str, str], Appendix8CompanyTotals],
    dividend_tax_rate: Decimal,
) -> list[Appendix8ComputedRow]:
    results: list[Appendix8ComputedRow] = []
    for _company_key, totals in sorted(
        totals_by_company.items(),
        key=lambda item: (item[1].country_iso, item[1].company_name),
    ):
        gross = totals.gross_dividend_eur
        foreign_tax = totals.withholding_tax_paid_eur
        bulgarian_tax = gross * dividend_tax_rate
        credit_correct = min(foreign_tax, bulgarian_tax)
        method_code = _determine_appendix8_method_code(
            foreign_withholding_paid_eur=foreign_tax,
        )
        results.append(
            Appendix8ComputedRow(
                payer_name=totals.company_name,
                country_iso=totals.country_iso,
                country_english=totals.country_english,
                country_bulgarian=totals.country_bulgarian,
                method_code=method_code,
                gross_dividend_eur=gross,
                foreign_tax_paid_eur=foreign_tax,
                bulgarian_tax_eur=bulgarian_tax,
                allowable_credit_eur=credit_correct,
                recognized_credit_eur=credit_correct,
                tax_due_bg_eur=bulgarian_tax - credit_correct,
                company_rows_count=1,
            )
        )
    return results


def _aggregate_appendix8_company_rows_by_country_and_method(
    *,
    company_rows: list[Appendix8ComputedRow],
) -> list[Appendix8ComputedRow]:
    buckets: dict[tuple[str, str], Appendix8ComputedRow] = {}
    for row in company_rows:
        key = (row.country_iso, row.method_code)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = Appendix8ComputedRow(
                payer_name=APPENDIX8_COUNTRY_MODE_PAYER_LABEL,
                country_iso=row.country_iso,
                country_english=row.country_english,
                country_bulgarian=row.country_bulgarian,
                method_code=row.method_code,
                company_rows_count=0,
            )
            buckets[key] = bucket
        bucket.gross_dividend_eur += row.gross_dividend_eur
        bucket.foreign_tax_paid_eur += row.foreign_tax_paid_eur
        bucket.bulgarian_tax_eur += row.bulgarian_tax_eur
        bucket.allowable_credit_eur += row.allowable_credit_eur
        bucket.recognized_credit_eur += row.recognized_credit_eur
        bucket.tax_due_bg_eur += row.tax_due_bg_eur
        bucket.company_rows_count += row.company_rows_count
    return sorted(buckets.values(), key=lambda item: (item.country_iso, item.method_code))


def _build_appendix8_country_debug(
    *,
    company_rows: list[Appendix8ComputedRow],
    dividend_tax_rate: Decimal,
) -> dict[str, Appendix8CountryDebugComputed]:
    aggregated: dict[str, Appendix8CountryDebugComputed] = {}
    for row in company_rows:
        current = aggregated.get(row.country_iso)
        if current is None:
            current = Appendix8CountryDebugComputed(
                country_iso=row.country_iso,
                country_english=row.country_english,
                country_bulgarian=row.country_bulgarian,
            )
            aggregated[row.country_iso] = current
        current.aggregated_gross_eur += row.gross_dividend_eur
        current.aggregated_foreign_tax_paid_eur += row.foreign_tax_paid_eur
        current.bulgarian_tax_aggregated_eur += row.bulgarian_tax_eur
        current.credit_correct_eur += row.recognized_credit_eur
        current.tax_due_correct_eur += row.tax_due_bg_eur

    for country_iso, current in aggregated.items():
        wrong_credit = min(
            current.aggregated_foreign_tax_paid_eur,
            current.aggregated_gross_eur * dividend_tax_rate,
        )
        current.credit_wrong_rowwise_eur = wrong_credit
        current.delta_correct_minus_rowwise_eur = current.credit_correct_eur - wrong_credit
        current.tax_due_wrong_rowwise_eur = current.bulgarian_tax_aggregated_eur - wrong_credit
        aggregated[country_iso] = current

    return aggregated


def _build_appendix8_part1_rows(
    *,
    totals_by_country: dict[str, Appendix8Part1Row],
) -> list[Appendix8Part1Row]:
    return sorted(
        totals_by_country.values(),
        key=lambda item: item.country_iso,
    )


def _compute_appendix9_country_results(
    *,
    totals_by_country: dict[str, Appendix9CountryTotals],
    components_by_country: dict[str, dict[str, _CountryCreditComponent]],
) -> dict[str, Appendix9CountryComputed]:
    results: dict[str, Appendix9CountryComputed] = {}
    for country_iso, totals in totals_by_country.items():
        gross = totals.gross_interest_eur
        foreign_tax = totals.withholding_tax_paid_eur
        allowable_credit = gross * APPENDIX_9_ALLOWABLE_CREDIT_RATE
        recognized_credit = min(foreign_tax, allowable_credit)
        rowwise_components = components_by_country.get(country_iso, {})
        recognized_wrong_rowwise = _sum_rowwise_wrong_credit(
            rowwise_components,
            rate=APPENDIX_9_ALLOWABLE_CREDIT_RATE,
        )
        results[country_iso] = Appendix9CountryComputed(
            country_iso=country_iso,
            country_english=totals.country_english,
            country_bulgarian=totals.country_bulgarian,
            aggregated_gross_eur=gross,
            aggregated_foreign_tax_paid_eur=foreign_tax,
            allowable_credit_aggregated_eur=allowable_credit,
            recognized_credit_correct_eur=recognized_credit,
            recognized_credit_wrong_rowwise_eur=recognized_wrong_rowwise,
            delta_correct_minus_rowwise_eur=recognized_credit - recognized_wrong_rowwise,
        )
    return results


def _write_tax_credit_debug_report(
    *,
    output_dir: Path,
    normalized_alias: str,
    tax_year: int,
    appendix8_company_rows: list[Appendix8ComputedRow],
    appendix8_country_debug: dict[str, Appendix8CountryDebugComputed],
    appendix8_output_rows: list[Appendix8ComputedRow],
    appendix8_list_mode: str,
    appendix9_results: dict[str, Appendix9CountryComputed],
) -> Path:
    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    debug_dir = output_dir / "_tax_credit_debug" / f"ibkr_activity{alias_suffix}_{tax_year}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    report_path = debug_dir / "tax_credit_country_debug.json"

    payload = {
        "note": "Debug diagnostics only. Not declaration-ready output.",
        "appendix_8_list_mode": appendix8_list_mode,
        "appendix_8_company_rows": [
            {
                "payer_name": item.payer_name,
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "method_code": item.method_code,
                "gross_dividend": _fmt(item.gross_dividend_eur),
                "foreign_tax_paid": _fmt(item.foreign_tax_paid_eur),
                "bulgarian_tax": _fmt(item.bulgarian_tax_eur),
                "allowable_credit": _fmt(item.allowable_credit_eur),
                "recognized_credit": _fmt(item.recognized_credit_eur),
                "tax_due_bg": _fmt(item.tax_due_bg_eur),
            }
            for item in appendix8_company_rows
        ],
        "appendix_8_country_debug": [
            {
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "aggregated_gross": _fmt(item.aggregated_gross_eur),
                "aggregated_foreign_tax_paid": _fmt(item.aggregated_foreign_tax_paid_eur),
                "bulgarian_tax_aggregated": _fmt(item.bulgarian_tax_aggregated_eur),
                "recognized_credit_sum_company": _fmt(item.credit_correct_eur),
                "recognized_credit_wrong_country_recomputed": _fmt(item.credit_wrong_rowwise_eur),
                "delta_correct_minus_wrong_country_recomputed": _fmt(item.delta_correct_minus_rowwise_eur),
                "tax_due_sum_company": _fmt(item.tax_due_correct_eur),
                "tax_due_wrong_country_recomputed": _fmt(item.tax_due_wrong_rowwise_eur),
            }
            for item in sorted(appendix8_country_debug.values(), key=lambda value: value.country_iso)
        ],
        "appendix_8_output_rows": [
            {
                "payer_name": item.payer_name,
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "method_code": item.method_code,
                "gross_dividend": _fmt(item.gross_dividend_eur),
                "foreign_tax_paid": _fmt(item.foreign_tax_paid_eur),
                "allowable_credit": _fmt(item.allowable_credit_eur),
                "recognized_credit": _fmt(item.recognized_credit_eur),
                "tax_due_bg": _fmt(item.tax_due_bg_eur),
                "company_rows_count": item.company_rows_count,
            }
            for item in appendix8_output_rows
        ],
        "appendix_9": [
            {
                "country_iso": item.country_iso,
                "country_english": item.country_english,
                "country_bulgarian": item.country_bulgarian,
                "aggregated_gross": _fmt(item.aggregated_gross_eur),
                "aggregated_foreign_tax_paid": _fmt(item.aggregated_foreign_tax_paid_eur),
                "allowable_credit_aggregated": _fmt(item.allowable_credit_aggregated_eur),
                "recognized_credit_correct": _fmt(item.recognized_credit_correct_eur),
                "recognized_credit_wrong_rowwise": _fmt(item.recognized_credit_wrong_rowwise_eur),
                "delta_correct_minus_rowwise": _fmt(item.delta_correct_minus_rowwise_eur),
            }
            for item in sorted(appendix9_results.values(), key=lambda value: value.country_iso)
        ],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def _parse_interest_date(raw: str, *, row_number: int) -> date:
    text = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d, %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise IbkrAnalyzerError(f"row {row_number}: invalid Interest date format: {raw!r}")


def _normalize_interest_type(description: str, *, currency: str) -> str:
    text = description.strip()
    if not text:
        return ""

    if text.upper().startswith(currency.strip().upper() + " "):
        text = text[len(currency.strip()) :].strip()
    else:
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[A-Z]{3,5}", parts[0].upper()):
            text = parts[1].strip()

    text = re.sub(r"\s+for\s+.+$", "", text, flags=re.IGNORECASE).strip()
    return text


def _classify_interest_type(normalized_type: str) -> str:
    if normalized_type in INTEREST_DECLARED_TYPES:
        return INTEREST_STATUS_TAXABLE
    if normalized_type in INTEREST_NON_DECLARED_TYPES:
        return INTEREST_STATUS_NON_TAXABLE
    return INTEREST_STATUS_UNKNOWN


def _code_has_closing_token(code: str) -> bool:
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", code.upper()) if token]
    return "C" in tokens


def _default_fx_provider(cache_dir: str | Path | None) -> FxRateProvider:
    def provider(currency: str, on_date: date) -> Decimal:
        normalized = currency.strip().upper()
        if normalized == "EUR":
            return Decimal("1")
        fx = get_exchange_rate(normalized, on_date, cache_dir=cache_dir)
        return fx.rate

    return provider


def _to_eur(amount: Decimal, currency: str, on_date: date, fx_provider: FxRateProvider, *, row_number: int) -> tuple[Decimal, Decimal]:
    normalized = currency.strip().upper()
    try:
        fx_rate = fx_provider(normalized, on_date)
    except Exception as exc:  # noqa: BLE001
        raise FxConversionError(
            f"row {row_number}: FX conversion failed for currency={normalized} on date={on_date.isoformat()}"
        ) from exc
    return amount * fx_rate, fx_rate


def _is_supported_asset(asset_category: str) -> bool:
    return asset_category.strip() in SUPPORTED_ASSET_CATEGORIES


def _is_forex_asset(asset_category: str) -> bool:
    return asset_category.strip() == FOREX_ASSET_CATEGORY


def _is_treasury_bills_asset(asset_category: str) -> bool:
    return asset_category.strip() == "Treasury Bills"


def _extract_treasury_bill_identifiers(raw_symbol: str) -> list[str]:
    # IBKR Treasury Bills symbols may include free text + embedded CUSIP-like token,
    # e.g. "...<br/>912797NP8 ...". We extract deterministic 9-char uppercase tokens.
    matches = re.findall(r"\b[A-Z0-9]{9}\b", raw_symbol.upper())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in matches:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _resolve_instrument_for_trade_symbol(
    *,
    asset_category: str,
    trade_symbol: str,
    listings: dict[str, InstrumentListing],
) -> tuple[InstrumentListing | None, str, str | None]:
    symbol_upper = trade_symbol.strip().upper()
    instrument = listings.get(symbol_upper)
    if instrument is not None:
        return instrument, "", None

    if not _is_treasury_bills_asset(asset_category):
        return None, "", None

    candidates = _extract_treasury_bill_identifiers(symbol_upper)
    if len(candidates) == 1:
        normalized_symbol = candidates[0]
        return listings.get(normalized_symbol), normalized_symbol, None
    if len(candidates) > 1:
        return (
            None,
            "",
            "Treasury Bills symbol contains multiple 9-char identifier candidates; manual review required",
        )
    return (
        None,
        "",
        "Treasury Bills symbol has no 9-char identifier candidate; manual review required",
    )


def _resolve_tax_target(
    *,
    tax_exempt_mode: str,
    symbol_is_eu_listed: bool | None,
    execution_exchange_class: str,
    missing_symbol_mapping: bool,
    forced_review_reason: str | None = None,
) -> tuple[str, str, bool]:
    if forced_review_reason is not None:
        if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
            return APPENDIX_REVIEW, forced_review_reason, True
        return APPENDIX_5, forced_review_reason, True

    if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL:
        if missing_symbol_mapping:
            return APPENDIX_5, "Missing symbol mapping", True
        if symbol_is_eu_listed:
            return APPENDIX_13, "EU-listed symbol (listed_symbol mode)", False
        return APPENDIX_5, "Non-EU-listed symbol", False

    if missing_symbol_mapping:
        return APPENDIX_REVIEW, "Missing symbol mapping", True

    if not symbol_is_eu_listed:
        return APPENDIX_5, "Non-EU-listed symbol", False

    if execution_exchange_class == EXCHANGE_CLASS_EU_REGULATED:
        return APPENDIX_13, "EU-listed + EU-regulated execution", False
    if execution_exchange_class == EXCHANGE_CLASS_EU_NON_REGULATED:
        return APPENDIX_REVIEW, "EU-listed + non-regulated execution", True
    return APPENDIX_REVIEW, "EU-listed + unknown execution", True


def _run_open_position_trade_quantity_reconciliation(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
) -> list[str]:
    warnings: list[str] = []
    open_qty_by_key: dict[tuple[str, str], Decimal] = {}
    trade_qty_by_key: dict[tuple[str, str], Decimal] = {}

    def add_qty(bucket: dict[tuple[str, str], Decimal], *, asset_category: str, canonical_symbol: str, quantity: Decimal) -> None:
        key = (asset_category, canonical_symbol)
        bucket[key] = bucket.get(key, ZERO) + quantity

    def canonical_symbol_for_row(*, asset_category: str, symbol_raw: str) -> tuple[str | None, str | None]:
        instrument, _normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if instrument is None:
            return None, forced_reason or "symbol was not resolved via Financial Instrument Information"
        return instrument.canonical_symbol, None

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Open Positions" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} reason=Open Positions row encountered before header"
            )
            continue
        try:
            field_idx = _open_positions_indexes(active_header)
        except CsvStructureError as exc:
            warnings.append(f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} reason={exc}")
            continue

        base_len = 2 + len(active_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        discriminator = data[field_idx.discriminator].strip().lower()
        if discriminator != "summary":
            continue
        asset_category = data[field_idx.asset].strip()
        if not _is_supported_asset(asset_category):
            continue
        symbol_raw = data[field_idx.symbol].strip()
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if quantity is None:
            warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason=invalid summary quantity"
            )
            continue
        canonical_symbol, resolve_error = canonical_symbol_for_row(
            asset_category=asset_category,
            symbol_raw=symbol_raw,
        )
        if canonical_symbol is None:
            warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason={resolve_error}"
            )
            continue
        add_qty(
            open_qty_by_key,
            asset_category=asset_category,
            canonical_symbol=canonical_symbol,
            quantity=quantity,
        )

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            warnings.append(
                f"{REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT}: row={row_number} reason=Trades row encountered before header"
            )
            continue
        try:
            field_idx = _trade_order_indexes(active_header)
        except CsvStructureError:
            continue

        base_len = 2 + len(active_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        discriminator = data[field_idx.discriminator].strip().lower()
        if discriminator != "order":
            continue
        asset_category = data[field_idx.asset].strip()
        if not _is_supported_asset(asset_category):
            continue
        symbol_raw = data[field_idx.symbol].strip()
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if quantity is None:
            warnings.append(
                f"{REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason=invalid order quantity"
            )
            continue
        canonical_symbol, resolve_error = canonical_symbol_for_row(
            asset_category=asset_category,
            symbol_raw=symbol_raw,
        )
        if canonical_symbol is None:
            warnings.append(
                f"{REVIEW_REASON_TRADE_UNMATCHED_INSTRUMENT}: row={row_number} asset={asset_category} symbol={symbol_raw!r} reason={resolve_error}"
            )
            continue
        add_qty(
            trade_qty_by_key,
            asset_category=asset_category,
            canonical_symbol=canonical_symbol,
            quantity=quantity,
        )

    for asset_category, canonical_symbol in sorted(set(open_qty_by_key) | set(trade_qty_by_key)):
        expected_open_qty = trade_qty_by_key.get((asset_category, canonical_symbol), ZERO)
        actual_open_qty = open_qty_by_key.get((asset_category, canonical_symbol), ZERO)
        diff = expected_open_qty - actual_open_qty
        if abs(diff) <= QTY_RECONCILIATION_EPSILON:
            continue
        warnings.append(
            f"{REVIEW_REASON_OPEN_POSITION_TRADE_QTY_MISMATCH}: "
            f"asset={asset_category} symbol={canonical_symbol} expected_open_qty={_fmt(expected_open_qty)} "
            f"actual_open_qty={_fmt(actual_open_qty)} diff={_fmt(diff)}"
        )

    return warnings


def _try_parse_decimal(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_reconciliation_quantity(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return ZERO
    cleaned = text.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _parse_decimal_loose_or_zero(raw: str) -> Decimal | None:
    text = raw.strip()
    if text == "":
        return ZERO
    cleaned = text.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _run_sanity_checks(
    *,
    rows: list[list[str]],
    active_headers: dict[int, _ActiveHeader],
    listings: dict[str, InstrumentListing],
    output_dir: Path,
    normalized_alias: str,
    tax_year: int,
) -> _SanityCheckResult:
    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    debug_dir = output_dir / "_sanity_debug" / f"ibkr_activity{alias_suffix}_{tax_year}"
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_csv_path = debug_dir / "ibkr_activity_modified_fx1_debug.csv"
    report_path = debug_dir / "sanity_report.json"
    tolerance = Decimal("0.01")

    failures: list[_SanityFailure] = []
    row_failure_reasons: dict[int, list[str]] = {}

    def add_failure(
        *,
        check_type: str,
        row_number: int | None,
        row_kind: str,
        asset_category: str,
        symbol: str,
        field_name: str,
        expected: Decimal | str,
        actual: Decimal | str,
        details: str,
    ) -> None:
        expected_str = _fmt(expected) if isinstance(expected, Decimal) else str(expected)
        actual_str = _fmt(actual) if isinstance(actual, Decimal) else str(actual)
        if isinstance(expected, Decimal) and isinstance(actual, Decimal):
            diff = expected - actual
            diff_str = _fmt(diff)
        else:
            diff_str = "-"
        failure = _SanityFailure(
            check_type=check_type,
            row_number=row_number,
            row_kind=row_kind,
            asset_category=asset_category,
            symbol=symbol,
            field_name=field_name,
            expected=expected_str,
            actual=actual_str,
            difference=diff_str,
            details=details,
        )
        failures.append(failure)
        if row_number is not None:
            row_failure_reasons.setdefault(row_number, []).append(failure.to_message())

    sanity_extras_by_row: dict[int, dict[str, str]] = {}
    sanity_row_kind_by_row: dict[int, str] = {}
    checked_trade_rows = 0
    checked_closedlots = 0
    checked_subtotals = 0
    checked_totals = 0
    forex_ignored_rows = 0

    symbol_agg: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    asset_agg: dict[tuple[str, str], dict[str, Decimal]] = {}

    def ensure_bucket(bucket: dict, key: tuple) -> dict[str, Decimal]:
        if key not in bucket:
            bucket[key] = {
                "proceeds": ZERO,
                "basis": ZERO,
                "comm_fee": ZERO,
                "sale_price": ZERO,
                "purchase_price": ZERO,
                "realized_pl": ZERO,
                "wins": ZERO,
                "losses": ZERO,
            }
        return bucket[key]

    def set_sanity_extras(row_idx: int, row_kind: str, values: dict[str, str]) -> None:
        existing = sanity_extras_by_row.get(row_idx, {})
        existing.update(values)
        sanity_extras_by_row[row_idx] = existing
        sanity_row_kind_by_row[row_idx] = row_kind

    subtotal_rows: list[dict[str, object]] = []
    total_rows: list[dict[str, object]] = []

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades":
            continue
        row_type = row[1]
        if row_type == "Header":
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            add_failure(
                check_type="ROW_FIELD_MISMATCH",
                row_number=row_number,
                row_kind=row_type,
                asset_category="",
                symbol="",
                field_name="active_header",
                expected="Trades header available",
                actual="missing",
                details="Trades row cannot be interpreted without active header",
            )
            continue

        field_idx = _trade_indexes(active_header)
        padded = row + [""] * (2 + len(active_header.headers) - len(row))
        data = padded[2 : 2 + len(active_header.headers)]
        asset_category = data[field_idx.asset].strip()
        currency = data[field_idx.currency].strip().upper()
        symbol_raw = data[field_idx.symbol].strip()
        symbol_upper = symbol_raw.upper()
        code = data[field_idx.code].strip()
        discriminator = data[field_idx.discriminator].strip().lower()

        if row_type == "Data" and discriminator == "trade":
            if _is_forex_asset(asset_category):
                forex_ignored_rows += 1
                continue
            if not _is_supported_asset(asset_category):
                continue

            proceeds = _try_parse_decimal(data[field_idx.proceeds]) or ZERO
            commission = (
                _try_parse_decimal(data[field_idx.commission]) or ZERO
                if field_idx.commission is not None
                else ZERO
            )
            realized_idx = _optional_index(
                active_header.headers,
                "Realized P/L",
                "Realized P&L",
                "Realized Profit and Loss",
                "RealizedProfitLoss",
            )
            realized_pl = _try_parse_decimal(data[realized_idx]) if realized_idx is not None else None
            trade_basis = _try_parse_decimal(data[field_idx.basis]) if field_idx.basis is not None else None

            instrument, normalized_symbol, _forced_reason = _resolve_instrument_for_trade_symbol(
                asset_category=asset_category,
                trade_symbol=symbol_raw,
                listings=listings,
            )
            if normalized_symbol:
                grouping_symbol = normalized_symbol
            elif instrument is not None:
                grouping_symbol = instrument.symbol
            else:
                grouping_symbol = symbol_upper

            closedlot_sum = ZERO
            closedlot_count_for_trade = 0
            scan_idx = row_idx + 1
            while scan_idx < len(rows):
                scan_row = rows[scan_idx]
                if len(scan_row) < 2 or scan_row[0] != "Trades" or scan_row[1] != "Data":
                    break
                scan_header = active_headers.get(scan_idx)
                if scan_header is None:
                    break
                scan_idxes = _trade_indexes(scan_header)
                scan_padded = scan_row + [""] * (2 + len(scan_header.headers) - len(scan_row))
                scan_data = scan_padded[2 : 2 + len(scan_header.headers)]
                scan_discriminator = scan_data[scan_idxes.discriminator].strip().lower()
                if scan_discriminator != "closedlot":
                    break
                closedlot_basis = _try_parse_decimal(scan_data[scan_idxes.basis]) if scan_idxes.basis is not None else None
                if closedlot_basis is not None:
                    closedlot_sum += closedlot_basis
                closedlot_count_for_trade += 1
                checked_closedlots += 1
                set_sanity_extras(
                    scan_idx,
                    "ClosedLot",
                    {
                        "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                        "Basis (EUR)": _fmt(closedlot_basis or ZERO, quant=DECIMAL_EIGHT),
                    },
                    )
                scan_idx += 1

            is_closing_trade = _code_has_closing_token(code)
            expected_trade_basis = -closedlot_sum
            if (
                is_closing_trade
                and trade_basis is not None
                and closedlot_count_for_trade > 0
                and trade_basis != expected_trade_basis
            ):
                add_failure(
                    check_type="BASIS_SIGN_MISMATCH",
                    row_number=row_number,
                    row_kind="Trade",
                    asset_category=asset_category,
                    symbol=grouping_symbol,
                    field_name="Basis",
                    expected=expected_trade_basis,
                    actual=trade_basis,
                    details="Trade.Basis must equal -sum(attached ClosedLot.Basis)",
                )

            if is_closing_trade and closedlot_count_for_trade > 0:
                basis_for_checks = expected_trade_basis
            else:
                basis_for_checks = trade_basis if trade_basis is not None else ZERO

            if is_closing_trade:
                expected_realized = proceeds + basis_for_checks + commission
                cash_leg = proceeds + commission
                if cash_leg >= ZERO:
                    sale_price_for_checks = abs(cash_leg)
                    purchase_price_for_checks = abs(basis_for_checks)
                else:
                    sale_price_for_checks = abs(basis_for_checks)
                    purchase_price_for_checks = abs(cash_leg)
                if realized_pl is not None and abs(expected_realized - realized_pl) > tolerance:
                    add_failure(
                        check_type="ROW_PNL_IDENTITY_MISMATCH",
                        row_number=row_number,
                        row_kind="Trade",
                        asset_category=asset_category,
                        symbol=grouping_symbol,
                        field_name="Realized P/L",
                        expected=expected_realized,
                        actual=realized_pl,
                        details="Expected Proceeds + Basis + Comm/Fee ~= Realized P/L",
                    )
                realized_for_checks = realized_pl if realized_pl is not None else expected_realized
            else:
                expected_realized = ZERO
                sale_price_for_checks = ZERO
                purchase_price_for_checks = ZERO
                if realized_pl is not None and abs(realized_pl) > tolerance:
                    add_failure(
                        check_type="ENTRY_REALIZED_NONZERO",
                        row_number=row_number,
                        row_kind="Trade",
                        asset_category=asset_category,
                        symbol=grouping_symbol,
                        field_name="Realized P/L",
                        expected=expected_realized,
                        actual=realized_pl,
                        details="Entry Trade rows are expected to have zero realized P/L",
                    )
                realized_for_checks = ZERO

            wins = realized_for_checks if realized_for_checks > 0 else ZERO
            losses = -realized_for_checks if realized_for_checks < 0 else ZERO

            if wins - losses != realized_for_checks:
                add_failure(
                    check_type="WINS_LOSSES_MISMATCH",
                    row_number=row_number,
                    row_kind="Trade",
                    asset_category=asset_category,
                    symbol=grouping_symbol,
                    field_name="Realized P/L",
                    expected=realized_for_checks,
                    actual=wins - losses,
                    details="Wins minus losses must equal realized P/L",
                )

            symbol_bucket = ensure_bucket(symbol_agg, (asset_category, currency, grouping_symbol))
            symbol_bucket["proceeds"] += proceeds
            symbol_bucket["basis"] += basis_for_checks
            symbol_bucket["comm_fee"] += commission
            symbol_bucket["sale_price"] += sale_price_for_checks
            symbol_bucket["purchase_price"] += purchase_price_for_checks
            symbol_bucket["realized_pl"] += realized_for_checks
            symbol_bucket["wins"] += wins
            symbol_bucket["losses"] += losses

            asset_bucket = ensure_bucket(asset_agg, (asset_category, currency))
            asset_bucket["proceeds"] += proceeds
            asset_bucket["basis"] += basis_for_checks
            asset_bucket["comm_fee"] += commission
            asset_bucket["sale_price"] += sale_price_for_checks
            asset_bucket["purchase_price"] += purchase_price_for_checks
            asset_bucket["realized_pl"] += realized_for_checks
            asset_bucket["wins"] += wins
            asset_bucket["losses"] += losses

            checked_trade_rows += 1
            set_sanity_extras(
                row_idx,
                "Trade",
                {
                    "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                    "Comm/Fee (EUR)": _fmt(commission, quant=DECIMAL_EIGHT),
                    "Proceeds (EUR)": _fmt(proceeds, quant=DECIMAL_EIGHT),
                    "Basis (EUR)": _fmt(basis_for_checks, quant=DECIMAL_EIGHT),
                    "Sale Price (EUR)": _fmt(sale_price_for_checks, quant=DECIMAL_EIGHT) if is_closing_trade else "",
                    "Purchase Price (EUR)": _fmt(purchase_price_for_checks, quant=DECIMAL_EIGHT) if is_closing_trade else "",
                    "Realized P/L (EUR)": _fmt(realized_for_checks, quant=DECIMAL_EIGHT),
                    "Realized P/L Wins (EUR)": _fmt(wins, quant=DECIMAL_EIGHT),
                    "Realized P/L Losses (EUR)": _fmt(losses, quant=DECIMAL_EIGHT),
                    "Normalized Symbol": normalized_symbol,
                },
            )
            continue

        if row_type in {"SubTotal", "Total"}:
            if _is_forex_asset(asset_category):
                continue
            if not _is_supported_asset(asset_category):
                continue

            subtotal_symbol = symbol_upper
            if row_type == "SubTotal":
                sub_instrument, sub_normalized_symbol, _sub_reason = _resolve_instrument_for_trade_symbol(
                    asset_category=asset_category,
                    trade_symbol=symbol_raw,
                    listings=listings,
                )
                if sub_normalized_symbol:
                    subtotal_symbol = sub_normalized_symbol
                elif sub_instrument is not None:
                    subtotal_symbol = sub_instrument.symbol

            proceeds_val = _try_parse_decimal(data[field_idx.proceeds])
            comm_idx = _optional_index(active_header.headers, "Comm/Fee", "Comm in EUR", "Commission")
            comm_val = _try_parse_decimal(data[comm_idx]) if comm_idx is not None else None
            basis_val = _try_parse_decimal(data[field_idx.basis]) if field_idx.basis is not None else None
            realized_idx = _optional_index(
                active_header.headers,
                "Realized P/L",
                "Realized P&L",
                "Realized Profit and Loss",
                "RealizedProfitLoss",
            )
            realized_val = _try_parse_decimal(data[realized_idx]) if realized_idx is not None else None

            container = subtotal_rows if row_type == "SubTotal" else total_rows
            container.append(
                {
                    "row_number": row_number,
                    "asset_category": asset_category,
                    "currency": currency,
                    "symbol": subtotal_symbol,
                    "proceeds": proceeds_val,
                    "basis": basis_val,
                    "comm_fee": comm_val,
                    "realized_pl": realized_val,
                    "row_kind": row_type,
                }
            )

    def _row_distance_to_expected(entry: dict[str, object], expected: dict[str, Decimal]) -> Decimal:
        distance = ZERO
        for field_name, agg_key in (
            ("proceeds", "proceeds"),
            ("basis", "basis"),
            ("comm_fee", "comm_fee"),
            ("realized_pl", "realized_pl"),
        ):
            row_val = entry[field_name]
            if isinstance(row_val, Decimal):
                distance += abs(expected[agg_key] - row_val)
        return distance

    selected_subtotals: list[dict[str, object]] = []
    subtotals_by_group: dict[tuple[str, str], list[dict[str, object]]] = {}
    for entry in subtotal_rows:
        key = (str(entry["asset_category"]), str(entry["symbol"]))
        subtotals_by_group.setdefault(key, []).append(entry)

    for (asset_category, symbol), group_entries in subtotals_by_group.items():
        non_eur_rows = [item for item in group_entries if str(item["currency"]).upper() != "EUR"]
        eur_rows = [item for item in group_entries if str(item["currency"]).upper() == "EUR"]

        selected_subtotals.extend(non_eur_rows)

        eur_expected = symbol_agg.get((asset_category, "EUR", symbol))
        if eur_expected is not None and eur_rows:
            best_eur_row = min(
                eur_rows,
                key=lambda item: _row_distance_to_expected(item, eur_expected),
            )
            selected_subtotals.append(best_eur_row)

    subtotal_seen: dict[tuple[str, str, str], int] = {}
    for entry in selected_subtotals:
        key = (str(entry["asset_category"]), str(entry["currency"]), str(entry["symbol"]))
        subtotal_seen[key] = subtotal_seen.get(key, 0) + 1
    for (asset_category, currency, symbol), count in subtotal_seen.items():
        if count > 1:
            add_failure(
                check_type="DUPLICATE_SUBTOTAL",
                row_number=None,
                row_kind="SubTotal",
                asset_category=asset_category,
                symbol=symbol,
                field_name="grouping key",
                expected="single subtotal row",
                actual=str(count),
                details=f"Duplicate SubTotal rows detected for currency={currency}",
            )

    selected_totals: list[dict[str, object]] = []
    totals_by_asset: dict[str, list[dict[str, object]]] = {}
    for entry in total_rows:
        totals_by_asset.setdefault(str(entry["asset_category"]), []).append(entry)

    for asset_category, group_entries in totals_by_asset.items():
        non_eur_rows = [item for item in group_entries if str(item["currency"]).upper() != "EUR"]
        eur_rows = [item for item in group_entries if str(item["currency"]).upper() == "EUR"]

        selected_totals.extend(non_eur_rows)

        eur_expected = asset_agg.get((asset_category, "EUR"))
        if eur_expected is not None and eur_rows:
            best_eur_row = min(
                eur_rows,
                key=lambda item: _row_distance_to_expected(item, eur_expected),
            )
            selected_totals.append(best_eur_row)

    total_seen: dict[tuple[str, str], int] = {}
    for entry in selected_totals:
        key = (str(entry["asset_category"]), str(entry["currency"]))
        total_seen[key] = total_seen.get(key, 0) + 1
    for (asset_category, currency), count in total_seen.items():
        if count > 1:
            add_failure(
                check_type="DUPLICATE_TOTAL",
                row_number=None,
                row_kind="Total",
                asset_category=asset_category,
                symbol="",
                field_name="grouping key",
                expected="single total row",
                actual=str(count),
                details=f"Duplicate Total rows detected for currency={currency}",
            )

    for entry in selected_subtotals:
        row_number = int(entry["row_number"])
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        symbol = str(entry["symbol"])
        agg = symbol_agg.get((asset_category, currency, symbol))
        if agg is None:
            add_failure(
                check_type="MISSING_SUBTOTAL",
                row_number=row_number,
                row_kind="SubTotal",
                asset_category=asset_category,
                symbol=symbol,
                field_name="aggregate",
                expected="trade aggregates for symbol exist",
                actual="missing",
                details="No matching Trade rows found for this subtotal row",
            )
            continue

        checked_subtotals += 1
        row_fail_before = len(failures)
        for field_name, agg_key in (
            ("Proceeds", "proceeds"),
            ("Basis", "basis"),
            ("Comm/Fee", "comm_fee"),
            ("Realized P/L", "realized_pl"),
        ):
            row_value = entry[agg_key]
            if not isinstance(row_value, Decimal):
                continue
            expected = agg[agg_key]
            if abs(expected - row_value) > tolerance:
                add_failure(
                    check_type="SUBTOTAL_MISMATCH",
                    row_number=row_number,
                    row_kind="SubTotal",
                    asset_category=asset_category,
                    symbol=symbol,
                    field_name=field_name,
                    expected=expected,
                    actual=row_value,
                    details="Subtotal does not match sum of Trade rows",
                )

        if agg["wins"] - agg["losses"] != agg["realized_pl"]:
            add_failure(
                check_type="WINS_LOSSES_MISMATCH",
                row_number=row_number,
                row_kind="SubTotal",
                asset_category=asset_category,
                symbol=symbol,
                field_name="Realized P/L",
                expected=agg["realized_pl"],
                actual=agg["wins"] - agg["losses"],
                details="Wins minus losses must equal realized P/L aggregate",
            )

        set_sanity_extras(
            row_number - 1,
            "SubTotal",
            {
                "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(agg["comm_fee"], quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(agg["proceeds"], quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(agg["basis"], quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(agg["sale_price"], quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(agg["purchase_price"], quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(agg["realized_pl"], quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(agg["wins"], quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(agg["losses"], quant=DECIMAL_EIGHT),
            },
        )

    for entry in selected_totals:
        row_number = int(entry["row_number"])
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        agg = asset_agg.get((asset_category, currency))
        if agg is None:
            add_failure(
                check_type="MISSING_TOTAL",
                row_number=row_number,
                row_kind="Total",
                asset_category=asset_category,
                symbol="",
                field_name="aggregate",
                expected="trade aggregates for asset category exist",
                actual="missing",
                details="No matching Trade rows found for this total row",
            )
            continue

        checked_totals += 1
        row_fail_before = len(failures)
        for field_name, agg_key in (
            ("Proceeds", "proceeds"),
            ("Basis", "basis"),
            ("Comm/Fee", "comm_fee"),
            ("Realized P/L", "realized_pl"),
        ):
            row_value = entry[agg_key]
            if not isinstance(row_value, Decimal):
                continue
            expected = agg[agg_key]
            if abs(expected - row_value) > tolerance:
                add_failure(
                    check_type="TOTAL_MISMATCH",
                    row_number=row_number,
                    row_kind="Total",
                    asset_category=asset_category,
                    symbol="",
                    field_name=field_name,
                    expected=expected,
                    actual=row_value,
                    details="Total does not match sum of Trade rows",
                )

        if agg["wins"] - agg["losses"] != agg["realized_pl"]:
            add_failure(
                check_type="WINS_LOSSES_MISMATCH",
                row_number=row_number,
                row_kind="Total",
                asset_category=asset_category,
                symbol="",
                field_name="Realized P/L",
                expected=agg["realized_pl"],
                actual=agg["wins"] - agg["losses"],
                details="Wins minus losses must equal realized P/L aggregate",
            )

        set_sanity_extras(
            row_number - 1,
            "Total",
            {
                "Fx Rate": _fmt(Decimal("1"), quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(agg["comm_fee"], quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(agg["proceeds"], quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(agg["basis"], quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(agg["sale_price"], quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(agg["purchase_price"], quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(agg["realized_pl"], quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(agg["wins"], quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(agg["losses"], quant=DECIMAL_EIGHT),
            },
        )

    debug_columns = [
        "DEBUG_SANITY_STATUS",
        "DEBUG_SANITY_ROW_KIND",
        "DEBUG_SANITY_FAILURES",
    ]
    sanity_output_rows: list[list[str]] = []
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Trades":
            sanity_output_rows.append(row)
            continue

        row_type = row[1]
        if row_type == "Header":
            sanity_output_rows.append(row + ADDED_TRADES_COLUMNS + debug_columns)
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            sanity_output_rows.append(row)
            continue

        base_len = 2 + len(active_header.headers)
        padded = row + [""] * (base_len - len(row))
        extras_map = sanity_extras_by_row.get(row_idx, {})
        extras = [extras_map.get(col, "") for col in ADDED_TRADES_COLUMNS]
        failures_for_row = row_failure_reasons.get(row_number, [])
        if failures_for_row:
            debug_status = "FAIL"
        elif row_idx in sanity_extras_by_row:
            debug_status = "PASS"
        else:
            debug_status = ""
        debug_kind = sanity_row_kind_by_row.get(row_idx, row_type)
        sanity_output_rows.append(
            padded
            + extras
            + [
                debug_status,
                debug_kind,
                " | ".join(failures_for_row),
            ]
        )

    with debug_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(sanity_output_rows)

    report_data = {
        "passed": len(failures) == 0,
        "checked_closing_trades": checked_trade_rows,
        "checked_closedlots": checked_closedlots,
        "checked_subtotals": checked_subtotals,
        "checked_totals": checked_totals,
        "forex_ignored_rows": forex_ignored_rows,
        "debug_csv_path": str(debug_csv_path),
        "failures_count": len(failures),
        "failures": [failure.to_dict() for failure in failures],
        "note": "Debug sanity artifacts are verification-only and not production tax outputs.",
    }
    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")

    return _SanityCheckResult(
        passed=len(failures) == 0,
        checked_closing_trades=checked_trade_rows,
        checked_closedlots=checked_closedlots,
        checked_subtotals=checked_subtotals,
        checked_totals=checked_totals,
        forex_ignored_rows=forex_ignored_rows,
        debug_dir=debug_dir,
        debug_csv_path=debug_csv_path,
        report_path=report_path,
        failures=failures,
    )


def _sum_bucket(bucket: BucketTotals, sale_price_eur: Decimal, purchase_eur: Decimal, pnl_eur: Decimal) -> None:
    bucket.sale_price_eur += sale_price_eur
    bucket.purchase_eur += purchase_eur

    if pnl_eur > 0:
        bucket.wins_eur += pnl_eur
    elif pnl_eur < 0:
        bucket.losses_eur += -pnl_eur
    bucket.rows += 1


def _build_declaration_text(result: AnalysisResult) -> str:
    summary = result.summary
    app5 = summary.appendix_5
    app13 = summary.appendix_13
    review = summary.review
    manual_check_reasons: list[str] = []
    if summary.sanity_failures_count > 0:
        manual_check_reasons.append(f"sanity checks failed: {summary.sanity_failures_count}")
    if summary.review_required_rows > 0:
        manual_check_reasons.append(f"има {summary.review_required_rows} записа с изисквана ръчна проверка")
    if summary.interest_unknown_rows > 0:
        manual_check_reasons.append(f"има {summary.interest_unknown_rows} записа с непознат вид лихва")
    if summary.dividends_unknown_rows > 0:
        manual_check_reasons.append(f"има {summary.dividends_unknown_rows} записа с неразпознат дивидентен ред")
    if summary.dividends_country_errors_rows > 0:
        manual_check_reasons.append(f"има {summary.dividends_country_errors_rows} дивидентни реда с невалиден ISIN/държава")
    if summary.withholding_country_errors_rows > 0:
        manual_check_reasons.append(f"има {summary.withholding_country_errors_rows} реда удържан данък с невалиден ISIN/държава")
    if summary.unknown_review_status_rows > 0:
        values = ", ".join(sorted(summary.unknown_review_status_values)) or "-"
        manual_check_reasons.append(
            f"има {summary.unknown_review_status_rows} записа с непознат Review Status ({values})"
        )
    if summary.forex_ignored_rows > 0:
        manual_check_reasons.append(f"има {summary.forex_ignored_rows} Forex записа, които са изключени")
    manual_check_required = bool(manual_check_reasons)

    lines: list[str] = []
    if manual_check_required:
        lines.append("!!! РЪЧНА ПРОВЕРКА / MANUAL CHECK !!!")
        lines.append("СТАТУС: REQUIRED")
        for reason in manual_check_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append("ПРОВЕРКА НА ИЗЧИСЛЕНИЯТА")
    lines.append(f"- Sanity checks: {'PASS' if summary.sanity_passed else 'FAIL'}")
    lines.append(f"- Проверени Trade редове (entry + exit): {summary.sanity_checked_closing_trades}")
    lines.append(f"- Проверени ClosedLot редове: {summary.sanity_checked_closedlots}")
    lines.append(f"- Проверени SubTotal редове: {summary.sanity_checked_subtotals}")
    lines.append(f"- Проверени Total редове: {summary.sanity_checked_totals}")
    lines.append(f"- Игнорирани Forex редове: {summary.sanity_forex_ignored_rows}")
    if summary.sanity_forex_ignored_rows > 0:
        lines.append("- ВНИМАНИЕ: Forex операциите не са включени в sanity проверките, защото са игнорирани от анализатора в тази версия.")
    if summary.sanity_debug_artifacts_dir:
        lines.append(f"- Sanity-check debug artifacts path: {summary.sanity_debug_artifacts_dir}")
        lines.append("- Debug artifacts are verification-only and not production tax outputs.")
    if summary.sanity_report_path:
        lines.append(f"- Sanity report: {summary.sanity_report_path}")
    if summary.sanity_failure_messages:
        lines.append("- Sanity diagnostics:")
        for item in summary.sanity_failure_messages[:20]:
            lines.append(f"  {item}")
    lines.append("")

    lines.append("Приложение 5")
    lines.append("Таблица 2")
    lines.append(f"- продажна цена (EUR) - код 508: {_fmt(app5.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- цена на придобиване (EUR) - код 508: {_fmt(app5.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- печалба (EUR) - код 508: {_fmt(app5.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR) - код 508: {_fmt(app5.losses_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- нетен резултат (EUR): {_fmt(app5.wins_eur - app5.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {app5.rows}")
    lines.append("")
    lines.append("Приложение 13")
    lines.append("Част ІІ")
    lines.append(f"- Брутен размер на дохода (EUR) - код 5081: {_fmt(app13.sale_price_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Цена на придобиване (EUR) - код 5081: {_fmt(app13.purchase_eur, quant=DECIMAL_TWO)}")
    lines.append("Информативни")
    lines.append(f"- печалба (EUR): {_fmt(app13.wins_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- загуба (EUR): {_fmt(app13.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- нетен резултат (EUR): {_fmt(app13.wins_eur - app13.losses_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- брой сделки: {app13.rows}")
    lines.append("")

    lines.append("Приложение 6")
    lines.append("Част I")
    lines.append("Информативни")
    lines.append(f"- Подател: Credit Interest (EUR): {_fmt(summary.appendix_6_credit_interest_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Подател: IBKR Managed Securities (SYEP) Interest (EUR): {_fmt(summary.appendix_6_syep_interest_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Подател: Other taxable (Review override) (EUR): {_fmt(summary.appendix_6_other_taxable_eur, quant=DECIMAL_TWO)}")
    lines.append(f"- Подател: Lieu Received (EUR): {_fmt(summary.appendix_6_lieu_received_eur, quant=DECIMAL_TWO)}")
    lines.append("Декларационна стойност")
    lines.append(f"- Обща сума на доходите с код 603: {_fmt(summary.appendix_6_code_603_eur, quant=DECIMAL_TWO)}")
    if summary.interest_unknown_rows > 0:
        lines.append("- НУЖЕН Е ПРЕГЛЕД: открити са непознати видове лихви")
        lines.append(f"- брой непознати редове: {summary.interest_unknown_rows}")
        lines.append(f"- непознати видове: {', '.join(sorted(summary.interest_unknown_types))}")
    if summary.dividends_unknown_rows > 0:
        lines.append("- НУЖЕН Е ПРЕГЛЕД: открити са неразпознати дивидентни описания")
        lines.append(f"- брой неразпознати редове: {summary.dividends_unknown_rows}")
    lines.append("")

    lines.append("Приложение 8")
    lines.append("Част І, Акции, ред 1.N")
    if summary.appendix_8_part1_rows:
        for idx, part1 in enumerate(summary.appendix_8_part1_rows, start=1):
            lines.append(f"- Приложение 8, Част І, Акции, ред 1.{idx}")
            lines.append("- Вид: Акции")
            lines.append(f"- Държава: {part1.country_bulgarian}")
            lines.append(f"- Брой: {_fmt(part1.quantity)}")
            lines.append(
                f"- Дата и година на придобиване: {part1.acquisition_date.strftime('%d.%m.%Y')}"
            )
            lines.append(
                f"- Обща цена на придобиване в съответната валута: "
                f"{_fmt(part1.cost_basis_original, quant=DECIMAL_TWO)}"
            )
            lines.append(f"- В EUR: {_fmt(part1.cost_basis_eur, quant=DECIMAL_TWO)}")
            lines.append("")
    else:
        lines.append("- Няма разпознаваеми Open Positions Summary записи за данъчната година")
        lines.append("")
    lines.append("Напомняне: Към Приложение 8, Част I следва да се приложи файл с open positions.")
    lines.append("")

    lines.append("Част III, ред 1.N")
    if summary.appendix_8_output_rows:
        for bucket in summary.appendix_8_output_rows:
            lines.append(
                f"- Наименование на лицето, изплатило дохода: {bucket.payer_name}"
            )
            lines.append(f"- Държава: {bucket.country_bulgarian}")
            lines.append("- Код вид доход: 8141")
            lines.append(f"- Код за прилагане на метод за избягване на двойното данъчно облагане: {bucket.method_code}")
            lines.append(f"- Брутен размер на дохода: {_fmt(bucket.gross_dividend_eur, quant=DECIMAL_TWO)}")
            lines.append("- Документално доказана цена на придобиване: ")
            lines.append(f"- Платен данък в чужбина: {_fmt(bucket.foreign_tax_paid_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Допустим размер на данъчния кредит: {_fmt(bucket.allowable_credit_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Размер на признатия данъчен кредит: {_fmt(bucket.recognized_credit_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Дължим данък, подлежащ на внасяне: {_fmt(bucket.tax_due_bg_eur, quant=DECIMAL_TWO)}")
            lines.append("")
    else:
        lines.append("- Няма разпознаваеми Cash Dividend записи за данъчната година")
        lines.append("")

    lines.append("Приложение 9")
    lines.append("Част II")
    if summary.appendix_9_country_results:
        for country_iso in sorted(summary.appendix_9_country_results):
            country_result = summary.appendix_9_country_results[country_iso]
            lines.append(f"- Държава: {country_result.country_bulgarian}")
            lines.append("- Код вид доход: 603")
            lines.append(
                f"- Брутен размер на дохода (включително платеният данък): "
                f"{_fmt(country_result.aggregated_gross_eur, quant=DECIMAL_TWO)}"
            )
            lines.append("- Нормативно определени разходи: 0")
            lines.append("- Задължителни осигурителни вноски: 0")
            lines.append(f"- Годишна данъчна основа: {_fmt(country_result.aggregated_gross_eur, quant=DECIMAL_TWO)}")
            lines.append(f"- Платен данък в чужбина: {_fmt(country_result.aggregated_foreign_tax_paid_eur, quant=DECIMAL_TWO)}")
            lines.append(
                f"- Допустим размер на данъчния кредит: "
                f"{_fmt(country_result.allowable_credit_aggregated_eur, quant=DECIMAL_TWO)}"
            )
            lines.append(
                f"- Размер на признатия данъчен кредит: "
                f"{_fmt(country_result.recognized_credit_correct_eur, quant=DECIMAL_TWO)}"
            )
            lines.append("- № и дата на документа за дохода и съответния данък: R-185 / Activity Statement")
            lines.append("")
    else:
        lines.append("- Държава: Ирландия")
        lines.append("- Код вид доход: 603")
        lines.append(
            f"- Брутен размер на дохода (включително платеният данък): "
            f"{_fmt(summary.appendix_9_credit_interest_eur, quant=DECIMAL_TWO)}"
        )
        lines.append("- Нормативно определени разходи: 0")
        lines.append("- Задължителни осигурителни вноски: 0")
        lines.append(f"- Годишна данъчна основа: {_fmt(summary.appendix_9_credit_interest_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- Платен данък в чужбина: {_fmt(summary.appendix_9_withholding_paid_eur, quant=DECIMAL_TWO)}")
        lines.append(
            f"- Допустим размер на данъчния кредит: "
            f"{_fmt(summary.appendix_9_credit_interest_eur * APPENDIX_9_ALLOWABLE_CREDIT_RATE, quant=DECIMAL_TWO)}"
        )
        lines.append(
            f"- Размер на признатия данъчен кредит: "
            f"{_fmt(min(summary.appendix_9_withholding_paid_eur, summary.appendix_9_credit_interest_eur * APPENDIX_9_ALLOWABLE_CREDIT_RATE), quant=DECIMAL_TWO)}"
        )
        lines.append("- № и дата на документа за дохода и съответния данък: R-185 / Activity Statement")
    lines.append("")

    if summary.tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE:
        lines.append("РЪЧНА ПРОВЕРКА (ИЗКЛЮЧЕНИ ОТ АВТОМАТИЧНИТЕ ТАБЛИЦИ)")
        lines.append(f"- изключени записи: {summary.review_rows}")
        lines.append(f"- продажна цена (EUR): {_fmt(review.sale_price_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- цена на придобиване (EUR): {_fmt(review.purchase_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- печалба (EUR): {_fmt(review.wins_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- загуба (EUR): {_fmt(review.losses_eur, quant=DECIMAL_TWO)}")
        lines.append(f"- нетен резултат (EUR): {_fmt(review.wins_eur - review.losses_eur, quant=DECIMAL_TWO)}")
        lines.append("")
        for entry in summary.review_entries:
            lines.append(
                "- row={row} symbol={symbol} date={dt} listing={listing} execution={execution} "
                "reason={reason} proceeds_eur={proceeds} basis_eur={basis} pnl_eur={pnl}".format(
                    row=entry.row_number,
                    symbol=entry.symbol,
                    dt=entry.trade_date,
                    listing=entry.listing_exchange,
                    execution=entry.execution_exchange,
                    reason=entry.reason,
                    proceeds=_fmt(entry.proceeds_eur, quant=DECIMAL_TWO),
                    basis=_fmt(entry.basis_eur, quant=DECIMAL_TWO),
                    pnl=_fmt(entry.pnl_eur, quant=DECIMAL_TWO),
                )
            )
        lines.append("")

    lines.append("ВНИМАНИЕ: FOREX ОПЕРАЦИИ")
    lines.append("- Forex сделки (конвертиране на валута или търговия) НЕ са включени в изчисленията за Приложение 5 и Приложение 13")
    lines.append("- Тези операции са игнорирани от анализатора в тази версия")
    lines.append("- При наличие на значителни Forex операции е необходима ръчна проверка")
    lines.append(f"- брой Forex записи: {summary.forex_ignored_rows}")
    lines.append(f"- общ обем (EUR): {_fmt(summary.forex_ignored_abs_proceeds_eur, quant=DECIMAL_TWO)}")
    lines.append("")

    lines.append("Доказателствена част")
    lines.append(f"- избран режим: {summary.tax_exempt_mode}")
    lines.append(f"- Приложение 8 дивидентен режим: {summary.appendix8_dividend_list_mode}")
    lines.append(f"- report alias: {result.report_alias or '-'}")
    lines.append(f"- данъчна година: {summary.tax_year}")
    lines.append(f"- обработени сделки (в данъчната година): {summary.processed_trades_in_tax_year}")
    lines.append(f"- сделки извън данъчната година: {summary.trades_outside_tax_year}")
    lines.append(f"- игнорирани редове без token C: {summary.ignored_non_closing_trade_rows}")
    lines.append(f"- review overrides (TAXABLE/NON-TAXABLE): {summary.review_status_overrides_rows}")
    lines.append(f"- unknown Review Status rows: {summary.unknown_review_status_rows}")
    if summary.unknown_review_status_values:
        lines.append(f"- unknown Review Status values: {', '.join(sorted(summary.unknown_review_status_values))}")
    lines.append(f"- interest processed rows: {summary.interest_processed_rows}")
    lines.append(f"- interest total rows skipped: {summary.interest_total_rows_skipped}")
    lines.append(f"- interest taxable rows: {summary.interest_taxable_rows}")
    lines.append(f"- interest non-taxable rows: {summary.interest_non_taxable_rows}")
    lines.append(f"- interest unknown rows: {summary.interest_unknown_rows}")
    lines.append(f"- dividends processed rows: {summary.dividends_processed_rows}")
    lines.append(f"- dividends total rows skipped: {summary.dividends_total_rows_skipped}")
    lines.append(f"- dividends cash rows: {summary.dividends_cash_rows}")
    lines.append(f"- dividends lieu rows: {summary.dividends_lieu_rows}")
    lines.append(f"- dividends unknown rows: {summary.dividends_unknown_rows}")
    lines.append(f"- withholding processed rows: {summary.withholding_processed_rows}")
    lines.append(f"- withholding total rows skipped: {summary.withholding_total_rows_skipped}")
    lines.append(f"- withholding dividend rows: {summary.withholding_dividend_rows}")
    lines.append(f"- withholding non-dividend rows: {summary.withholding_non_dividend_rows}")
    lines.append(f"- open positions summary rows: {summary.open_positions_summary_rows}")
    lines.append(f"- Appendix 8 Part I rows: {summary.open_positions_part1_rows}")
    lines.append(f"- dividend tax rate: {_fmt(summary.dividend_tax_rate)}")
    lines.append(
        "- interest withholding source found: "
        + ("YES" if summary.appendix_9_withholding_source_found else "NO")
    )
    if summary.tax_credit_debug_report_path:
        lines.append(f"- tax credit debug report: {summary.tax_credit_debug_report_path}")
    lines.append(f"- използвани execution борси: {', '.join(sorted(summary.exchanges_used)) or '-'}")
    lines.append(f"- review execution борси: {', '.join(sorted(summary.review_exchanges)) or '-'}")
    lines.append("")

    if summary.warnings:
        lines.append("Warnings")
        for warning in summary.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _parse_instrument_listings(rows: list[list[str]]) -> dict[str, InstrumentListing]:
    active_headers, seen_headers = _build_active_headers(rows)
    return _parse_instrument_listings_with_headers(
        rows,
        active_headers=active_headers,
        seen_headers=seen_headers,
    )


def _parse_instrument_listings_with_headers(
    rows: list[list[str]],
    *,
    active_headers: dict[int, _ActiveHeader],
    seen_headers: set[str],
) -> dict[str, InstrumentListing]:
    section_name = "Financial Instrument Information"
    listings: dict[str, InstrumentListing] = {}

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != section_name or row[1] != "Data":
            continue

        active_header = active_headers.get(row_idx)
        if active_header is None:
            raise CsvStructureError(
                f"row {row_number}: {section_name} Data row encountered before {section_name} Header"
            )

        asset_idx = _index_for(active_header.headers, "Asset Category", section_name=f"{section_name} header at row {active_header.row_number}")
        symbol_idx = _index_for(active_header.headers, "Symbol", section_name=f"{section_name} header at row {active_header.row_number}")
        listing_idx = _index_for(active_header.headers, "Listing Exch", section_name=f"{section_name} header at row {active_header.row_number}")
        description_idx = _optional_index(active_header.headers, "Description", "Financial Instrument Description", "Name")
        isin_idx = _optional_index(
            active_header.headers,
            "ISIN",
            "Security ID",
            "SecurityID",
            "Security Id",
        )

        data = row[2:] + [""] * (len(active_header.headers) - len(row[2:]))
        asset_category = data[asset_idx].strip()
        if asset_category not in SUPPORTED_ASSET_CATEGORIES:
            continue
        raw_symbol = data[symbol_idx].strip()
        symbols = _split_symbol_aliases(raw_symbol)
        if _is_treasury_bills_asset(asset_category):
            # Treasury Bills symbols can be verbose; keep deterministic 9-char
            # identifier aliases so later symbol resolution can match reliably.
            for token in _extract_treasury_bill_identifiers(raw_symbol):
                if token not in symbols:
                    symbols.append(token)
        if not symbols:
            raise CsvStructureError(f"row {row_number}: empty symbol in Financial Instrument Information")

        listing_exchange = data[listing_idx].strip()
        instrument_description = data[description_idx].strip() if description_idx is not None else ""
        instrument_isin = (
            _extract_isin_from_text(data[isin_idx].strip()) if isin_idx is not None else ""
        )
        if instrument_isin == "" and instrument_description != "":
            instrument_isin = _extract_isin_from_text(instrument_description)
        listing_exchange_normalized = _normalize_exchange(listing_exchange)
        listing_class = _classify_exchange(listing_exchange)
        is_eu_listed = listing_class == EXCHANGE_CLASS_EU_REGULATED

        canonical_symbol = symbols[0]
        for symbol in symbols:
            new_item = InstrumentListing(
                symbol=symbol,
                canonical_symbol=canonical_symbol,
                listing_exchange=listing_exchange,
                listing_exchange_normalized=listing_exchange_normalized,
                listing_exchange_class=listing_class,
                is_eu_listed=is_eu_listed,
                description=instrument_description,
                isin=instrument_isin,
            )

            existing = listings.get(symbol)
            if existing is None:
                listings[symbol] = new_item
                continue

            if existing.listing_exchange_normalized == new_item.listing_exchange_normalized:
                continue
            if existing.is_eu_listed != new_item.is_eu_listed:
                raise CsvStructureError(
                    f"row {row_number}: conflicting symbol mapping for {symbol}: "
                    f"{existing.listing_exchange_normalized} vs {new_item.listing_exchange_normalized}"
                )
            # Same EU/non-EU classification, keep first mapping deterministically.

    if section_name not in seen_headers:
        raise CsvStructureError(f"missing section header: {section_name}")
    if not listings:
        raise CsvStructureError("Financial Instrument Information section has no supported symbol mappings")
    return listings


def _extract_interest_withholding_paid_eur(
    rows: list[list[str]],
    *,
    active_headers: dict[int, _ActiveHeader],
) -> tuple[Decimal, bool]:
    section_name = "Mark-to-Market Performance Summary"
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != section_name or row[1] != "Data":
            continue
        active_header = active_headers.get(row_idx)
        if active_header is None:
            raise CsvStructureError(f"row {row_number}: {section_name} Data row encountered before {section_name} Header")

        section_label = f"{section_name} header at row {active_header.row_number}"
        asset_idx = _index_for(active_header.headers, "Asset Category", section_name=section_label)
        total_idx = _index_for(active_header.headers, "Mark-to-Market P/L Total", section_name=section_label)
        padded = row[2:] + [""] * (len(active_header.headers) - len(row[2:]))
        asset_category = padded[asset_idx].strip()
        if asset_category != "Withholding on Interest Received":
            continue
        value = _parse_decimal(padded[total_idx], row_number=row_number, field_name="Mark-to-Market P/L Total")
        return abs(value), True
    return ZERO, False


def analyze_ibkr_activity_statement(
    *,
    input_csv: str | Path,
    tax_year: int,
    tax_exempt_mode: Literal["listed_symbol", "execution_exchange"],
    appendix8_dividend_list_mode: Literal["company", "country"] = APPENDIX8_LIST_MODE_COMPANY,
    report_alias: str | None = None,
    output_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    fx_rate_provider: FxRateProvider | None = None,
) -> AnalysisResult:
    if tax_year < 2009 or tax_year > 2100:
        raise IbkrAnalyzerError(f"invalid tax year: {tax_year}")

    if tax_exempt_mode not in {TAX_MODE_LISTED_SYMBOL, TAX_MODE_EXECUTION_EXCHANGE}:
        raise IbkrAnalyzerError(f"unsupported tax exempt mode: {tax_exempt_mode}")
    if appendix8_dividend_list_mode not in {
        APPENDIX8_LIST_MODE_COMPANY,
        APPENDIX8_LIST_MODE_COUNTRY,
    }:
        raise IbkrAnalyzerError(
            f"unsupported Appendix 8 dividend list mode: {appendix8_dividend_list_mode}"
        )

    input_path = Path(input_csv).expanduser().resolve()
    if not input_path.exists():
        raise IbkrAnalyzerError(f"input CSV does not exist: {input_path}")
    normalized_alias = _normalize_report_alias(report_alias)

    out_dir = (Path(output_dir).expanduser() if output_dir is not None else DEFAULT_OUTPUT_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    fx_provider = fx_rate_provider if fx_rate_provider is not None else _default_fx_provider(cache_dir)

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise CsvStructureError("empty CSV input")

    active_headers, seen_headers = _build_active_headers(rows)
    listings = _parse_instrument_listings_with_headers(
        rows,
        active_headers=active_headers,
        seen_headers=seen_headers,
    )
    trades_row_extras: dict[int, list[str]] = {}
    trades_row_base_len: dict[int, int] = {}
    interest_row_extras: dict[int, list[str]] = {}
    interest_row_base_len: dict[int, int] = {}
    dividends_row_extras: dict[int, dict[str, str]] = {}
    dividends_row_base_len: dict[int, int] = {}
    withholding_row_extras: dict[int, dict[str, str]] = {}
    withholding_row_base_len: dict[int, int] = {}
    open_positions_row_extras: dict[int, dict[str, str]] = {}
    open_positions_row_base_len: dict[int, int] = {}
    dividends_row_added_columns: dict[int, list[str]] = {}
    withholding_row_added_columns: dict[int, list[str]] = {}
    open_positions_row_added_columns: dict[int, list[str]] = {}
    summary = AnalysisSummary(
        tax_year=tax_year,
        tax_exempt_mode=tax_exempt_mode,
        dividend_tax_rate=DIVIDEND_TAX_RATE,
        appendix8_dividend_list_mode=appendix8_dividend_list_mode,
    )
    reconciliation_warnings = _run_open_position_trade_quantity_reconciliation(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
    )
    summary.review_required_rows += len(reconciliation_warnings)
    summary.warnings.extend(reconciliation_warnings)

    def _set_trade_extras(row_idx: int, values: dict[str, str]) -> None:
        extras = [""] * len(ADDED_TRADES_COLUMNS)
        for key, value in values.items():
            extras[ADDED_TRADES_COLUMNS.index(key)] = value
        trades_row_extras[row_idx] = extras

    def _set_interest_extras(row_idx: int, values: dict[str, str]) -> None:
        extras = [""] * len(ADDED_INTEREST_COLUMNS)
        for key, value in values.items():
            extras[ADDED_INTEREST_COLUMNS.index(key)] = value
        interest_row_extras[row_idx] = extras

    def _set_dividends_extras(row_idx: int, values: dict[str, str]) -> None:
        existing = dividends_row_extras.get(row_idx, {})
        for key, value in values.items():
            existing[key] = value
        dividends_row_extras[row_idx] = existing

    def _set_withholding_extras(row_idx: int, values: dict[str, str]) -> None:
        existing = withholding_row_extras.get(row_idx, {})
        for key, value in values.items():
            existing[key] = value
        withholding_row_extras[row_idx] = existing

    def _set_open_positions_extras(row_idx: int, values: dict[str, str]) -> None:
        existing = open_positions_row_extras.get(row_idx, {})
        for key, value in values.items():
            existing[key] = value
        open_positions_row_extras[row_idx] = existing

    def _set_existing_section_value(
        *,
        row_idx: int,
        active_header: _ActiveHeader,
        field_idx: int | None,
        value: str,
        only_if_empty: bool,
    ) -> None:
        if field_idx is None:
            return
        base_len = 2 + len(active_header.headers)
        row = rows[row_idx]
        if len(row) < base_len:
            row.extend([""] * (base_len - len(row)))
        current = row[2 + field_idx].strip()
        if only_if_empty and current != "":
            return
        row[2 + field_idx] = value

    def _appendix8_bucket(country_iso: str, country_english: str, country_bulgarian: str) -> Appendix8CountryTotals:
        bucket = summary.appendix_8_by_country.get(country_iso)
        if bucket is None:
            bucket = Appendix8CountryTotals(
                country_iso=country_iso,
                country_english=country_english,
                country_bulgarian=country_bulgarian,
            )
            summary.appendix_8_by_country[country_iso] = bucket
        return bucket

    def _appendix8_company_bucket(
        *,
        country_iso: str,
        country_english: str,
        country_bulgarian: str,
        company_name: str,
    ) -> Appendix8CompanyTotals:
        key = (country_iso, company_name)
        bucket = summary.appendix_8_by_company.get(key)
        if bucket is None:
            bucket = Appendix8CompanyTotals(
                country_iso=country_iso,
                country_english=country_english,
                country_bulgarian=country_bulgarian,
                company_name=company_name,
            )
            summary.appendix_8_by_company[key] = bucket
        return bucket

    def _appendix9_bucket(country_iso: str, country_english: str, country_bulgarian: str) -> Appendix9CountryTotals:
        bucket = summary.appendix_9_by_country.get(country_iso)
        if bucket is None:
            bucket = Appendix9CountryTotals(
                country_iso=country_iso,
                country_english=country_english,
                country_bulgarian=country_bulgarian,
            )
            summary.appendix_9_by_country[country_iso] = bucket
        return bucket

    def _appendix8_part1_bucket(
        *,
        country_iso: str,
        country_english: str,
        country_bulgarian: str,
    ) -> Appendix8Part1Row:
        bucket = appendix8_part1_by_country.get(country_iso)
        if bucket is None:
            bucket = Appendix8Part1Row(
                country_iso=country_iso,
                country_english=country_english,
                country_bulgarian=country_bulgarian,
                acquisition_date=date(tax_year, 12, 31),
            )
            appendix8_part1_by_country[country_iso] = bucket
        return bucket

    appendix9_components: dict[str, dict[str, _CountryCreditComponent]] = {}
    appendix8_part1_by_country: dict[str, Appendix8Part1Row] = {}

    consumed_closedlots: set[int] = set()
    current_trades_header: _ActiveHeader | None = None
    seen_trades_header = False
    found_trade_section_data = False

    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1

        if len(row) < 2 or row[0] != "Trades":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_trades_header = _activate_header("Trades", row, row_number=row_number)
            seen_trades_header = True
            trades_row_base_len[row_idx] = 2 + len(current_trades_header.headers)
            continue

        if current_trades_header is None:
            raise CsvStructureError(f"row {row_number}: Trades row encountered before Trades Header")

        trades_row_base_len[row_idx] = 2 + len(current_trades_header.headers)
        if row_type != "Data":
            continue

        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            raise CsvStructureError(f"row {row_number}: Trades Data row encountered before Trades Header")
        current_trades_header = active_trades_header
        trades_row_base_len[row_idx] = 2 + len(active_trades_header.headers)
        field_idx = _trade_indexes(active_trades_header)

        found_trade_section_data = True

        padded = row + [""] * (trades_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]
        summary.trades_data_rows_total += 1

        discriminator = data[field_idx.discriminator].strip()
        lowered = discriminator.lower()
        if lowered == "trade":
            summary.trade_discriminator_rows += 1
        elif lowered == "closedlot":
            summary.closedlot_discriminator_rows += 1
        elif lowered == "order":
            summary.order_discriminator_rows += 1

        if row_idx in consumed_closedlots:
            continue

        if lowered == "closedlot":
            raise IbkrAnalyzerError(
                f"row {row_number}: orphan ClosedLot row detected (must immediately follow a Trade row)"
            )
        if lowered != "trade":
            summary.ignored_non_closing_trade_rows += 1
            continue

        asset_category = data[field_idx.asset].strip()
        symbol_raw = data[field_idx.symbol].strip()
        symbol = symbol_raw.upper()
        currency = data[field_idx.currency].strip().upper()
        code = data[field_idx.code].strip()
        is_closing_trade = _code_has_closing_token(code)
        proceeds = _parse_decimal(data[field_idx.proceeds], row_number=row_number, field_name="Proceeds")
        commission = (
            _parse_decimal_or_zero(data[field_idx.commission], row_number=row_number, field_name="Comm/Fee")
            if field_idx.commission is not None
            else ZERO
        )
        trade_basis: Decimal | None = None
        if field_idx.basis is not None:
            trade_basis_raw = data[field_idx.basis].strip()
            if trade_basis_raw != "":
                trade_basis = _parse_decimal(trade_basis_raw, row_number=row_number, field_name="Basis")
        trade_dt = _parse_trade_datetime(data[field_idx.date_time], row_number=row_number)
        trade_date = trade_dt.date()
        realized_idx = _optional_index(
            active_trades_header.headers,
            "Realized P/L",
            "Realized P&L",
            "Realized Profit and Loss",
            "RealizedProfitLoss",
        )
        realized_pl: Decimal | None = None
        if realized_idx is not None:
            realized_raw = data[realized_idx].strip()
            if realized_raw != "":
                realized_pl = _parse_decimal(realized_raw, row_number=row_number, field_name="Realized P/L")

        execution_exchange_raw = data[field_idx.exchange].strip() if field_idx.exchange is not None else ""
        execution_exchange_norm = _normalize_exchange(execution_exchange_raw)
        execution_exchange_class = _classify_exchange(execution_exchange_raw)

        summary.exchanges_used.add(execution_exchange_norm or "<EMPTY>")
        proceeds_eur, trade_fx_rate = _to_eur(
            proceeds,
            currency,
            trade_date,
            fx_provider,
            row_number=row_number,
        )
        commission_eur, _ = _to_eur(
            commission,
            currency,
            trade_date,
            fx_provider,
            row_number=row_number,
        )
        trade_basis_eur_from_trade: Decimal | None = None
        if trade_basis is not None:
            trade_basis_eur_from_trade, _ = _to_eur(
                trade_basis,
                currency,
                trade_date,
                fx_provider,
                row_number=row_number,
            )
        realized_pl_eur: Decimal | None = None
        if realized_pl is not None:
            realized_pl_eur, _ = _to_eur(
                realized_pl,
                currency,
                trade_date,
                fx_provider,
                row_number=row_number,
            )

        closedlot_indices: list[int] = []
        scan_idx = row_idx + 1
        while scan_idx < len(rows):
            scan_row = rows[scan_idx]
            if len(scan_row) < 2 or scan_row[0] != "Trades" or scan_row[1] != "Data":
                break
            scan_header = active_headers.get(scan_idx)
            if scan_header is None:
                raise CsvStructureError(f"row {scan_idx + 1}: Trades Data row encountered before Trades Header")
            trades_row_base_len[scan_idx] = 2 + len(scan_header.headers)
            scan_idxes = _trade_indexes(scan_header)
            padded_scan = scan_row + [""] * (trades_row_base_len[scan_idx] - len(scan_row))
            scan_data = padded_scan[2 : 2 + len(scan_header.headers)]
            scan_discriminator = scan_data[scan_idxes.discriminator].strip()
            if scan_discriminator.lower() != "closedlot":
                break
            closedlot_indices.append(scan_idx)
            scan_idx += 1

        if _is_forex_asset(asset_category):
            summary.forex_ignored_rows += 1
            summary.forex_ignored_abs_proceeds_eur += abs(proceeds_eur)
            for closed_idx in closedlot_indices:
                consumed_closedlots.add(closed_idx)

            forex_values: dict[str, str] = {
                "Fx Rate": _fmt(trade_fx_rate, quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(commission_eur, quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(proceeds_eur, quant=DECIMAL_EIGHT),
                "Tax Exempt Mode": tax_exempt_mode,
                "Appendix Target": APPENDIX_IGNORED,
                "Tax Treatment Reason": "Forex ignored (not included in Appendix 5/13)",
                "Review Required": "NO",
            }
            if trade_basis_eur_from_trade is not None:
                forex_values["Basis (EUR)"] = _fmt(trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
            if realized_pl_eur is not None:
                forex_values["Realized P/L (EUR)"] = _fmt(realized_pl_eur, quant=DECIMAL_EIGHT)
            _set_trade_extras(
                row_idx,
                forex_values,
            )
            continue

        if not _is_supported_asset(asset_category):
            raise IbkrAnalyzerError(
                f"Unsupported Asset Category encountered: {asset_category}. Review required before using analyzer."
            )

        if not is_closing_trade:
            summary.ignored_non_closing_trade_rows += 1
            non_closing_values: dict[str, str] = {
                "Fx Rate": _fmt(trade_fx_rate, quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(commission_eur, quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(proceeds_eur, quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(ZERO, quant=DECIMAL_EIGHT),
                "Tax Exempt Mode": tax_exempt_mode,
                "Tax Treatment Reason": "Non-closing Trade row (informational only)",
                "Review Required": "NO",
            }
            if trade_basis_eur_from_trade is not None:
                non_closing_values["Basis (EUR)"] = _fmt(trade_basis_eur_from_trade, quant=DECIMAL_EIGHT)
            _set_trade_extras(row_idx, non_closing_values)
            continue

        summary.closing_trade_candidates += 1

        # ClosedLot rows must be an immediate sequence following Trade row.
        if not closedlot_indices:
            raise IbkrAnalyzerError(f"row {row_number}: no ClosedLot rows attached to closing Trade")

        closedlot_basis_eur_sum = ZERO
        closedlot_basis_original_sum = ZERO
        for closed_idx in closedlot_indices:
            closed_row_number = closed_idx + 1
            closed_row = rows[closed_idx]
            closed_header = active_headers.get(closed_idx)
            if closed_header is None:
                raise CsvStructureError(f"row {closed_row_number}: Trades Data row encountered before Trades Header")
            closed_idxes = _trade_indexes(closed_header)
            trades_row_base_len[closed_idx] = 2 + len(closed_header.headers)
            padded_closed = closed_row + [""] * (trades_row_base_len[closed_idx] - len(closed_row))
            closed_data = padded_closed[2 : 2 + len(closed_header.headers)]
            if closed_idxes.basis is None:
                raise CsvStructureError(
                    f"Trades header at row {closed_header.row_number}: missing required column; "
                    "expected one of ('Basis', 'Cost Basis', 'CostBasis')"
                )
            closed_basis_raw = closed_data[closed_idxes.basis]
            closed_basis = _parse_decimal(closed_basis_raw, row_number=closed_row_number, field_name="Basis")
            closed_dt = _parse_closedlot_date(closed_data[closed_idxes.date_time], row_number=closed_row_number)
            closed_currency = closed_data[closed_idxes.currency].strip().upper() or currency
            closed_basis_eur, closed_fx_rate = _to_eur(
                closed_basis,
                closed_currency,
                closed_dt,
                fx_provider,
                row_number=closed_row_number,
            )
            closedlot_basis_eur_sum += closed_basis_eur
            closedlot_basis_original_sum += closed_basis
            consumed_closedlots.add(closed_idx)
            _set_trade_extras(
                closed_idx,
                {
                    "Fx Rate": _fmt(closed_fx_rate, quant=DECIMAL_EIGHT),
                    "Basis (EUR)": _fmt(closed_basis_eur, quant=DECIMAL_EIGHT),
                },
            )

        trade_basis_eur = -closedlot_basis_eur_sum

        cash_leg_eur = proceeds_eur + commission_eur
        if cash_leg_eur >= ZERO:
            sale_price_component_eur = abs(cash_leg_eur)
            purchase_component_eur = abs(trade_basis_eur)
        else:
            sale_price_component_eur = abs(trade_basis_eur)
            purchase_component_eur = abs(cash_leg_eur)
        pnl_eur = proceeds_eur + trade_basis_eur + commission_eur

        pnl_win = pnl_eur if pnl_eur > 0 else ZERO
        pnl_loss = -pnl_eur if pnl_eur < 0 else ZERO

        instrument, normalized_symbol, forced_review_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        missing_symbol_mapping = instrument is None
        listing_exchange = instrument.listing_exchange_normalized if instrument is not None else ""
        symbol_is_eu_listed: bool | None = None if instrument is None else instrument.is_eu_listed

        appendix_target, reason, review_required = _resolve_tax_target(
            tax_exempt_mode=tax_exempt_mode,
            symbol_is_eu_listed=symbol_is_eu_listed,
            execution_exchange_class=execution_exchange_class,
            missing_symbol_mapping=missing_symbol_mapping,
            forced_review_reason=forced_review_reason,
        )

        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)
        review_notes_parts: list[str] = []
        if review_status_normalized == REVIEW_STATUS_TAXABLE:
            appendix_target = APPENDIX_5
            reason = "Review Status override: TAXABLE"
            review_required = False
            summary.review_status_overrides_rows += 1
            review_notes_parts.append("Review Status override applied")
        elif review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
            appendix_target = APPENDIX_13
            reason = "Review Status override: NON-TAXABLE"
            review_required = False
            summary.review_status_overrides_rows += 1
            review_notes_parts.append("Review Status override applied")
        elif review_status_normalized != "":
            reason = f"{reason}; unknown Review Status={review_status_normalized}"
            review_required = True
            summary.unknown_review_status_rows += 1
            summary.unknown_review_status_values.add(review_status_normalized)
            review_notes_parts.append("Unknown Review Status value")

        review_notes = ""
        if review_required:
            summary.review_required_rows += 1
            if not review_notes_parts:
                review_notes_parts.append("Review required by tax mode rules")
            review_notes = "; ".join(review_notes_parts)
            summary.warnings.append(
                f"row {row_number}: {reason} (symbol={symbol}, execution_exchange={execution_exchange_norm or '<EMPTY>'})"
            )
            logger.warning(
                "row %s marked REVIEW_REQUIRED: %s (symbol=%s, execution_exchange=%s)",
                row_number,
                reason,
                symbol,
                execution_exchange_norm or "<EMPTY>",
            )
        elif review_notes_parts:
            review_notes = "; ".join(review_notes_parts)

        if tax_exempt_mode == TAX_MODE_LISTED_SYMBOL and symbol_is_eu_listed:
            if execution_exchange_class in {EXCHANGE_CLASS_EU_NON_REGULATED, EXCHANGE_CLASS_UNKNOWN}:
                warning = (
                    f"row {row_number}: execution exchange {execution_exchange_norm or '<EMPTY>'} "
                    "is informational only in listed_symbol mode"
                )
                summary.warnings.append(warning)
                logger.warning("%s", warning)

        in_tax_year = trade_date.year == tax_year
        if in_tax_year:
            summary.processed_trades_in_tax_year += 1
            if tax_exempt_mode == TAX_MODE_EXECUTION_EXCHANGE and appendix_target == APPENDIX_REVIEW:
                summary.review_rows += 1
                summary.review_exchanges.add(execution_exchange_norm or "<EMPTY>")
                _sum_bucket(summary.review, sale_price_component_eur, purchase_component_eur, pnl_eur)
                summary.review_entries.append(
                    ReviewEntry(
                        row_number=row_number,
                        symbol=symbol,
                        trade_date=trade_date.isoformat(),
                        listing_exchange=listing_exchange or "<MISSING>",
                        execution_exchange=execution_exchange_norm or "<EMPTY>",
                        reason=reason,
                        proceeds_eur=proceeds_eur,
                        basis_eur=trade_basis_eur,
                        pnl_eur=pnl_eur,
                    )
                )
            elif appendix_target == APPENDIX_13:
                _sum_bucket(summary.appendix_13, sale_price_component_eur, purchase_component_eur, pnl_eur)
            else:
                _sum_bucket(summary.appendix_5, sale_price_component_eur, purchase_component_eur, pnl_eur)
        else:
            summary.trades_outside_tax_year += 1

        _set_trade_extras(
            row_idx,
            {
                "Fx Rate": _fmt(trade_fx_rate, quant=DECIMAL_EIGHT),
                "Comm/Fee (EUR)": _fmt(commission_eur, quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(proceeds_eur, quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(trade_basis_eur, quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(sale_price_component_eur, quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(purchase_component_eur, quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(pnl_eur, quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(pnl_win, quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(pnl_loss, quant=DECIMAL_EIGHT),
                "Normalized Symbol": normalized_symbol,
                "Listing Exchange": listing_exchange,
                "Symbol Listed On EU Regulated Market": (
                    "YES" if symbol_is_eu_listed else "NO" if symbol_is_eu_listed is not None else ""
                ),
                "Execution Exchange Classification": execution_exchange_class,
                "Tax Exempt Mode": tax_exempt_mode,
                "Appendix Target": appendix_target,
                "Tax Treatment Reason": reason,
                "Review Required": "YES" if review_required else "NO",
                "Review Notes": review_notes,
            },
        )

    if not seen_trades_header:
        raise CsvStructureError("missing section header: Trades")
    if not found_trade_section_data:
        raise CsvStructureError("Trades section has no Data rows")

    current_interest_header: _ActiveHeader | None = None
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Interest":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_interest_header = _activate_header("Interest", row, row_number=row_number)
            interest_row_base_len[row_idx] = 2 + len(current_interest_header.headers)
            continue

        if current_interest_header is None:
            raise CsvStructureError(f"row {row_number}: Interest row encountered before Interest Header")
        interest_row_base_len[row_idx] = 2 + len(current_interest_header.headers)
        if row_type != "Data":
            continue

        active_interest_header = active_headers.get(row_idx)
        if active_interest_header is None:
            raise CsvStructureError(f"row {row_number}: Interest Data row encountered before Interest Header")
        current_interest_header = active_interest_header
        interest_row_base_len[row_idx] = 2 + len(active_interest_header.headers)

        field_idx = _interest_indexes(active_interest_header)
        padded = row + [""] * (interest_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_interest_header.headers)]

        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            summary.interest_total_rows_skipped += 1
            continue

        summary.interest_processed_rows += 1
        interest_date = _parse_interest_date(data[field_idx.date], row_number=row_number)
        description = data[field_idx.description].strip()
        amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        normalized_type = _normalize_interest_type(description, currency=currency)
        status = _classify_interest_type(normalized_type)
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)
        if review_status_normalized == REVIEW_STATUS_TAXABLE:
            if status != INTEREST_STATUS_TAXABLE:
                summary.review_status_overrides_rows += 1
            status = INTEREST_STATUS_TAXABLE
        elif review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
            if status != INTEREST_STATUS_NON_TAXABLE:
                summary.review_status_overrides_rows += 1
            status = INTEREST_STATUS_NON_TAXABLE
        elif review_status_normalized in {"UNKNOWN", "REVIEW-REQUIRED"}:
            status = INTEREST_STATUS_UNKNOWN
        elif review_status_normalized != "":
            status = INTEREST_STATUS_UNKNOWN
            summary.unknown_review_status_rows += 1
            summary.unknown_review_status_values.add(review_status_normalized)
            summary.warnings.append(
                f"row {row_number}: unknown Review Status={review_status_normalized} (interest description={description!r})"
            )

        amount_eur_text = ""
        if status == INTEREST_STATUS_TAXABLE:
            summary.interest_taxable_rows += 1
            if interest_date.year == tax_year:
                amount_eur, _ = _to_eur(
                    amount,
                    currency,
                    interest_date,
                    fx_provider,
                    row_number=row_number,
                )
                amount_eur_text = _fmt(amount_eur, quant=DECIMAL_EIGHT)
                if normalized_type == INTEREST_TYPE_CREDIT:
                    summary.appendix_9_credit_interest_eur += amount_eur
                    summary.appendix_6_credit_interest_eur += amount_eur
                    country_iso, country_english, country_bulgarian = _appendix9_default_country()
                    appendix9_bucket = _appendix9_bucket(country_iso, country_english, country_bulgarian)
                    appendix9_bucket.gross_interest_eur += amount_eur
                    period_key = _extract_period_key_from_description(
                        description,
                        fallback=f"INTEREST_ROW_{row_number}",
                    )
                    _country_component(
                        appendix9_components,
                        country_iso=country_iso,
                        component_key=period_key,
                    ).gross_eur += amount_eur
                elif normalized_type == INTEREST_TYPE_SYEP:
                    summary.appendix_6_syep_interest_eur += amount_eur
                else:
                    summary.appendix_6_other_taxable_eur += amount_eur
        elif status == INTEREST_STATUS_NON_TAXABLE:
            summary.interest_non_taxable_rows += 1
        else:
            summary.interest_unknown_rows += 1
            summary.review_required_rows += 1
            normalized_display = normalized_type or "<EMPTY>"
            if normalized_display not in INTEREST_DECLARED_TYPES | INTEREST_NON_DECLARED_TYPES:
                summary.interest_unknown_types.add(normalized_display)
                summary.interest_unknown_descriptions.append(description)
                summary.warnings.append(
                    f"row {row_number}: unknown interest type={normalized_display} (description={description!r})"
                )

        _set_interest_extras(
            row_idx,
            {
                "Amount (EUR)": amount_eur_text,
                "Status": status,
            },
        )

    current_dividends_header: _ActiveHeader | None = None
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Dividends":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_dividends_header = _activate_header("Dividends", row, row_number=row_number)
            dividends_row_base_len[row_idx] = 2 + len(current_dividends_header.headers)
            dividends_row_added_columns[row_idx] = [
                col for col in ADDED_DIVIDENDS_COLUMNS if col not in current_dividends_header.headers
            ]
            continue

        if current_dividends_header is None:
            raise CsvStructureError(f"row {row_number}: Dividends row encountered before Dividends Header")
        dividends_row_base_len[row_idx] = 2 + len(current_dividends_header.headers)
        if row_type != "Data":
            continue

        active_dividends_header = active_headers.get(row_idx)
        if active_dividends_header is None:
            raise CsvStructureError(f"row {row_number}: Dividends Data row encountered before Dividends Header")
        current_dividends_header = active_dividends_header
        dividends_row_base_len[row_idx] = 2 + len(active_dividends_header.headers)
        dividends_row_added_columns[row_idx] = [
            col for col in ADDED_DIVIDENDS_COLUMNS if col not in active_dividends_header.headers
        ]

        field_idx = _dividends_indexes(active_dividends_header)
        padded = row + [""] * (dividends_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_dividends_header.headers)]
        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            summary.dividends_total_rows_skipped += 1
            continue

        summary.dividends_processed_rows += 1
        dividend_date = _parse_interest_date(data[field_idx.date], row_number=row_number)
        description = data[field_idx.description].strip()
        amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        auto_appendix = _classify_dividend_description(description)
        auto_status = _classify_status_from_description(description)
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)
        manual_country = data[field_idx.country].strip() if field_idx.country is not None else ""
        manual_amount_eur_text = data[field_idx.amount_eur].strip() if field_idx.amount_eur is not None else ""
        manual_amount_eur = _parse_optional_decimal(
            manual_amount_eur_text,
            row_number=row_number,
            field_name="Amount (EUR)",
        )
        manual_isin = data[field_idx.isin].strip() if field_idx.isin is not None else ""
        manual_appendix = data[field_idx.appendix].strip() if field_idx.appendix is not None else ""

        effective_status = auto_status
        if review_status_normalized == REVIEW_STATUS_TAXABLE:
            summary.review_status_overrides_rows += 1
            effective_status = INTEREST_STATUS_TAXABLE
        elif review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
            summary.review_status_overrides_rows += 1
            effective_status = INTEREST_STATUS_NON_TAXABLE
        elif review_status_normalized == "":
            pass
        elif review_status_normalized != "":
            effective_status = INTEREST_STATUS_UNKNOWN
            summary.unknown_review_status_rows += 1
            summary.unknown_review_status_values.add(review_status_normalized)
            summary.warnings.append(
                f"row {row_number}: unknown Review Status={review_status_normalized} (dividend description={description!r})"
            )
            summary.review_required_rows += 1

        auto_isin = ""
        auto_country_english = ""
        auto_amount_eur: Decimal | None = None
        auto_amount_eur_text = ""

        if auto_appendix != DIVIDEND_APPENDIX_UNKNOWN:
            auto_isin_value, auto_isin_error = _extract_isin(description)
            if auto_isin_error is None and auto_isin_value is not None:
                auto_isin = auto_isin_value
                auto_country_info = _resolve_country_from_isin(auto_isin_value)
                if auto_country_info is not None:
                    auto_country_iso, auto_country_english, auto_country_bulgarian = auto_country_info
                    auto_amount_eur, _ = _to_eur(
                        amount,
                        currency,
                        dividend_date,
                        fx_provider,
                        row_number=row_number,
                    )
                    auto_amount_eur_text = _fmt(auto_amount_eur, quant=DECIMAL_EIGHT)
                else:
                    summary.dividends_country_errors_rows += 1
                    summary.review_required_rows += 1
                    summary.warnings.append(
                        f"row {row_number}: unknown ISIN country code={auto_isin_value[:2]} for dividend description={description!r}"
                    )
            else:
                summary.dividends_country_errors_rows += 1
                summary.review_required_rows += 1
                summary.warnings.append(
                    f"row {row_number}: {auto_isin_error or 'missing ISIN'} for dividend description={description!r}"
                )

        effective_appendix = manual_appendix if manual_appendix else auto_appendix
        if effective_appendix == DIVIDEND_APPENDIX_UNKNOWN and effective_status == INTEREST_STATUS_TAXABLE:
            effective_appendix = DIVIDEND_APPENDIX_8 if "lieu received" not in description.lower() else DIVIDEND_APPENDIX_6

        effective_country_text = manual_country if manual_country else auto_country_english
        effective_amount_eur = manual_amount_eur if manual_amount_eur is not None else auto_amount_eur
        effective_amount_eur_text = manual_amount_eur_text if manual_amount_eur_text else auto_amount_eur_text
        effective_isin = manual_isin if manual_isin else auto_isin

        if effective_status == INTEREST_STATUS_UNKNOWN:
            summary.dividends_unknown_rows += 1
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: unknown dividend description requires manual review (description={description!r})"
            )

        is_taxable = effective_status == INTEREST_STATUS_TAXABLE
        if is_taxable and dividend_date.year == tax_year and effective_amount_eur is not None:
            if effective_appendix == DIVIDEND_APPENDIX_8:
                if effective_country_text == "":
                    summary.review_required_rows += 1
                    summary.warnings.append(
                        f"row {row_number}: taxable dividend row is missing Country (description={description!r})"
                    )
                else:
                    country_iso, country_english, country_bulgarian = _resolve_country_from_text(effective_country_text)
                    company_name, company_error = _resolve_dividend_company_name(
                        description=description,
                        listings=listings,
                    )
                    if company_name is None:
                        company_name = f"UNKNOWN_PAYER_ROW_{row_number}"
                    if company_error is not None:
                        summary.review_required_rows += 1
                        summary.warnings.append(
                            f"row {row_number}: dividend company mapping requires review "
                            f"(description={description!r}, resolved_company={company_name!r}, reason={company_error})"
                        )
                    summary.dividends_cash_rows += 1
                    _appendix8_bucket(country_iso, country_english, country_bulgarian).gross_dividend_eur += effective_amount_eur
                    _appendix8_company_bucket(
                        country_iso=country_iso,
                        country_english=country_english,
                        country_bulgarian=country_bulgarian,
                        company_name=company_name,
                    ).gross_dividend_eur += effective_amount_eur
            elif effective_appendix == DIVIDEND_APPENDIX_6:
                summary.dividends_lieu_rows += 1
                summary.appendix_6_lieu_received_eur += effective_amount_eur

        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_dividends_header,
            field_idx=field_idx.country,
            value=effective_country_text,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_dividends_header,
            field_idx=field_idx.amount_eur,
            value=effective_amount_eur_text,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_dividends_header,
            field_idx=field_idx.isin,
            value=effective_isin,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_dividends_header,
            field_idx=field_idx.appendix,
            value=effective_appendix,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_dividends_header,
            field_idx=field_idx.status,
            value=effective_status,
            only_if_empty=False,
        )

        _set_dividends_extras(
            row_idx,
            {
                "Country": effective_country_text,
                "Amount (EUR)": effective_amount_eur_text,
                "ISIN": effective_isin,
                "Appendix": effective_appendix,
                "Status": effective_status,
                "Review Status": review_status_raw,
            },
        )

    current_withholding_header: _ActiveHeader | None = None
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Withholding Tax":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_withholding_header = _activate_header("Withholding Tax", row, row_number=row_number)
            withholding_row_base_len[row_idx] = 2 + len(current_withholding_header.headers)
            withholding_row_added_columns[row_idx] = [
                col for col in ADDED_WITHHOLDING_COLUMNS if col not in current_withholding_header.headers
            ]
            continue

        if current_withholding_header is None:
            raise CsvStructureError(f"row {row_number}: Withholding Tax row encountered before Withholding Tax Header")
        withholding_row_base_len[row_idx] = 2 + len(current_withholding_header.headers)
        if row_type != "Data":
            continue

        active_withholding_header = active_headers.get(row_idx)
        if active_withholding_header is None:
            raise CsvStructureError(f"row {row_number}: Withholding Tax Data row encountered before Withholding Tax Header")
        current_withholding_header = active_withholding_header
        withholding_row_base_len[row_idx] = 2 + len(active_withholding_header.headers)
        withholding_row_added_columns[row_idx] = [
            col for col in ADDED_WITHHOLDING_COLUMNS if col not in active_withholding_header.headers
        ]

        field_idx = _withholding_indexes(active_withholding_header)
        padded = row + [""] * (withholding_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_withholding_header.headers)]
        currency = data[field_idx.currency].strip().upper()
        if _is_interest_total_row(currency):
            summary.withholding_total_rows_skipped += 1
            continue

        summary.withholding_processed_rows += 1
        description = data[field_idx.description].strip()
        lowered = description.lower()
        auto_status = _classify_status_from_description(description)
        manual_country = data[field_idx.country].strip() if field_idx.country is not None else ""
        manual_amount_eur_text = data[field_idx.amount_eur].strip() if field_idx.amount_eur is not None else ""
        manual_amount_eur = _parse_optional_decimal(
            manual_amount_eur_text,
            row_number=row_number,
            field_name="Amount (EUR)",
        )
        manual_isin = data[field_idx.isin].strip() if field_idx.isin is not None else ""
        manual_appendix = data[field_idx.appendix].strip() if field_idx.appendix is not None else ""
        review_status_raw = data[field_idx.review_status].strip() if field_idx.review_status is not None else ""
        review_status_normalized = _normalize_review_status(review_status_raw)

        auto_appendix = ""
        auto_country_text = ""
        auto_isin = ""
        auto_amount_eur: Decimal | None = None
        auto_amount_eur_text = ""

        if "cash dividend" in lowered:
            summary.withholding_dividend_rows += 1
            auto_appendix = "Appendix 8"
        elif "credit interest" in lowered:
            summary.withholding_non_dividend_rows += 1
            auto_appendix = "Appendix 9"
        elif "lieu received" in lowered:
            summary.withholding_non_dividend_rows += 1
            auto_appendix = "Appendix 6"
        else:
            summary.withholding_non_dividend_rows += 1

        tax_date = _parse_interest_date(data[field_idx.date], row_number=row_number)
        tax_amount = _parse_decimal(data[field_idx.amount], row_number=row_number, field_name="Amount")
        if auto_appendix == "Appendix 8":
            isin, isin_error = _extract_isin(description)
            if isin_error is not None or isin is None:
                summary.withholding_country_errors_rows += 1
                summary.review_required_rows += 1
                summary.warnings.append(
                    f"row {row_number}: {isin_error or 'missing ISIN'} for withholding description={description!r}"
                )
            else:
                country_info = _resolve_country_from_isin(isin)
                if country_info is None:
                    summary.withholding_country_errors_rows += 1
                    summary.review_required_rows += 1
                    summary.warnings.append(
                        f"row {row_number}: unknown ISIN country code={isin[:2]} for withholding description={description!r}"
                    )
                else:
                    _, country_english, _ = country_info
                    auto_country_text = country_english
                    auto_isin = isin
        elif auto_appendix == "Appendix 9":
            auto_country_text = "Ireland"
            auto_isin = ""

        if auto_appendix in {"Appendix 8", "Appendix 9", "Appendix 6"}:
            tax_amount_eur, _ = _to_eur(
                tax_amount,
                currency,
                tax_date,
                fx_provider,
                row_number=row_number,
            )
            auto_amount_eur = tax_amount_eur
            auto_amount_eur_text = _fmt(tax_amount_eur, quant=DECIMAL_EIGHT)

        effective_status = auto_status
        if review_status_normalized == REVIEW_STATUS_TAXABLE:
            summary.review_status_overrides_rows += 1
            effective_status = INTEREST_STATUS_TAXABLE
        elif review_status_normalized == REVIEW_STATUS_NON_TAXABLE:
            summary.review_status_overrides_rows += 1
            effective_status = INTEREST_STATUS_NON_TAXABLE
        elif review_status_normalized == "":
            pass
        elif review_status_normalized != "":
            effective_status = INTEREST_STATUS_UNKNOWN
            summary.review_required_rows += 1
            summary.unknown_review_status_rows += 1
            summary.unknown_review_status_values.add(review_status_normalized)
            summary.warnings.append(
                f"row {row_number}: unknown Review Status={review_status_normalized} (withholding description={description!r})"
            )

        effective_appendix = manual_appendix if manual_appendix else auto_appendix
        effective_country_text = manual_country if manual_country else auto_country_text
        effective_amount_eur = manual_amount_eur if manual_amount_eur is not None else auto_amount_eur
        effective_amount_eur_text = manual_amount_eur_text if manual_amount_eur_text else auto_amount_eur_text
        effective_isin = manual_isin if manual_isin else auto_isin

        if effective_status == INTEREST_STATUS_UNKNOWN:
            summary.review_required_rows += 1
            summary.warnings.append(
                f"row {row_number}: UNKNOWN withholding status requires manual review (description={description!r})"
            )
        is_taxable = effective_status == INTEREST_STATUS_TAXABLE

        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_withholding_header,
            field_idx=field_idx.country,
            value=effective_country_text,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_withholding_header,
            field_idx=field_idx.amount_eur,
            value=effective_amount_eur_text,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_withholding_header,
            field_idx=field_idx.isin,
            value=effective_isin,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_withholding_header,
            field_idx=field_idx.appendix,
            value=effective_appendix,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_withholding_header,
            field_idx=field_idx.status,
            value=effective_status,
            only_if_empty=False,
        )

        _set_withholding_extras(
            row_idx,
            {
                "Country": effective_country_text,
                "Amount (EUR)": effective_amount_eur_text,
                "ISIN": effective_isin,
                "Appendix": effective_appendix,
                "Status": effective_status,
                "Review Status": review_status_raw,
            },
        )

        if tax_date.year == tax_year and is_taxable and effective_amount_eur is not None:
            if effective_appendix == "Appendix 8":
                if effective_country_text == "":
                    summary.review_required_rows += 1
                    summary.warnings.append(
                        f"row {row_number}: taxable withholding row is missing Country (description={description!r})"
                    )
                else:
                    country_iso, country_english, country_bulgarian = _resolve_country_from_text(effective_country_text)
                    company_name, company_error = _resolve_dividend_company_name(
                        description=description,
                        listings=listings,
                    )
                    if company_name is None:
                        company_name = f"UNKNOWN_PAYER_ROW_{row_number}"
                    if company_error is not None:
                        summary.review_required_rows += 1
                        summary.warnings.append(
                            f"row {row_number}: withholding company mapping requires review "
                            f"(description={description!r}, resolved_company={company_name!r}, reason={company_error})"
                        )
                    _appendix8_bucket(country_iso, country_english, country_bulgarian).withholding_tax_paid_eur += abs(effective_amount_eur)
                    _appendix8_company_bucket(
                        country_iso=country_iso,
                        country_english=country_english,
                        country_bulgarian=country_bulgarian,
                        company_name=company_name,
                    ).withholding_tax_paid_eur += abs(effective_amount_eur)
            elif effective_appendix == "Appendix 9":
                # Appendix 9 paid foreign tax source of truth is Mark-to-Market
                # ("Withholding on Interest Received"). Appendix 9 rows in this
                # section stay informational/enriched and do not drive totals.
                pass
            else:
                summary.review_required_rows += 1
                summary.warnings.append(
                    f"row {row_number}: taxable withholding row has unknown Appendix value={effective_appendix!r}"
                )

    current_open_positions_header: _ActiveHeader | None = None
    appendix8_part1_fx_date = date(tax_year, 12, 31)
    for row_idx, row in enumerate(rows):
        row_number = row_idx + 1
        if len(row) < 2 or row[0] != "Open Positions":
            continue

        row_type = row[1]
        if row_type == "Header":
            current_open_positions_header = _activate_header("Open Positions", row, row_number=row_number)
            open_positions_row_base_len[row_idx] = 2 + len(current_open_positions_header.headers)
            open_positions_row_added_columns[row_idx] = [
                col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in current_open_positions_header.headers
            ]
            continue

        if current_open_positions_header is None:
            raise CsvStructureError(f"row {row_number}: Open Positions row encountered before Open Positions Header")
        open_positions_row_base_len[row_idx] = 2 + len(current_open_positions_header.headers)
        if row_type != "Data":
            continue

        active_open_positions_header = active_headers.get(row_idx)
        if active_open_positions_header is None:
            raise CsvStructureError(f"row {row_number}: Open Positions Data row encountered before Open Positions Header")
        current_open_positions_header = active_open_positions_header
        open_positions_row_base_len[row_idx] = 2 + len(active_open_positions_header.headers)
        open_positions_row_added_columns[row_idx] = [
            col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in active_open_positions_header.headers
        ]

        field_idx = _open_positions_indexes(active_open_positions_header)
        padded = row + [""] * (open_positions_row_base_len[row_idx] - len(row))
        data = padded[2 : 2 + len(active_open_positions_header.headers)]
        discriminator = data[field_idx.discriminator].strip().lower()
        if discriminator != "summary":
            continue

        asset_category = data[field_idx.asset].strip()
        summary.open_positions_summary_rows += 1
        if not _is_supported_asset(asset_category):
            summary.review_required_rows += 1
            summary.warnings.append(
                f"{REVIEW_REASON_OPEN_POSITION_UNSUPPORTED_ASSET}: "
                f"row={row_number} asset={asset_category!r} symbol={data[field_idx.symbol].strip()!r}"
            )
            continue

        symbol_raw = data[field_idx.symbol].strip()
        quantity = _parse_reconciliation_quantity(data[field_idx.quantity])
        if quantity is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: invalid Open Positions summary quantity for symbol={symbol_raw!r}"
            )

        instrument, _normalized_symbol, forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if instrument is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: Open Positions symbol cannot be matched to Financial Instrument "
                f"for symbol={symbol_raw!r}"
                + (f"; reason={forced_reason}" if forced_reason else "")
            )

        country_english = ""
        country_resolved: tuple[str, str, str] | None = None
        if instrument.isin != "":
            country_resolved = _resolve_country_from_isin(instrument.isin)
        if country_resolved is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: Open Positions ISIN is missing/invalid or unmapped country "
                f"for symbol={symbol_raw!r}; cannot build Appendix 8 Part I row"
            )
        _country_iso, country_english, _country_bulgarian = country_resolved

        cost_basis_original = ZERO
        cost_basis_eur = ZERO
        cost_basis_eur_text = ""
        if field_idx.cost_basis is None:
            raise CsvStructureError(
                f"Open Positions header at row {active_open_positions_header.row_number}: "
                "missing required column Cost Basis"
            )
        parsed_basis = _parse_decimal_loose_or_zero(data[field_idx.cost_basis])
        if parsed_basis is None:
            raise IbkrAnalyzerError(
                f"row {row_number}: invalid Open Positions Cost Basis value for symbol={symbol_raw!r}"
            )
        cost_basis_original = parsed_basis
        if field_idx.currency is None:
            raise CsvStructureError(
                f"Open Positions header at row {active_open_positions_header.row_number}: "
                "missing required column Currency"
            )
        currency = data[field_idx.currency].strip().upper()
        if currency == "":
            raise IbkrAnalyzerError(
                f"row {row_number}: empty Open Positions currency for symbol={symbol_raw!r}; "
                "cannot convert Cost Basis to EUR"
            )
        cost_basis_eur, _ = _to_eur(
            cost_basis_original,
            currency,
            appendix8_part1_fx_date,
            fx_provider,
            row_number=row_number,
        )
        cost_basis_eur_text = _fmt(cost_basis_eur, quant=DECIMAL_EIGHT)

        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_open_positions_header,
            field_idx=field_idx.country,
            value=country_english,
            only_if_empty=True,
        )
        _set_existing_section_value(
            row_idx=row_idx,
            active_header=active_open_positions_header,
            field_idx=field_idx.cost_basis_eur,
            value=cost_basis_eur_text,
            only_if_empty=True,
        )
        _set_open_positions_extras(
            row_idx,
            {
                "Country": country_english,
                "Cost Basis (EUR)": cost_basis_eur_text,
            },
        )

        country_iso, _, country_bulgarian = country_resolved
        bucket = _appendix8_part1_bucket(
            country_iso=country_iso,
            country_english=country_english,
            country_bulgarian=country_bulgarian,
        )
        bucket.quantity += quantity
        bucket.cost_basis_original += cost_basis_original
        bucket.cost_basis_eur += cost_basis_eur

    withholding_found = False
    withholding_paid_eur, withholding_found = _extract_interest_withholding_paid_eur(
        rows,
        active_headers=active_headers,
    )
    if withholding_paid_eur > ZERO:
        country_iso, country_english, country_bulgarian = _appendix9_default_country()
        appendix9_bucket = _appendix9_bucket(country_iso, country_english, country_bulgarian)
        appendix9_bucket.withholding_tax_paid_eur += withholding_paid_eur
        _country_component(
            appendix9_components,
            country_iso=country_iso,
            component_key="MTM_SOURCE",
        ).foreign_tax_paid_eur += withholding_paid_eur
    summary.appendix_9_withholding_paid_eur = withholding_paid_eur
    summary.appendix_9_withholding_source_found = withholding_found

    summary.appendix_9_credit_interest_eur = sum(
        (bucket.gross_interest_eur for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )
    summary.appendix_9_withholding_paid_eur = sum(
        (bucket.withholding_tax_paid_eur for bucket in summary.appendix_9_by_country.values()),
        ZERO,
    )

    summary.appendix_8_part1_rows = _build_appendix8_part1_rows(
        totals_by_country=appendix8_part1_by_country,
    )
    summary.open_positions_part1_rows = len(summary.appendix_8_part1_rows)

    summary.appendix_8_company_results = _compute_appendix8_company_results(
        totals_by_company=summary.appendix_8_by_company,
        dividend_tax_rate=summary.dividend_tax_rate,
    )
    if summary.appendix8_dividend_list_mode == APPENDIX8_LIST_MODE_COUNTRY:
        summary.appendix_8_output_rows = _aggregate_appendix8_company_rows_by_country_and_method(
            company_rows=summary.appendix_8_company_results,
        )
    else:
        summary.appendix_8_output_rows = list(summary.appendix_8_company_results)
    summary.appendix_8_country_debug = _build_appendix8_country_debug(
        company_rows=summary.appendix_8_company_results,
        dividend_tax_rate=summary.dividend_tax_rate,
    )
    summary.appendix_9_country_results = _compute_appendix9_country_results(
        totals_by_country=summary.appendix_9_by_country,
        components_by_country=appendix9_components,
    )

    tax_credit_debug_report_path = _write_tax_credit_debug_report(
        output_dir=out_dir,
        normalized_alias=normalized_alias,
        tax_year=tax_year,
        appendix8_company_rows=summary.appendix_8_company_results,
        appendix8_country_debug=summary.appendix_8_country_debug,
        appendix8_output_rows=summary.appendix_8_output_rows,
        appendix8_list_mode=summary.appendix8_dividend_list_mode,
        appendix9_results=summary.appendix_9_country_results,
    )
    summary.tax_credit_debug_report_path = str(tax_credit_debug_report_path)

    summary.appendix_6_code_603_eur = (
        summary.appendix_6_credit_interest_eur
        + summary.appendix_6_syep_interest_eur
        + summary.appendix_6_other_taxable_eur
        + summary.appendix_6_lieu_received_eur
    )
    if summary.appendix_9_credit_interest_eur > ZERO and not withholding_found:
        summary.review_required_rows += 1
        summary.warnings.append(
            "Mark-to-Market Performance Summary row for 'Withholding on Interest Received' was not found; using 0"
        )

    # Populate EUR columns for Trades SubTotal/Total rows in the production CSV
    # using the same methodology as sanity aggregation over Trade rows.
    aggregate_col_idx = {
        "comm": ADDED_TRADES_COLUMNS.index("Comm/Fee (EUR)"),
        "proceeds": ADDED_TRADES_COLUMNS.index("Proceeds (EUR)"),
        "basis": ADDED_TRADES_COLUMNS.index("Basis (EUR)"),
        "sale_price": ADDED_TRADES_COLUMNS.index("Sale Price (EUR)"),
        "purchase_price": ADDED_TRADES_COLUMNS.index("Purchase Price (EUR)"),
        "realized": ADDED_TRADES_COLUMNS.index("Realized P/L (EUR)"),
    }
    symbol_agg_eur: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    asset_agg_eur: dict[tuple[str, str], dict[str, Decimal]] = {}

    def _ensure_agg_bucket(
        bucket: dict[tuple[str, str] | tuple[str, str, str], dict[str, Decimal]],
        key: tuple[str, str] | tuple[str, str, str],
    ) -> dict[str, Decimal]:
        item = bucket.get(key)
        if item is None:
            item = {
                "proceeds": ZERO,
                "basis": ZERO,
                "comm_fee": ZERO,
                "sale_price": ZERO,
                "purchase_price": ZERO,
                "realized_pl": ZERO,
                "wins": ZERO,
                "losses": ZERO,
            }
            bucket[key] = item
        return item

    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Trades" or row[1] != "Data":
            continue
        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            continue
        field_idx = _trade_indexes(active_trades_header)
        base_len = 2 + len(active_trades_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]
        if data[field_idx.discriminator].strip().lower() != "trade":
            continue

        asset_category = data[field_idx.asset].strip()
        if _is_forex_asset(asset_category) or not _is_supported_asset(asset_category):
            continue

        extras = trades_row_extras.get(row_idx)
        if extras is None:
            continue
        proceeds_eur = _try_parse_decimal(extras[aggregate_col_idx["proceeds"]]) or ZERO
        basis_eur = _try_parse_decimal(extras[aggregate_col_idx["basis"]]) or ZERO
        comm_fee_eur = _try_parse_decimal(extras[aggregate_col_idx["comm"]]) or ZERO
        sale_price_eur = _try_parse_decimal(extras[aggregate_col_idx["sale_price"]]) or ZERO
        purchase_price_eur = _try_parse_decimal(extras[aggregate_col_idx["purchase_price"]]) or ZERO
        realized_eur = _try_parse_decimal(extras[aggregate_col_idx["realized"]]) or ZERO
        wins_eur = realized_eur if realized_eur > 0 else ZERO
        losses_eur = -realized_eur if realized_eur < 0 else ZERO

        symbol_raw = data[field_idx.symbol].strip()
        symbol_upper = symbol_raw.upper()
        currency = data[field_idx.currency].strip().upper()
        instrument, normalized_symbol, _forced_reason = _resolve_instrument_for_trade_symbol(
            asset_category=asset_category,
            trade_symbol=symbol_raw,
            listings=listings,
        )
        if normalized_symbol:
            grouping_symbol = normalized_symbol
        elif instrument is not None:
            grouping_symbol = instrument.symbol
        else:
            grouping_symbol = symbol_upper

        symbol_bucket = _ensure_agg_bucket(symbol_agg_eur, (asset_category, currency, grouping_symbol))
        symbol_bucket["proceeds"] += proceeds_eur
        symbol_bucket["basis"] += basis_eur
        symbol_bucket["comm_fee"] += comm_fee_eur
        symbol_bucket["sale_price"] += sale_price_eur
        symbol_bucket["purchase_price"] += purchase_price_eur
        symbol_bucket["realized_pl"] += realized_eur
        symbol_bucket["wins"] += wins_eur
        symbol_bucket["losses"] += losses_eur

        asset_bucket = _ensure_agg_bucket(asset_agg_eur, (asset_category, currency))
        asset_bucket["proceeds"] += proceeds_eur
        asset_bucket["basis"] += basis_eur
        asset_bucket["comm_fee"] += comm_fee_eur
        asset_bucket["sale_price"] += sale_price_eur
        asset_bucket["purchase_price"] += purchase_price_eur
        asset_bucket["realized_pl"] += realized_eur
        asset_bucket["wins"] += wins_eur
        asset_bucket["losses"] += losses_eur

    subtotal_rows_for_output: list[dict[str, object]] = []
    total_rows_for_output: list[dict[str, object]] = []
    for row_idx, row in enumerate(rows):
        if len(row) < 2 or row[0] != "Trades" or row[1] not in {"SubTotal", "Total"}:
            continue
        active_trades_header = active_headers.get(row_idx)
        if active_trades_header is None:
            continue
        field_idx = _trade_indexes(active_trades_header)
        base_len = 2 + len(active_trades_header.headers)
        padded = row + [""] * (base_len - len(row))
        data = padded[2 : 2 + len(active_trades_header.headers)]

        asset_category = data[field_idx.asset].strip()
        if _is_forex_asset(asset_category) or not _is_supported_asset(asset_category):
            continue
        currency = data[field_idx.currency].strip().upper()
        symbol_raw = data[field_idx.symbol].strip()
        symbol_upper = symbol_raw.upper()
        subtotal_symbol = symbol_upper
        if row[1] == "SubTotal":
            sub_instrument, sub_normalized_symbol, _sub_forced_reason = _resolve_instrument_for_trade_symbol(
                asset_category=asset_category,
                trade_symbol=symbol_raw,
                listings=listings,
            )
            if sub_normalized_symbol:
                subtotal_symbol = sub_normalized_symbol
            elif sub_instrument is not None:
                subtotal_symbol = sub_instrument.symbol

        container = subtotal_rows_for_output if row[1] == "SubTotal" else total_rows_for_output
        container.append(
            {
                "row_idx": row_idx,
                "asset_category": asset_category,
                "currency": currency,
                "symbol": subtotal_symbol,
            }
        )

    def _row_distance_to_expected_for_output(entry: dict[str, object], expected: dict[str, Decimal]) -> Decimal:
        currency = str(entry["currency"])
        symbol = str(entry.get("symbol", ""))
        asset = str(entry["asset_category"])
        if symbol:
            aggregate = symbol_agg_eur.get((asset, currency, symbol))
        else:
            aggregate = asset_agg_eur.get((asset, currency))
        if aggregate is None:
            return Decimal("999999999")
        return (
            abs(expected["proceeds"] - aggregate["proceeds"])
            + abs(expected["basis"] - aggregate["basis"])
            + abs(expected["comm_fee"] - aggregate["comm_fee"])
            + abs(expected["realized_pl"] - aggregate["realized_pl"])
        )

    selected_subtotals_for_output: list[dict[str, object]] = []
    grouped_subtotals_for_output: dict[tuple[str, str], list[dict[str, object]]] = {}
    for entry in subtotal_rows_for_output:
        key = (str(entry["asset_category"]), str(entry["symbol"]))
        grouped_subtotals_for_output.setdefault(key, []).append(entry)

    for (asset_category, symbol), entries in grouped_subtotals_for_output.items():
        non_eur = [item for item in entries if str(item["currency"]).upper() != "EUR"]
        eur = [item for item in entries if str(item["currency"]).upper() == "EUR"]
        selected_subtotals_for_output.extend(non_eur)
        expected_eur = symbol_agg_eur.get((asset_category, "EUR", symbol))
        if expected_eur is not None and eur:
            best_eur = min(
                eur,
                key=lambda item: _row_distance_to_expected_for_output(item, expected_eur),
            )
            selected_subtotals_for_output.append(best_eur)

    selected_totals_for_output: list[dict[str, object]] = []
    grouped_totals_for_output: dict[str, list[dict[str, object]]] = {}
    for entry in total_rows_for_output:
        grouped_totals_for_output.setdefault(str(entry["asset_category"]), []).append(entry)

    for asset_category, entries in grouped_totals_for_output.items():
        non_eur = [item for item in entries if str(item["currency"]).upper() != "EUR"]
        eur = [item for item in entries if str(item["currency"]).upper() == "EUR"]
        selected_totals_for_output.extend(non_eur)
        expected_eur = asset_agg_eur.get((asset_category, "EUR"))
        if expected_eur is not None and eur:
            best_eur = min(
                eur,
                key=lambda item: _row_distance_to_expected_for_output(item, expected_eur),
            )
            selected_totals_for_output.append(best_eur)

    for entry in selected_subtotals_for_output:
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        symbol = str(entry["symbol"])
        agg = symbol_agg_eur.get((asset_category, currency, symbol))
        if agg is None:
            continue
        _set_trade_extras(
            int(entry["row_idx"]),
            {
                "Comm/Fee (EUR)": _fmt(agg["comm_fee"], quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(agg["proceeds"], quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(agg["basis"], quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(agg["sale_price"], quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(agg["purchase_price"], quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(agg["realized_pl"], quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(agg["wins"], quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(agg["losses"], quant=DECIMAL_EIGHT),
            },
        )

    for entry in selected_totals_for_output:
        asset_category = str(entry["asset_category"])
        currency = str(entry["currency"])
        agg = asset_agg_eur.get((asset_category, currency))
        if agg is None:
            continue
        _set_trade_extras(
            int(entry["row_idx"]),
            {
                "Comm/Fee (EUR)": _fmt(agg["comm_fee"], quant=DECIMAL_EIGHT),
                "Proceeds (EUR)": _fmt(agg["proceeds"], quant=DECIMAL_EIGHT),
                "Basis (EUR)": _fmt(agg["basis"], quant=DECIMAL_EIGHT),
                "Sale Price (EUR)": _fmt(agg["sale_price"], quant=DECIMAL_EIGHT),
                "Purchase Price (EUR)": _fmt(agg["purchase_price"], quant=DECIMAL_EIGHT),
                "Realized P/L (EUR)": _fmt(agg["realized_pl"], quant=DECIMAL_EIGHT),
                "Realized P/L Wins (EUR)": _fmt(agg["wins"], quant=DECIMAL_EIGHT),
                "Realized P/L Losses (EUR)": _fmt(agg["losses"], quant=DECIMAL_EIGHT),
            },
        )

    output_rows: list[list[str]] = []
    for idx, row in enumerate(rows):
        if len(row) < 2:
            output_rows.append(row)
            continue

        if row[0] == "Trades" and row[1] == "Header":
            output_rows.append(row + ADDED_TRADES_COLUMNS)
            continue

        if row[0] == "Trades":
            base_len = trades_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Trades row encountered before Trades Header")
            padded = row + [""] * (base_len - len(row))
            extras = trades_row_extras.get(idx, [""] * len(ADDED_TRADES_COLUMNS))
            output_rows.append(padded + extras)
            continue

        if row[0] == "Interest" and row[1] == "Header":
            output_rows.append(row + ADDED_INTEREST_COLUMNS)
            continue

        if row[0] == "Interest":
            base_len = interest_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Interest row encountered before Interest Header")
            padded = row + [""] * (base_len - len(row))
            extras = interest_row_extras.get(idx, [""] * len(ADDED_INTEREST_COLUMNS))
            output_rows.append(padded + extras)
            continue

        if row[0] == "Dividends" and row[1] == "Header":
            added_cols = dividends_row_added_columns.get(
                idx,
                [col for col in ADDED_DIVIDENDS_COLUMNS if col not in row[2:]],
            )
            output_rows.append(row + added_cols)
            continue

        if row[0] == "Dividends":
            base_len = dividends_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Dividends row encountered before Dividends Header")
            padded = row + [""] * (base_len - len(row))
            added_cols = dividends_row_added_columns.get(
                idx,
                [col for col in ADDED_DIVIDENDS_COLUMNS if col not in (active_headers.get(idx).headers if active_headers.get(idx) is not None else [])],
            )
            extras_map = dividends_row_extras.get(idx, {})
            extras = [extras_map.get(col, "") for col in added_cols]
            output_rows.append(padded + extras)
            continue

        if row[0] == "Withholding Tax" and row[1] == "Header":
            added_cols = withholding_row_added_columns.get(
                idx,
                [col for col in ADDED_WITHHOLDING_COLUMNS if col not in row[2:]],
            )
            output_rows.append(row + added_cols)
            continue

        if row[0] == "Withholding Tax":
            base_len = withholding_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Withholding Tax row encountered before Withholding Tax Header")
            padded = row + [""] * (base_len - len(row))
            added_cols = withholding_row_added_columns.get(
                idx,
                [col for col in ADDED_WITHHOLDING_COLUMNS if col not in (active_headers.get(idx).headers if active_headers.get(idx) is not None else [])],
            )
            extras_map = withholding_row_extras.get(idx, {})
            extras = [extras_map.get(col, "") for col in added_cols]
            output_rows.append(padded + extras)
            continue

        if row[0] == "Open Positions" and row[1] == "Header":
            added_cols = open_positions_row_added_columns.get(
                idx,
                [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in row[2:]],
            )
            output_rows.append(row + added_cols)
            continue

        if row[0] == "Open Positions":
            base_len = open_positions_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Open Positions row encountered before Open Positions Header")
            padded = row + [""] * (base_len - len(row))
            added_cols = open_positions_row_added_columns.get(
                idx,
                [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in (active_headers.get(idx).headers if active_headers.get(idx) is not None else [])],
            )
            extras_map = open_positions_row_extras.get(idx, {})
            extras = [extras_map.get(col, "") for col in added_cols]
            output_rows.append(padded + extras)
            continue

        output_rows.append(row)

    for idx, row in enumerate(output_rows):
        if len(row) >= 2 and row[0] == "Trades":
            base_len = trades_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Trades row encountered before Trades Header")
            expected_len = base_len + len(ADDED_TRADES_COLUMNS)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Trades row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )
        if len(row) >= 2 and row[0] == "Interest":
            base_len = interest_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Interest row encountered before Interest Header")
            expected_len = base_len + len(ADDED_INTEREST_COLUMNS)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Interest row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )
        if len(row) >= 2 and row[0] == "Dividends":
            base_len = dividends_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Dividends row encountered before Dividends Header")
            added_cols = dividends_row_added_columns.get(idx)
            if added_cols is None:
                if row[1] == "Header":
                    added_cols = [col for col in ADDED_DIVIDENDS_COLUMNS if col not in row[2:]]
                else:
                    active_header = active_headers.get(idx)
                    if active_header is None:
                        raise CsvStructureError(f"row {idx + 1}: Dividends row encountered before Dividends Header")
                    added_cols = [col for col in ADDED_DIVIDENDS_COLUMNS if col not in active_header.headers]
            expected_len = base_len + len(added_cols)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Dividends row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )
        if len(row) >= 2 and row[0] == "Withholding Tax":
            base_len = withholding_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Withholding Tax row encountered before Withholding Tax Header")
            added_cols = withholding_row_added_columns.get(idx)
            if added_cols is None:
                if row[1] == "Header":
                    added_cols = [col for col in ADDED_WITHHOLDING_COLUMNS if col not in row[2:]]
                else:
                    active_header = active_headers.get(idx)
                    if active_header is None:
                        raise CsvStructureError(f"row {idx + 1}: Withholding Tax row encountered before Withholding Tax Header")
                    added_cols = [col for col in ADDED_WITHHOLDING_COLUMNS if col not in active_header.headers]
            expected_len = base_len + len(added_cols)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Withholding Tax row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )
        if len(row) >= 2 and row[0] == "Open Positions":
            base_len = open_positions_row_base_len.get(idx)
            if base_len is None:
                raise CsvStructureError(f"row {idx + 1}: Open Positions row encountered before Open Positions Header")
            added_cols = open_positions_row_added_columns.get(idx)
            if added_cols is None:
                if row[1] == "Header":
                    added_cols = [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in row[2:]]
                else:
                    active_header = active_headers.get(idx)
                    if active_header is None:
                        raise CsvStructureError(f"row {idx + 1}: Open Positions row encountered before Open Positions Header")
                    added_cols = [col for col in ADDED_OPEN_POSITIONS_COLUMNS if col not in active_header.headers]
            expected_len = base_len + len(added_cols)
            if len(row) != expected_len:
                raise IbkrAnalyzerError(
                    f"Open Positions row column count mismatch at row {idx + 1}: expected {expected_len}, got {len(row)}"
                )

    alias_suffix = f"_{normalized_alias}" if normalized_alias else ""
    output_csv_path = out_dir / f"ibkr_activity{alias_suffix}_modified_{tax_year}.csv"
    declaration_txt_path = out_dir / f"ibkr_activity{alias_suffix}_declaration_{tax_year}.txt"

    with output_csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerows(output_rows)

    sanity = _run_sanity_checks(
        rows=rows,
        active_headers=active_headers,
        listings=listings,
        output_dir=out_dir,
        normalized_alias=normalized_alias,
        tax_year=tax_year,
    )
    summary.sanity_passed = sanity.passed
    summary.sanity_checked_closing_trades = sanity.checked_closing_trades
    summary.sanity_checked_closedlots = sanity.checked_closedlots
    summary.sanity_checked_subtotals = sanity.checked_subtotals
    summary.sanity_checked_totals = sanity.checked_totals
    summary.sanity_forex_ignored_rows = sanity.forex_ignored_rows
    summary.sanity_debug_artifacts_dir = str(sanity.debug_dir)
    summary.sanity_debug_csv_path = str(sanity.debug_csv_path)
    summary.sanity_report_path = str(sanity.report_path)
    summary.sanity_failures_count = len(sanity.failures)
    summary.sanity_failure_messages = [failure.to_message() for failure in sanity.failures[:50]]

    result = AnalysisResult(
        input_csv_path=input_path,
        output_csv_path=output_csv_path,
        declaration_txt_path=declaration_txt_path,
        report_alias=normalized_alias,
        summary=summary,
    )

    declaration_txt_path.write_text(_build_declaration_text(result), encoding="utf-8")
    if not sanity.passed:
        report_exists = sanity.report_path.exists()
        debug_exists = sanity.debug_csv_path.exists()
        raise IbkrAnalyzerError(
            "SANITY CHECKS FAILED: {count} issues.\n"
            "Sanity report: {report} (exists={report_exists})\n"
            "Sanity debug CSV: {debug} (exists={debug_exists})".format(
                count=len(sanity.failures),
                report=sanity.report_path,
                debug=sanity.debug_csv_path,
                report_exists=str(report_exists).lower(),
                debug_exists=str(debug_exists).lower(),
            )
        )

    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ibkr-activity-analyzer")
    parser.add_argument("--input", type=Path, required=True, help="IBKR Activity Statement CSV")
    parser.add_argument("--tax-year", type=int, required=True, help="Tax year")
    parser.add_argument(
        "--tax-exempt-mode",
        choices=[TAX_MODE_LISTED_SYMBOL, TAX_MODE_EXECUTION_EXCHANGE],
        required=True,
        help="Tax exempt classification mode",
    )
    parser.add_argument(
        "--appendix8-dividend-list-mode",
        choices=[APPENDIX8_LIST_MODE_COMPANY, APPENDIX8_LIST_MODE_COUNTRY],
        default=APPENDIX8_LIST_MODE_COMPANY,
        help="Appendix 8 dividend listing mode (default: company)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: output/ibkr/activity_statement)",
    )
    parser.add_argument(
        "--report-alias",
        help="Optional report alias to include in output filenames (for multiple accounts)",
    )
    parser.add_argument("--cache-dir", type=Path, help="Optional bnb_fx cache dir override")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        result = analyze_ibkr_activity_statement(
            input_csv=args.input,
            tax_year=args.tax_year,
            tax_exempt_mode=args.tax_exempt_mode,
            appendix8_dividend_list_mode=args.appendix8_dividend_list_mode,
            report_alias=args.report_alias,
            output_dir=args.output_dir,
            cache_dir=args.cache_dir,
        )
    except IbkrAnalyzerError as exc:
        logger.error("%s", exc)
        return 2

    summary = result.summary
    print(f"processed_rows: {summary.processed_trades_in_tax_year}")
    print(f"ignored_rows: {summary.ignored_non_closing_trade_rows + summary.forex_ignored_rows + summary.trades_outside_tax_year}")
    print(f"trades_data_rows_total: {summary.trades_data_rows_total}")
    print(f"trade_discriminator_rows: {summary.trade_discriminator_rows}")
    print(f"closedlot_discriminator_rows: {summary.closedlot_discriminator_rows}")
    print(f"order_discriminator_rows: {summary.order_discriminator_rows}")
    print(f"closing_trade_candidates: {summary.closing_trade_candidates}")
    print(f"forex_ignored_rows: {summary.forex_ignored_rows}")
    print(f"ignored_non_closing_trade_rows: {summary.ignored_non_closing_trade_rows}")
    print(f"trades_outside_tax_year: {summary.trades_outside_tax_year}")
    print(f"appendix_5_rows: {summary.appendix_5.rows}")
    print(f"appendix_13_rows: {summary.appendix_13.rows}")
    print(f"review_rows: {summary.review_rows}")
    print(f"review_status_overrides_rows: {summary.review_status_overrides_rows}")
    print(f"unknown_review_status_rows: {summary.unknown_review_status_rows}")
    if summary.unknown_review_status_values:
        print(f"unknown_review_status_values: {', '.join(sorted(summary.unknown_review_status_values))}")
    print(f"interest_processed_rows: {summary.interest_processed_rows}")
    print(f"interest_total_rows_skipped: {summary.interest_total_rows_skipped}")
    print(f"interest_taxable_rows: {summary.interest_taxable_rows}")
    print(f"interest_non_taxable_rows: {summary.interest_non_taxable_rows}")
    print(f"interest_unknown_rows: {summary.interest_unknown_rows}")
    if summary.interest_unknown_types:
        print(f"interest_unknown_types: {', '.join(sorted(summary.interest_unknown_types))}")
    print(f"dividends_processed_rows: {summary.dividends_processed_rows}")
    print(f"dividends_total_rows_skipped: {summary.dividends_total_rows_skipped}")
    print(f"dividends_cash_rows: {summary.dividends_cash_rows}")
    print(f"dividends_lieu_rows: {summary.dividends_lieu_rows}")
    print(f"dividends_unknown_rows: {summary.dividends_unknown_rows}")
    print(f"withholding_processed_rows: {summary.withholding_processed_rows}")
    print(f"withholding_total_rows_skipped: {summary.withholding_total_rows_skipped}")
    print(f"withholding_dividend_rows: {summary.withholding_dividend_rows}")
    print(f"withholding_non_dividend_rows: {summary.withholding_non_dividend_rows}")
    print(f"open_positions_summary_rows: {summary.open_positions_summary_rows}")
    print(f"appendix_8_part1_rows: {summary.open_positions_part1_rows}")
    print(f"dividend_tax_rate: {_fmt(summary.dividend_tax_rate)}")
    print(f"appendix8_dividend_list_mode: {summary.appendix8_dividend_list_mode}")
    print(f"appendix_5_profit_eur: {_fmt(summary.appendix_5.wins_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_5_loss_eur: {_fmt(summary.appendix_5.losses_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_13_profit_eur: {_fmt(summary.appendix_13.wins_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_13_loss_eur: {_fmt(summary.appendix_13.losses_eur, quant=DECIMAL_TWO)}")
    print(f"review_profit_eur: {_fmt(summary.review.wins_eur, quant=DECIMAL_TWO)}")
    print(f"review_loss_eur: {_fmt(summary.review.losses_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_6_code_603_eur: {_fmt(summary.appendix_6_code_603_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_9_credit_interest_eur: {_fmt(summary.appendix_9_credit_interest_eur, quant=DECIMAL_TWO)}")
    print(f"appendix_9_withholding_paid_eur: {_fmt(summary.appendix_9_withholding_paid_eur, quant=DECIMAL_TWO)}")
    print("SANITY CHECKS PASSED" if summary.sanity_passed else "SANITY CHECKS FAILED")
    print(f"sanity_checks_passed: {'YES' if summary.sanity_passed else 'NO'}")
    print(f"sanity_checked_trade_rows: {summary.sanity_checked_closing_trades}")
    print(f"sanity_checked_closedlots: {summary.sanity_checked_closedlots}")
    print(f"sanity_checked_subtotals: {summary.sanity_checked_subtotals}")
    print(f"sanity_checked_totals: {summary.sanity_checked_totals}")
    print(f"sanity_forex_ignored_rows: {summary.sanity_forex_ignored_rows}")
    print(f"Modified CSV: {result.output_csv_path}")
    print(f"Declaration TXT: {result.declaration_txt_path}")
    if summary.tax_credit_debug_report_path:
        print(f"Tax credit debug report: {summary.tax_credit_debug_report_path}")
    print(f"Sanity-check debug artifacts written to: {summary.sanity_debug_artifacts_dir}")
    print("These are verification artifacts, not production tax outputs.")
    if summary.warnings:
        print("Warnings:")
        for warning in summary.warnings:
            print(f"- {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
