"""Every country with its flag, name and international dialling code.

Used by the phone-number picker (ui/widgets.py). Pure Python, no dependencies, easy to test.
Order matters in one place only: when several countries share a dialling code (+1, +7, +44 ...)
the FIRST one listed is the one chosen when an existing number is split back into
country + local part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# ISO-2 | English name | dialling code (digits only, no "+")
_RAW = """
PK|Pakistan|92
US|United States|1
CA|Canada|1
GB|United Kingdom|44
IN|India|91
AE|United Arab Emirates|971
SA|Saudi Arabia|966
RU|Russia|7
KZ|Kazakhstan|7
AF|Afghanistan|93
AL|Albania|355
DZ|Algeria|213
AS|American Samoa|1684
AD|Andorra|376
AO|Angola|244
AI|Anguilla|1264
AG|Antigua and Barbuda|1268
AR|Argentina|54
AM|Armenia|374
AW|Aruba|297
AU|Australia|61
AT|Austria|43
AZ|Azerbaijan|994
BS|Bahamas|1242
BH|Bahrain|973
BD|Bangladesh|880
BB|Barbados|1246
BY|Belarus|375
BE|Belgium|32
BZ|Belize|501
BJ|Benin|229
BM|Bermuda|1441
BT|Bhutan|975
BO|Bolivia|591
BA|Bosnia and Herzegovina|387
BW|Botswana|267
BR|Brazil|55
IO|British Indian Ocean Territory|246
VG|British Virgin Islands|1284
BN|Brunei|673
BG|Bulgaria|359
BF|Burkina Faso|226
BI|Burundi|257
KH|Cambodia|855
CM|Cameroon|237
CV|Cape Verde|238
KY|Cayman Islands|1345
CF|Central African Republic|236
TD|Chad|235
CL|Chile|56
CN|China|86
CX|Christmas Island|61
CC|Cocos Islands|61
CO|Colombia|57
KM|Comoros|269
CG|Congo (Republic)|242
CD|Congo (DR)|243
CK|Cook Islands|682
CR|Costa Rica|506
CI|Cote d'Ivoire|225
HR|Croatia|385
CU|Cuba|53
CW|Curacao|599
CY|Cyprus|357
CZ|Czech Republic|420
DK|Denmark|45
DJ|Djibouti|253
DM|Dominica|1767
DO|Dominican Republic|1809
EC|Ecuador|593
EG|Egypt|20
SV|El Salvador|503
GQ|Equatorial Guinea|240
ER|Eritrea|291
EE|Estonia|372
SZ|Eswatini|268
ET|Ethiopia|251
FK|Falkland Islands|500
FO|Faroe Islands|298
FJ|Fiji|679
FI|Finland|358
FR|France|33
GF|French Guiana|594
PF|French Polynesia|689
GA|Gabon|241
GM|Gambia|220
GE|Georgia|995
DE|Germany|49
GH|Ghana|233
GI|Gibraltar|350
GR|Greece|30
GL|Greenland|299
GD|Grenada|1473
GP|Guadeloupe|590
GU|Guam|1671
GT|Guatemala|502
GG|Guernsey|44
GN|Guinea|224
GW|Guinea-Bissau|245
GY|Guyana|592
HT|Haiti|509
HN|Honduras|504
HK|Hong Kong|852
HU|Hungary|36
IS|Iceland|354
ID|Indonesia|62
IR|Iran|98
IQ|Iraq|964
IE|Ireland|353
IM|Isle of Man|44
IL|Israel|972
IT|Italy|39
JM|Jamaica|1876
JP|Japan|81
JE|Jersey|44
JO|Jordan|962
KE|Kenya|254
KI|Kiribati|686
XK|Kosovo|383
KW|Kuwait|965
KG|Kyrgyzstan|996
LA|Laos|856
LV|Latvia|371
LB|Lebanon|961
LS|Lesotho|266
LR|Liberia|231
LY|Libya|218
LI|Liechtenstein|423
LT|Lithuania|370
LU|Luxembourg|352
MO|Macau|853
MG|Madagascar|261
MW|Malawi|265
MY|Malaysia|60
MV|Maldives|960
ML|Mali|223
MT|Malta|356
MH|Marshall Islands|692
MQ|Martinique|596
MR|Mauritania|222
MU|Mauritius|230
YT|Mayotte|262
MX|Mexico|52
FM|Micronesia|691
MD|Moldova|373
MC|Monaco|377
MN|Mongolia|976
ME|Montenegro|382
MS|Montserrat|1664
MA|Morocco|212
MZ|Mozambique|258
MM|Myanmar|95
NA|Namibia|264
NR|Nauru|674
NP|Nepal|977
NL|Netherlands|31
NC|New Caledonia|687
NZ|New Zealand|64
NI|Nicaragua|505
NE|Niger|227
NG|Nigeria|234
NU|Niue|683
NF|Norfolk Island|672
KP|North Korea|850
MK|North Macedonia|389
MP|Northern Mariana Islands|1670
NO|Norway|47
OM|Oman|968
PW|Palau|680
PS|Palestine|970
PA|Panama|507
PG|Papua New Guinea|675
PY|Paraguay|595
PE|Peru|51
PH|Philippines|63
PL|Poland|48
PT|Portugal|351
PR|Puerto Rico|1787
QA|Qatar|974
RE|Reunion|262
RO|Romania|40
RW|Rwanda|250
BL|Saint Barthelemy|590
SH|Saint Helena|290
KN|Saint Kitts and Nevis|1869
LC|Saint Lucia|1758
MF|Saint Martin|590
PM|Saint Pierre and Miquelon|508
VC|Saint Vincent and the Grenadines|1784
WS|Samoa|685
SM|San Marino|378
ST|Sao Tome and Principe|239
SN|Senegal|221
RS|Serbia|381
SC|Seychelles|248
SL|Sierra Leone|232
SG|Singapore|65
SX|Sint Maarten|1721
SK|Slovakia|421
SI|Slovenia|386
SB|Solomon Islands|677
SO|Somalia|252
ZA|South Africa|27
KR|South Korea|82
SS|South Sudan|211
ES|Spain|34
LK|Sri Lanka|94
SD|Sudan|249
SR|Suriname|597
SE|Sweden|46
CH|Switzerland|41
SY|Syria|963
TW|Taiwan|886
TJ|Tajikistan|992
TZ|Tanzania|255
TH|Thailand|66
TL|Timor-Leste|670
TG|Togo|228
TK|Tokelau|690
TO|Tonga|676
TT|Trinidad and Tobago|1868
TN|Tunisia|216
TR|Turkey|90
TM|Turkmenistan|993
TC|Turks and Caicos Islands|1649
TV|Tuvalu|688
UG|Uganda|256
UA|Ukraine|380
UY|Uruguay|598
VI|US Virgin Islands|1340
UZ|Uzbekistan|998
VU|Vanuatu|678
VA|Vatican City|39
VE|Venezuela|58
VN|Vietnam|84
WF|Wallis and Futuna|681
EH|Western Sahara|212
YE|Yemen|967
ZM|Zambia|260
ZW|Zimbabwe|263
"""

DEFAULT_COUNTRY = "PK"
# Countries whose national numbers legitimately START with 0 after the country code (keep that 0).
KEEP_LEADING_ZERO = frozenset({"IT", "VA", "SM"})


@dataclass(frozen=True)
class Country:
    iso: str
    name: str
    dial: str

    @property
    def flag(self) -> str:
        """Flag emoji built from the two letters (Windows shows the letters instead of a flag)."""
        return "".join(chr(0x1F1E6 + ord(c) - 65) for c in self.iso) if len(self.iso) == 2 else ""

    @property
    def label(self) -> str:
        return f"{self.flag} {self.name} (+{self.dial})"


COUNTRIES: tuple[Country, ...] = tuple(
    Country(*line.split("|")) for line in _RAW.strip().splitlines()
)
BY_ISO: dict[str, Country] = {c.iso: c for c in COUNTRIES}
ISO_CODES: list[str] = [c.iso for c in COUNTRIES]

# dial code -> first (preferred) country that uses it
_BY_DIAL: dict[str, Country] = {}
for _c in COUNTRIES:
    _BY_DIAL.setdefault(_c.dial, _c)
_MAX_DIAL = max(len(d) for d in _BY_DIAL)


def get(iso: Optional[str]) -> Country:
    return BY_ISO.get((iso or "").upper(), BY_ISO[DEFAULT_COUNTRY])


def label(iso: str) -> str:
    return get(iso).label


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def split_phone(value: str, default_iso: str = DEFAULT_COUNTRY) -> tuple[str, str]:
    """Split a stored number into ``(country ISO, local number)``.

    ``+92 3001234567`` -> ("PK", "3001234567"). A number without ``+``/``00`` is taken as a local
    number of ``default_iso``. Unknown prefixes fall back to ``default_iso`` with everything as local part.
    """
    raw = (value or "").strip()
    default = get(default_iso)
    if not raw:
        return default.iso, ""
    digits = _digits(raw)
    international = raw.startswith("+") or digits.startswith("00")
    if digits.startswith("00") and not raw.startswith("+"):
        digits = digits[2:]
    if not international:
        return default.iso, digits.lstrip("0") if default.iso not in KEEP_LEADING_ZERO else digits
    for size in range(min(_MAX_DIAL, len(digits)), 0, -1):
        country = _BY_DIAL.get(digits[:size])
        if country:
            return country.iso, digits[size:]
    return default.iso, digits


def join_phone(iso: str, local: str) -> str:
    """Combine country + local number into the stored form ``+<code> <number>`` (``""`` if no number).

    * A local number may be typed with the national leading 0 (0300 1234567): the 0 is dropped.
    * If the user pastes a full international number (+44 7911 123456) into the number box,
      it wins over the selected country.
    """
    local = (local or "").strip()
    if not _digits(local):
        return ""
    if local.startswith("+") or _digits(local).startswith("00") and local.startswith("00"):
        iso2, rest = split_phone(local, iso)
        return f"+{get(iso2).dial} {rest}" if rest else ""
    country = get(iso)
    digits = _digits(local)
    if country.iso not in KEEP_LEADING_ZERO:
        digits = digits.lstrip("0")
    return f"+{country.dial} {digits}" if digits else ""


def phone_problem(value: str) -> str:
    """Empty string when the number is fine (or empty); otherwise a short English reason."""
    if not (value or "").strip():
        return ""
    digits = _digits(value)
    if not 7 <= len(digits) <= 15:
        return "Phone number looks incomplete or too long (7 to 15 digits including the country code)."
    return ""
