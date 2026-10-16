"""The price table of a quotation: validated and added up on the server, so the
figures in the email are always right, whatever the browser sent."""
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from rest_framework.exceptions import ValidationError

MAX_ROWS = 50
SYMBOLS = {"USD": "$", "NGN": "₦", "GBP": "£", "EUR": "€", "CNY": "¥", "GHS": "GH₵", "ZAR": "R", "AED": "AED "}
CENT = Decimal("0.01")


def money(value, currency):
    value = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{abs(value):,.2f}"


def _dec(value, label, low, high, places=2, required=True):
    if value in (None, ""):
        if required:
            raise ValidationError(f"{label}: enter a number.")
        return Decimal(0)
    try:
        d = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{label}: '{value}' is not a number.")
    if not d.is_finite() or d < low or d > high:
        raise ValidationError(f"{label}: use a number between {low} and {high}.")
    return d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def parse_items(raw):
    """The form sends the rows as a JSON text. Returns a clean list, or []."""
    if raw in (None, "", "[]"):
        return []
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ValidationError("The price table could not be read.")
    if not isinstance(rows, list):
        raise ValidationError("The price table could not be read.")
    if len(rows) > MAX_ROWS:
        raise ValidationError(f"Use at most {MAX_ROWS} rows in the price table.")
    clean = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValidationError(f"Row {i} could not be read.")
        description = str(row.get("description", "")).strip()
        if not description:
            raise ValidationError(f"Row {i}: describe the item.")
        if len(description) > 200:
            raise ValidationError(f"Row {i}: keep the description under 200 characters.")
        clean.append({
            "description": description,
            "quantity": _dec(row.get("quantity"), f"Row {i} quantity", Decimal("0.001"), Decimal(1_000_000), places=3),
            "unit_price": _dec(row.get("unit_price"), f"Row {i} unit price", Decimal(0), Decimal(1_000_000_000)),
        })
    return clean


def build(rows, currency, shipping=None, vat_percent=None):
    """Adds the table up. Everything is returned as text (safe to store and to show)."""
    if not rows:
        return {}
    out_rows, subtotal = [], Decimal(0)
    for r in rows:
        line = (r["quantity"] * r["unit_price"]).quantize(CENT, rounding=ROUND_HALF_UP)
        subtotal += line
        qty = f"{r['quantity'].normalize():f}"
        out_rows.append({
            "description": r["description"], "quantity": qty,
            "unit_price": str(r["unit_price"]), "unit_price_text": money(r["unit_price"], currency),
            "line_total": str(line), "line_total_text": money(line, currency),
        })
    shipping = (shipping or Decimal(0)).quantize(CENT, rounding=ROUND_HALF_UP)
    vat_percent = (vat_percent or Decimal(0)).quantize(CENT, rounding=ROUND_HALF_UP)
    vat = ((subtotal + shipping) * vat_percent / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    total = subtotal + shipping + vat
    return {
        "currency": currency, "rows": out_rows,
        "subtotal": str(subtotal), "subtotal_text": money(subtotal, currency),
        "shipping": str(shipping), "shipping_text": money(shipping, currency) if shipping else "",
        "vat_percent": str(vat_percent.normalize()) if vat_percent else "",
        "vat": str(vat), "vat_text": money(vat, currency) if vat_percent else "",
        "total": str(total), "total_text": money(total, currency),
    }


def optional_amount(value, label, high):
    return _dec(value, label, Decimal(0), high, required=False) if value not in (None, "") else None
