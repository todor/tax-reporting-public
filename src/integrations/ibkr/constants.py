from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Callable

from config import OUTPUT_DIR

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

EU_NON_REGULATED_MARKETS = {
    "EUIBSI",
    "EUIBFRSH",
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
    "TRWBIT",
    "TRWBEN",
    "TRADEWEBG",
    "SWB",
}

KNOWN_NON_EU_MARKETS = {
    # US
    "NYSE",
    "NASDAQ",
    "ARCA",
    "AMEX",
    "IEX",
    "BEX",
    # UK
    "LSE",
    "AIM",
    # Switzerland
    "SWX",
    "VTX",
    # Canada
    "TSX",
    "TSXV",
    "NEO",
    # Australia
    "ASX",
    # Japan
    "TSEJ",
    "OSE.JPN",
    # Hong Kong
    "SEHK",
    "HKFE",
    # Singapore
    "SGX",
    # India
    "NSE",
    "BSE",
}

INVALID_EXCHANGE_VALUES = {
    "",
    "-",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "UNKNOWN",
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
EXCHANGE_CLASS_NON_EU = "NON_EU"
EXCHANGE_CLASS_UNMAPPED = "UNMAPPED"
EXCHANGE_CLASS_INVALID = "INVALID"
# Backward-compatible alias for tests/callers still importing UNKNOWN.
EXCHANGE_CLASS_UNKNOWN = EXCHANGE_CLASS_UNMAPPED

EXCHANGE_CLASSIFICATION_MODE_OPEN_WORLD = "OPEN_WORLD MODE"
EXCHANGE_CLASSIFICATION_MODE_CLOSED_WORLD = "CLOSED_WORLD MODE"

# Backward-compatible alias kept while callers migrate.
EU_NON_REGULATED = EU_NON_REGULATED_MARKETS
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
