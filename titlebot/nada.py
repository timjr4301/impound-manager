"""
Thin wrapper that calls nada_lookup.py from the TitleBot installation.
Falls back gracefully if Playwright or Anthropic are not available.
"""

import os
import sys

TITLEBOT_DIR = r'C:\TitleBot'
DEFAULT_VALUE = 3499


def lookup_wholesale_value(vin, mileage=80000, zip_code='43219', api_key=None, fallback_value=DEFAULT_VALUE):
    """
    Look up Edmunds wholesale trade-in value for a VIN.
    Requires playwright, anthropic, and C:\\TitleBot\\nada_lookup.py to be present.
    Returns a dict with keys: value, source, condition, screenshot_pdf, url, confidence, notes, used_default.
    """
    if TITLEBOT_DIR not in sys.path:
        sys.path.insert(0, TITLEBOT_DIR)
    try:
        from nada_lookup import lookup_wholesale_value as _lookup
        return _lookup(
            vin=vin,
            mileage=int(mileage) if mileage else 80000,
            zip_code=zip_code or '43219',
            api_key=api_key or os.environ.get('ANTHROPIC_API_KEY'),
            fallback_value=fallback_value,
        )
    except ImportError as exc:
        return {
            'value': fallback_value,
            'source': 'NOT_AVAILABLE',
            'condition': 'NADA lookup unavailable',
            'screenshot_pdf': None,
            'url': '',
            'confidence': 'none',
            'notes': f'Cannot import nada_lookup: {exc}. Install playwright + anthropic and ensure C:\\TitleBot exists.',
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
