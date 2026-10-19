"""Everything an administrator can change from the Customize page, as plain data + pure functions.

No Streamlit and no database in this module, so every rule here is unit-tested without either.
* Form layouts   - which fields the Client and Invoice forms show, their labels, order, extra fields.
* Dropdown lists - categories and payment methods offered in forms.
* Theme          - accent colour, font, roundness ... turned into CSS.
Storage lives in services/formconfig.py; drawing the fields lives in ui/widgets.py.
"""

from __future__ import annotations

import copy
import re
import uuid
from datetime import date
from typing import Any, Optional

FIELD_TYPES = ("text", "long_text", "number", "date", "dropdown", "checkbox", "phone")
TYPE_NAMES = {  # shown in the designer (translated there)
    "text": "Short text", "long_text": "Long text", "number": "Number", "date": "Date",
    "dropdown": "Dropdown list", "checkbox": "Tick box (yes/no)", "phone": "Phone number",
}
MAX_FIELDS = 40
MAX_LABEL = 60
MAX_OPTIONS = 60

# --------------------------------------------------------------------------- built-in fields
# ``label`` is the English default (translated at display time). An admin rename is stored in
# the field's ``custom_label`` so the default keeps following the selected language.
def _b(key: str, label: str, ftype: str = "text", *, locked: bool = False, required: bool = False) -> dict:
    return {"key": key, "label": label, "custom_label": "", "type": ftype, "required": required or locked,
            "visible": True, "options": [], "builtin": True, "locked": locked, "pdf": False}


BUILTIN_FIELDS: dict[str, list[dict]] = {
    "client": [
        _b("name", "Client name", locked=True),
        _b("email", "Email"),
        _b("phone", "WhatsApp / phone", "phone"),
        _b("tax_id", "Tax ID"),
        _b("address", "Address", "long_text"),
    ],
    "invoice": [
        _b("number", "Invoice number"),
        _b("due_date", "Due date", "date"),
        _b("category", "Category", "dropdown"),
        _b("payment_method", "Payment method", "dropdown"),
        _b("expenses", "Expenses (internal cost)", "number"),
        _b("notes", "Notes (printed on the invoice)", "long_text"),
    ],
}
ENTITIES = tuple(BUILTIN_FIELDS)

DEFAULT_LISTS: dict[str, list[str]] = {
    "categories": ["Consulting", "Design", "Development", "Subscriptions", "Products", "Support", "General"],
    "payment_methods": ["Bank Transfer", "Credit Card", "PayPal", "Stripe", "Cash"],
    "product_categories": ["Digital product", "Template", "Course", "Subscription", "Service", "Consulting", "Support", "General"],
    "expense_categories": ["Software & tools", "Advertising", "Hosting & domains", "Payment fees", "Contractors",
                           "Travel", "Office", "Other"],
}
LIST_TITLES = {"categories": "Invoice categories", "payment_methods": "Payment methods",
               "product_categories": "Product categories", "expense_categories": "Expense categories"}


def new_custom_key(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:20] or "field"
    return f"cf_{slug}_{uuid.uuid4().hex[:5]}"


def new_custom_field(label: str, ftype: str = "text", *, required: bool = False, options: Optional[list] = None,
                     pdf: bool = False) -> dict:
    return {"key": new_custom_key(label), "label": label.strip(), "custom_label": "", "type": ftype,
            "required": required, "visible": True, "options": list(options or []), "builtin": False,
            "locked": False, "pdf": pdf}


def sanitize_list(items: Any, fallback: Optional[list[str]] = None) -> list[str]:
    """Trim, drop empties and case-insensitive duplicates, cap sizes. Never returns an empty list."""
    out, seen = [], set()
    for raw in items or []:
        text = " ".join(str(raw).split())[:MAX_LABEL]
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
        if len(out) >= MAX_OPTIONS:
            break
    return out or list(fallback or ["General"])


