"""
Vehicle valuation via VinAudit API.
Replaces the old Playwright/NADA scraper which doesn't work on Render.
Set VINAUDIT_API_KEY in Render env vars to enable live lookups.
Falls back to $3,499 when the key is missing or the lookup fails.
"""

import os
import requests

DEFAULT_VALUE = 3499
_VINAUDIT_URL = 'https://api.vinaudit.com/query.php'


def lookup_wholesale_value(vin, mileage=80000, zip_code='43219', api_key=None, fallback_value=DEFAULT_VALUE):
    """
    Returns dict: value, source, condition, screenshot_pdf, url, confidence, notes, used_default.
    Uses VinAudit trade-in value as the wholesale proxy.
    """
    vinaudit_key = os.environ.get('VINAUDIT_API_KEY', '')

    if not vinaudit_key:
        return {
            'value': fallback_value,
            'source': 'DEFAULT',
            'condition': 'Worst-case default',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'none',
            'notes': 'VINAUDIT_API_KEY not set — using default fallback value. Add key in Render environment variables.',
            'used_default': True,
        }

    if not vin or len(vin) < 11:
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
