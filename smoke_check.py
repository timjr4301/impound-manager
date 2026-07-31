"""
WP-5: one command that answers "did my push actually deploy?" — the
question D9 (a deploy silently not landing, costing a night of confusion)
came down to. Hits a running Impound Manager instance and checks:

  1. `/` responds (200 logged in, or 302 to login — either means the app
     is actually serving, not crashed/down).
  2. `/vehicles` responds the same way.
  3. `/version` reports a commit SHA and deploy time (proves the /version
     endpoint itself, from WP-5, is live).
  4. If you pass an expected commit SHA, compares it against what's live —
     PASS only if they match. Without one, just reports what's live so you
     can eyeball it against `git log -1`.

Exit code 0 on full PASS, 1 on any FAIL — safe to chain in a script.

    python3 smoke_check.py https://impound-manager.onrender.com
    python3 smoke_check.py https://impound-manager.onrender.com $(git rev-parse HEAD)
    python3 smoke_check.py https://impound-manager-staging.onrender.com
"""
import sys

import requests

TIMEOUT = 15


def check_page(url, path):
    full = url.rstrip('/') + path
    try:
        resp = requests.get(full, timeout=TIMEOUT, allow_redirects=False)
    except requests.RequestException as exc:
        return False, f'{path}: request failed — {exc}'
    if resp.status_code in (200, 302):
        return True, f'{path}: {resp.status_code}'
    return False, f'{path}: unexpected status {resp.status_code}'


def check_version(url, expected_sha):
    full = url.rstrip('/') + '/version'
    try:
        resp = requests.get(full, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, f'/version: request failed — {exc}'
    if resp.status_code != 200:
        return False, f'/version: unexpected status {resp.status_code}'
    try:
        data = resp.json()
    except ValueError:
        return False, '/version: response was not JSON'

    commit = data.get('commit', 'unknown')
    deployed_at = data.get('deployed_at', 'unknown')
    line = f'/version: commit={commit} deployed_at={deployed_at} staging={data.get("is_staging")}'

    if expected_sha:
        if commit == 'unknown':
            return False, line + ' — RENDER_GIT_COMMIT not set, cannot compare'
        if commit.startswith(expected_sha) or expected_sha.startswith(commit):
            return True, line + f' — matches expected {expected_sha[:7]}'
        return False, line + f' — does NOT match expected {expected_sha[:7]}'

    return True, line


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 smoke_check.py <url> [expected_commit_sha]')
        sys.exit(1)

    url = sys.argv[1]
    expected_sha = sys.argv[2] if len(sys.argv) > 2 else None

    checks = [
        check_page(url, '/'),
        check_page(url, '/vehicles'),
        check_version(url, expected_sha),
    ]

    all_ok = True
    for ok, line in checks:
        print(('PASS  ' if ok else 'FAIL  ') + line)
        all_ok = all_ok and ok

    print()
    print('ALL CHECKS PASSED' if all_ok else 'ONE OR MORE CHECKS FAILED')
    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