def get_lists(stored: Optional[dict]) -> dict[str, list[str]]:
    stored = stored if isinstance(stored, dict) else {}
    return {name: sanitize_list(stored.get(name), default) for name, default in DEFAULT_LISTS.items()}


def resolve_fields(entity: str, stored: Optional[list]) -> list[dict]:
    """The ordered field list for a form: stored layout merged with the built-ins.

    Built-ins missing from storage are appended; unknown built-in keys are dropped; locked
    fields are forced visible and required; custom fields are sanitised.
    """
    builtins = {f["key"]: f for f in BUILTIN_FIELDS[entity]}
    result, seen = [], set()
    for raw in stored if isinstance(stored, list) else []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", ""))
        if not key or key in seen:
            continue
        if key in builtins:
            field = copy.deepcopy(builtins[key])
            field["custom_label"] = " ".join(str(raw.get("custom_label", "")).split())[:MAX_LABEL]
            if not field["locked"]:
                field["visible"] = bool(raw.get("visible", True))
                field["required"] = bool(raw.get("required", False))
            field["pdf"] = bool(raw.get("pdf", False)) and entity == "invoice"
        else:
            if not key.startswith("cf_") or raw.get("type") not in FIELD_TYPES:
                continue
            label = " ".join(str(raw.get("label", "")).split())[:MAX_LABEL]
            if not label:
                continue
            field = {"key": key, "label": label, "custom_label": "", "type": raw["type"],
                     "required": bool(raw.get("required", False)), "visible": bool(raw.get("visible", True)),
                     "options": sanitize_list(raw.get("options"), []) if raw["type"] == "dropdown" else [],
                     "builtin": False, "locked": False, "pdf": bool(raw.get("pdf", False)) and entity == "invoice"}
            if field["type"] == "dropdown" and not field["options"]:
                field["options"] = ["Option 1"]
        seen.add(key)
        result.append(field)
    for key, field in builtins.items():
        if key not in seen:
            result.insert(0, copy.deepcopy(field)) if field["locked"] else result.append(copy.deepcopy(field))
    result.sort(key=lambda f: not f["locked"])   # locked fields (client name) always come first; sort is stable
    return result[:MAX_FIELDS + len(builtins)]


def validate_fields(entity: str, fields: list[dict]) -> list[str]:
    problems = []
    customs = [f for f in fields if not f.get("builtin")]
    if len(customs) > MAX_FIELDS:
        problems.append(f"At most {MAX_FIELDS} extra fields are allowed.")
    labels = [(f.get("custom_label") or f.get("label") or "").strip().lower() for f in fields if f.get("visible", True)]
    if len(labels) != len(set(labels)):
        problems.append("Two visible fields have the same name. Give each field its own name.")
    for f in customs:
        if not str(f.get("label", "")).strip():
            problems.append("Every extra field needs a name.")
            break
    return problems


def field_label(field: dict, translate=lambda s: s) -> str:
    return field.get("custom_label") or (translate(field["label"]) if field.get("builtin") else field["label"])


def empty_value(field: dict) -> Any:
    kind = field["type"]
    return False if kind == "checkbox" else None if kind == "date" else 0.0 if kind == "number" else ""


def encode_value(field: dict, value: Any) -> str:
    """Form value -> the text stored in the database ('' means 'no value')."""
    kind = field["type"]
    if value is None:
        return ""
    if kind == "checkbox":
        return "1" if value else ""
    if kind == "date":
        return value.isoformat() if isinstance(value, date) else str(value)
    if kind == "number":
        try:
            return "" if value in ("", None) else f"{float(value):g}"
        except (TypeError, ValueError):
            return ""
    return str(value).strip()


def decode_value(field: dict, text: Optional[str]) -> Any:
    kind = field["type"]
    text = text or ""
    if kind == "checkbox":
        return text == "1"
    if kind == "date":
        try:
            return date.fromisoformat(text) if text else None
        except ValueError:
            return None
    if kind == "number":
        try:
            return float(text) if text else 0.0
        except ValueError:
            return 0.0
    return text


