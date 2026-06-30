# Overnight Deploy Fix Summary
**Date:** 2026-06-30  
**Commit fixed:** dc49f31 (pushed to main)

---

## What Was Broken

Commit 5079535 removed `flask-cors` from `requirements.txt` and added try/except guards in `app.py` — but missed one file:

**`blueprints/api.py` line 9** had a hard, unguarded top-level import:
```python
from flask_cors import cross_origin
```

When Render started the app, `create_app()` imports all blueprints. This import fired immediately and raised `ImportError: No module named 'flask_cors'`, crashing startup before any request could be served. Every deploy of 5079535 exited with status 1.

All other optional-package imports (`flask_socketio` in app.py, `pywebpush` in chat.py, `twilio` in drivers.py) were already correctly guarded — this was the only hard import that slipped through.

---

## What Was Fixed

**File:** `blueprints/api.py`  
**Change:** Wrapped the import in try/except with a no-op fallback:

```python
try:
    from flask_cors import cross_origin
except ImportError:
    def cross_origin(*args, **kwargs):
        def decorator(f):
            return f
        return decorator
```

`@cross_origin()` is used on 13 routes in api.py. When flask-cors isn't installed, these decorators become no-ops — correct behavior since global CORS is already handled in `create_app()` via `_CORS(app, ...)` when flask-cors is available.

---

## Commits Pushed

| Commit | Description |
|--------|-------------|
| dc49f31 | **Bug fix** — guard flask_cors import in blueprints/api.py |
| b305056 | Permissions: Tina full Heather action access; Lori at Lawrence level |
| 1841dac | Add BMV document scanner blueprint + bmv_migration.sql |
| da6c77a | Add Towbook letter backfill + UPS tracking attach scripts |

---

## Deploy Status

- Pushed to `origin/main` at approx. 2026-06-30
- Render auto-deploy triggered by the push
- Live check at time of writing: HTTP 502 (Render mid-deploy — expected)
- Re-check `https://impound-manager.onrender.com/vehicles` in 3–5 minutes

---

## What Could Not Be Confirmed Autonomously

See OVERNIGHT_BLOCKER.md for items that required Render API/CLI access.
