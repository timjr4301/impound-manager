"""
Vehicle valuation. Tries VinAudit first (real comp-based pricing, needs
VINAUDIT_API_KEY — not currently set, so this path is dormant in
production), then falls back to a Claude-generated estimate off the
vehicle's year/make/model/mileage (same Anthropic account already used for
damage photos and document reading elsewhere in this app), and only falls
back to the flat $3,499 default when neither can produce a number.
"""

import json
import os
import re
import requests

DEFAULT_VALUE = 3499
_VINAUDIT_URL = 'https://api.vinaudit.com/query.php'

_CLAUDE_VALUE_SYSTEM_PROMPT = """You are estimating the wholesale/trade-in
value of a used vehicle for a towing company in Columbus, Ohio that is
about to sell or junk an unclaimed impounded vehicle. This is a rough
estimate to compare against towing/storage charges for title-transfer
paperwork — not an appraisal, and the vehicle's exact condition is usually
unknown (it sat impounded, condition may be rough).

Respond ONLY with valid JSON, no markdown, no backticks, no preamble:

{
  "value": <number, whole dollars, your best single wholesale/trade-in estimate>,
  "confidence": "low" | "medium" | "high",
  "reasoning": "<one short sentence citing typical market range you're drawing on>"
}

Assume average-to-rough condition (it's an impounded vehicle, unknown
maintenance history) unless told otherwise. If the vehicle is too
obscure/old to estimate confidently, still give your best number and mark
confidence "low" rather than refusing."""


def _claude_estimate(year, make, model, mileage, vin, api_key):
    """Ask Claude for a rough wholesale estimate. Returns a result dict or
    None if it couldn't produce one (missing key, missing vehicle info, API
    error, bad response) — caller falls back to the flat default."""
    if not api_key or not (year or make or model):
        return None
    import anthropic
    desc = ' '.join(str(p) for p in (year, make, model) if p) or 'unknown vehicle'
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=300,
            system=_CLAUDE_VALUE_SYSTEM_PROMPT,
            messages=[{
                'role': 'user',
                'content': (f'Vehicle: {desc}\nMileage: {mileage or "unknown"}\n'
                            f'VIN: {vin or "unknown"}\n'
                            'Give your best wholesale/trade-in value estimate.'),
            }],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        parsed = json.loads(raw)
        value = float(parsed['value'])
        if value <= 0:
            return None
        return {
            'value': round(value, 2),
            'source': 'CLAUDE_ESTIMATE',
            'condition': f'AI estimate — {desc}',
            'screenshot_pdf': None,
            'url': '',
            'confidence': parsed.get('confidence', 'medium'),
            'notes': f'AI-estimated wholesale value for {desc}: {parsed.get("reasoning", "")}',
            'used_default': False,
        }
    except Exception:
        return None


def lookup_wholesale_value(vin, mileage=80000, zip_code='43219', api_key=None,
                            fallback_value=DEFAULT_VALUE, year=None, make=None, model=None):
    """
    Returns dict: value, source, condition, screenshot_pdf, url, confidence, notes, used_default.
    Uses VinAudit trade-in value as the wholesale proxy when available, else
    a Claude estimate off year/make/model/mileage, else the flat default.
    """
    vinaudit_key = os.environ.get('VINAUDIT_API_KEY', '')

    if not vinaudit_key:
        estimate = _claude_estimate(year, make, model, mileage, vin, api_key)
        if estimate:
            return estimate
        return {
            'value': fallback_value,
            'source': 'DEFAULT',
            'condition': 'Worst-case default',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'none',
            'notes': 'VINAUDIT_API_KEY not set and no AI estimate available (missing vehicle '
                     'info or ANTHROPIC_API_KEY) — using default fallback value.',
            'used_default': True,
        }

    if not vin or len(vin) < 11:
        estimate = _claude_estimate(year, make, model, mileage, vin, api_key)
        if estimate:
            return estimate
        return {
            'value': fallback_value,
            'source': 'DEFAULT',
            'condition': 'Invalid VIN',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'none',
            'notes': f'VIN "{vin}" is too short for a reliable lookup.',
            'used_default': True,
        }

    try:
        resp = requests.get(
            _VINAUDIT_URL,
            params={
                'id': vin.strip().upper(),
                'format': 'json',
                'api_key': vinaudit_key,
                'mileage': int(mileage) if mileage else 80000,
                'country': 'us',
                'period': '90',
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get('success') or not data.get('prices'):
            estimate = _claude_estimate(year, make, model, mileage, vin, api_key)
            if estimate:
                return estimate
            return {
                'value': fallback_value,
                'source': 'VINAUDIT_NO_DATA',
                'condition': 'No listings found',
                'screenshot_pdf': None,
                'url': '',
                'confidence': 'none',
                'notes': f'VinAudit returned no price data for VIN {vin}. Using fallback.',
                'used_default': True,
            }

        prices = data['prices']
        # Prefer trade_in (wholesale proxy), then private_party, then retail
        trade_in = prices.get('trade_in') or prices.get('tradein')
        private_party = prices.get('private_party') or prices.get('privateparty')
        retail = prices.get('retail')

        raw_value = trade_in or private_party or retail
        if not raw_value:
            estimate = _claude_estimate(year, make, model, mileage, vin, api_key)
            if estimate:
                return estimate
            return {
                'value': fallback_value,
                'source': 'VINAUDIT_NO_PRICE',
                'condition': 'Price fields empty',
                'screenshot_pdf': None,
                'url': '',
                'confidence': 'none',
                'notes': f'VinAudit returned empty prices for VIN {vin}. Using fallback.',
                'used_default': True,
            }

        value = float(raw_value)
        source_label = 'trade_in' if trade_in else ('private_party' if private_party else 'retail')
        count = data.get('count', 0)

        return {
            'value': round(value, 2),
            'source': f'VINAUDIT_{source_label.upper()}',
            'condition': f'{source_label.replace("_", " ").title()} ({count} listings, 90-day window)',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'high' if count >= 5 else ('medium' if count >= 2 else 'low'),
            'notes': (
                f'VinAudit {source_label.replace("_", " ")} value for {vin}. '
                f'{count} comparable listings. Mileage: {mileage:,}.'
            ),
            'used_default': False,
        }

    except requests.RequestException as exc:
        estimate = _claude_estimate(year, make, model, mileage, vin, api_key)
        if estimate:
            return estimate
        return {
            'value': fallback_value,
            'source': 'VINAUDIT_ERROR',
            'condition': 'API request failed',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'none',
            'notes': f'VinAudit API error: {exc}. Using fallback value.',
            'used_default': True,
        }
    except Exception as exc:
        estimate = _claude_estimate(year, make, model, mileage, vin, api_key)
        if estimate:
            return estimate
        return {
            'value': fallback_value,
            'source': 'ERROR',
            'condition': 'Lookup failed',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'none',
            'notes': str(exc),
            'used_default': True,
        }