def display_value(field: dict, text: Optional[str]) -> str:
    """Human readable stored value ('' when empty)."""
    kind = field["type"]
    if not text:
        return ""
    if kind == "checkbox":
        return "Yes" if text == "1" else ""
    if kind == "date":
        try:
            return f"{date.fromisoformat(text):%b %d, %Y}"
        except ValueError:
            return text
    return text


def missing_required(fields: list[dict], values: dict[str, Any], translate=lambda s: s) -> list[str]:
    """Labels of visible required CUSTOM/optional fields that are empty."""
    out = []
    for f in fields:
        if f.get("locked") or not f.get("visible", True) or not f.get("required"):
            continue
        if f["key"] not in values:
            continue
        v = values[f["key"]]
        empty = (v in ("", None)) or (f["type"] == "checkbox" and not v) or (f["type"] == "number" and f["key"].startswith("cf_") and not v)
        if empty:
            out.append(field_label(f, translate))
    return out


# --------------------------------------------------------------------------------------- theme
FONTS: dict[str, str] = {   # name -> Google Fonts family query ('' = no download)
    "Plus Jakarta Sans": "Plus+Jakarta+Sans:wght@400;500;600;700;800",
    "Inter": "Inter:wght@400;500;600;700;800",
    "Poppins": "Poppins:wght@400;500;600;700;800",
    "Nunito": "Nunito:wght@400;500;600;700;800",
    "Roboto": "Roboto:wght@400;500;700;900",
    "Open Sans": "Open+Sans:wght@400;500;600;700;800",
    "System default": "",
}
RTL_FONT = ("Noto Naskh Arabic", "Noto+Naskh+Arabic:wght@400;500;600;700")
PRESETS: dict[str, dict[str, str]] = {
    "Ocean Blue": {"accent": "#2563EB", "accent2": "#7C3AED"},
    "Emerald": {"accent": "#0F9D74", "accent2": "#0891B2"},
    "Royal Purple": {"accent": "#7C3AED", "accent2": "#DB2777"},
    "Sunset Orange": {"accent": "#EA580C", "accent2": "#DC2626"},
    "Rose": {"accent": "#E11D48", "accent2": "#9333EA"},
    "Slate": {"accent": "#334155", "accent2": "#2563EB"},
}
MODES = ("auto", "light", "dark")
SIDEBARS = ("default", "tinted")
BACKGROUNDS = ("soft", "tinted", "plain")
DENSITIES = ("comfortable", "compact")
DEFAULT_THEME: dict[str, Any] = {
    "accent": "#2563EB", "accent2": "#7C3AED", "font": "Plus Jakarta Sans", "radius": 16,
    "sidebar": "default", "background": "soft", "density": "comfortable", "mode": "auto", "custom_css": "",
}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def sanitize_theme(stored: Optional[dict]) -> dict[str, Any]:
    stored = stored if isinstance(stored, dict) else {}
    theme = dict(DEFAULT_THEME)
    for key in ("accent", "accent2"):
        if _HEX.match(str(stored.get(key, ""))):
            theme[key] = str(stored[key]).upper()
    if stored.get("font") in FONTS:
        theme["font"] = stored["font"]
    try:
        theme["radius"] = max(0, min(28, int(stored.get("radius", theme["radius"]))))
    except (TypeError, ValueError):
        pass
    for key, allowed in (("sidebar", SIDEBARS), ("background", BACKGROUNDS), ("density", DENSITIES), ("mode", MODES)):
        if stored.get(key) in allowed:
            theme[key] = stored[key]
    css = str(stored.get("custom_css", ""))[:6000]
    theme["custom_css"] = re.sub(r"</?\s*style[^>]*>|<[^>]*>", "", css, flags=re.I)  # CSS only, never HTML
    return theme


def text_on(hex_color: str) -> str:
    """Black or white, whichever is readable on this colour."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#111827" if (0.299 * r + 0.587 * g + 0.114 * b) > 170 else "#FFFFFF"


_MODE_CSS = {
    "dark": """
:root { color-scheme: dark; --lg-border: rgba(255,255,255,.14); }
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background-color: #0E1117; color: #E8EAF0; }
[data-testid="stSidebar"], [data-testid="stSidebar"] > div { background-color: #141924; }
[data-testid="stHeader"] { background: transparent; }
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6, .stApp p, .stApp label, .stApp li,
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stWidgetLabel"], .stApp [data-testid="stCaptionContainer"],
.stApp [data-testid="stMetricValue"], .stApp [data-testid="stMetricLabel"] { color: #E8EAF0; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div, [data-baseweb="textarea"], textarea, input {
  background-color: #1B2230 !important; color: #E8EAF0 !important; border-color: rgba(255,255,255,.16) !important; }
[data-baseweb="popover"], [data-baseweb="popover"] ul, [data-baseweb="menu"], [data-baseweb="calendar"] { background-color: #1B2230 !important; color: #E8EAF0 !important; }
.stButton > button, .stDownloadButton > button, [data-testid="stFormSubmitButton"] > button { background-color: #1B2230; color: #E8EAF0; border-color: rgba(255,255,255,.18); }
.kpi, .empty-card, .totals-box, .side-card, .lg-table-wrap, [data-testid="stVerticalBlockBorderWrapper"], [data-testid="stExpander"] {
  background-color: #151B27; border-color: rgba(255,255,255,.12); }
.lg-table td { color: #E8EAF0; } .lg-table th { color: #AAB2C0; background: #1B2230; }
""",
    "light": """
:root { color-scheme: light; --lg-border: rgba(0,0,0,.12); }
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background-color: #FFFFFF; color: #1F2430; }
[data-testid="stSidebar"], [data-testid="stSidebar"] > div { background-color: #F5F7FB; }
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6, .stApp p, .stApp label, .stApp li,
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stWidgetLabel"], .stApp [data-testid="stCaptionContainer"] { color: #1F2430; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div, [data-baseweb="textarea"], textarea, input {
  background-color: #FFFFFF !important; color: #1F2430 !important; }
[data-baseweb="popover"], [data-baseweb="popover"] ul, [data-baseweb="menu"], [data-baseweb="calendar"] { background-color: #FFFFFF !important; color: #1F2430 !important; }
.lg-table td { color: #1F2430; } .lg-table th { color: #475467; background: color-mix(in srgb, var(--lg-accent) 7%, #f4f6fa); }
""",
}


def theme_css(theme: dict, *, rtl: bool = False) -> str:
    """The stylesheet that applies a theme on top of the two shipped CSS files."""
    t = sanitize_theme(theme)
    a, a2, r = t["accent"], t["accent2"], t["radius"]
    on = text_on(a)
    families = []
    if FONTS[t["font"]]:
        families.append(FONTS[t["font"]])
    if rtl:
        families.append(RTL_FONT[1])
    imports = "".join(f"@import url('https://fonts.googleapis.com/css2?family={f}&display=swap');" for f in families)
    stack = [f"'{t['font']}'" for _ in [0] if FONTS[t["font"]]]
    if rtl:
        stack.insert(0, f"'{RTL_FONT[0]}'")
    font = ", ".join(stack + ["system-ui", "-apple-system", "'Segoe UI'", "sans-serif"])
    compact = t["density"] == "compact"
    css = [imports, f""":root {{ --lg-accent: {a}; --lg-accent-2: {a2}; --lg-card-radius: {r}px; --lg-control-height: {38 if compact else 44}px; }}
html, body, .stApp, .stApp p, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp h1, .stApp h2, .stApp h3,
.stApp h4, .stApp h5, .stApp li, .stApp td, .stApp th {{ font-family: {font}; }}
/* Streamlit's icons are a font: never override it, or icon NAMES show up as text */
[data-testid="stIconMaterial"], [class*="material-symbols"], .material-icons, [data-testid="stIconMaterial"] * {{
  font-family: 'Material Symbols Rounded', 'Material Symbols Outlined', 'Material Icons' !important; font-feature-settings: 'liga'; }}
.dash-title {{ background: linear-gradient(90deg, {a}, {a2} 70%); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }}
[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {{
  background: color-mix(in srgb, {a} 14%, transparent); border-color: color-mix(in srgb, {a} 45%, transparent); color: {a}; }}
[data-testid="stExpandSidebarButton"]:hover, [data-testid="stSidebarCollapseButton"] button:hover {{ border-color: {a}; }}
.lg-table tbody tr:hover td {{ background: color-mix(in srgb, {a} 7%, transparent); }}
.pill--info {{ color: {a}; background: color-mix(in srgb, {a} 13%, transparent); }}
.role-chip {{ color: {a}; background: color-mix(in srgb, {a} 12%, transparent); }}
.empty-card__icon {{ background: color-mix(in srgb, {a} 12%, transparent); }}
.step-num {{ background: {a}; color: {on}; }}
button[kind="primary"], [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"],
.stDownloadButton button[kind="primary"] {{ background-color: {a} !important; border-color: {a} !important; color: {on} !important; }}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {{
  filter: brightness(.92); }}
[data-baseweb="tab-highlight"] {{ background-color: {a} !important; }}
.stTabs [data-baseweb="tab"][aria-selected="true"] {{ color: {a} !important; }}
a, a:visited {{ color: {a}; }}
.stButton > button, .stDownloadButton > button, .stLinkButton > a, [data-testid="stFormSubmitButton"] > button,
[data-testid="stSidebar"] .stButton > button, [data-testid="stSidebar"] .stDownloadButton > button {{ border-radius: {min(r, 16)}px; }}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {{ border-radius: {min(r, 14)}px; }}
[data-testid="stVerticalBlockBorderWrapper"], .kpi, .empty-card, .lg-table-wrap, .totals-box, .side-card {{ border-radius: {r}px; }}"""]
    bg = t["background"]
    if bg == "soft":
        css.append(f".stApp {{ background-image: radial-gradient(1100px 380px at 85% -80px, color-mix(in srgb, {a} 9%, transparent), transparent 70%); }}")
    elif bg == "tinted":
        css.append(f".stApp {{ background-image: linear-gradient(color-mix(in srgb, {a} 6%, transparent), color-mix(in srgb, {a} 6%, transparent)); }}")
    else:
        css.append(".stApp { background-image: none; }")
    if t["sidebar"] == "tinted":
        css.append(f'[data-testid="stSidebar"] {{ background-image: linear-gradient(color-mix(in srgb, {a} 8%, transparent), color-mix(in srgb, {a} 8%, transparent)); }}')
    if compact:
        css.append(".block-container { padding-top: 2.6rem !important; } .kpi { min-height: 126px; padding: .8rem 1rem .75rem; } "
                   ".kpi__value { font-size: 1.65rem; } .lg-table td { padding: .42rem .8rem; } .lg-table th { padding: .5rem .8rem; }")
    if rtl:
        css.append("""
.stApp, [data-testid="stSidebar"], [data-testid="stMain"] { direction: rtl; }
.stApp, .stApp p, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp li, .stApp [data-testid="stMarkdownContainer"] { text-align: right; }
.lg-table th, .lg-table td { text-align: right; }
.lg-table .num, .lg-table th.num { text-align: left; }
.kpi::before { left: auto; right: 0; }
.js-plotly-plot, .plotly, .kpi__value, .totals-box span:last-child, [class*="st-key-ltr_"] { direction: ltr; unicode-bidi: plaintext; }
[class*="st-key-ltr_"] input { direction: ltr; text-align: left; }
[data-testid="stSidebar"] [role="radiogroup"] > label { text-align: right; }
""")
    if t["mode"] != "auto":
        css.append(_MODE_CSS[t["mode"]])
    if t["custom_css"].strip():
        css.append(t["custom_css"])
    return "\n".join(css)
