#!/usr/bin/env python3
"""canary -- the always-on version of the free tools.

Named by Colin: the canary in the coal mine. The point of the bird was never
that it sang -- it was that it stopped, early, while there was still time to
walk out. This watches the files a business already saves and speaks up before
anyone thought to ask.

Build 2 of UNBELIEVABLE-PLAN-2026-08.md.

THE QUESTION IT ANSWERS: what is wrong in the files this business just saved?
THE BLIND SPOT IT HAS: it only sees files that change on disk. It knows nothing
about whether a scheduled job ran (attest), or whether an output went stale
between runs (watchpost). Stated per the rule in RELIABILITY-MAP.md -- a new
tool has to name its question and its blind spot in one line each, or it is a
feature of something that already exists.

WHY IT EXISTS. Every tool we ship waits for someone to bring it a file. That is
backwards: the owner has to already suspect a problem, and the people who most
need the check are the ones who never think to run it. This inverts the trigger.
Point it at the folder where exports already get saved, and the answer arrives
before the question -- you saved an invoice export at 2:14pm, and at 2:15 you
know what is wrong with it.

IT DOES NOT REIMPLEMENT THE ANALYSIS. It calls flatline, which we already own
and already test. A second copy of that logic would drift from the first within
a month, and drift between two checkers is worse than one checker -- see
_qa/fleet.py for the last time two copies of a definition disagreed.

Local only. Nothing is uploaded; there is no endpoint to upload to.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import pathlib
import subprocess
import sys

WATCHED_SUFFIXES = {'.csv', '.tsv'}
FLATLINE = pathlib.Path(__file__).resolve().parents[1] / 'flatline'


def _sha256(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def load_state(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        # A corrupt state file must not silently mean "nothing changed" -- that
        # would make the canary report all-clear forever. Treat it as first run,
        # which re-examines everything.
        return {}


def changed_files(roots, state: dict):
    """Files whose CONTENT changed since last look.

    Hash, not mtime: a file re-saved with identical bytes has not changed in any
    way a business cares about, and re-reporting it every night is how a tool
    trains its owner to ignore it.
    """
    found, changes = {}, []
    for root in roots:
        r = pathlib.Path(root)
        if not r.exists():
            continue
        for p in sorted(r.rglob('*')):
            if not p.is_file() or p.suffix.lower() not in WATCHED_SUFFIXES:
                continue
            key = str(p)
            digest = _sha256(p)
            found[key] = {'sha256': digest, 'size': p.stat().st_size,
                          'seen': dt.datetime.now().isoformat(timespec='seconds')}
            if state.get(key, {}).get('sha256') != digest:
                changes.append(p)
    return changes, found


def analyze(path: pathlib.Path) -> dict:
    """Run flatline's scan over one file and return a small summary.

    flatline is the analysis; this is only the adapter. If flatline cannot run,
    that is reported as ERROR rather than as a clean file -- a checker that says
    "no findings" when it never checked is the exact failure this company exists
    to catch.
    """
    try:
        r = subprocess.run([sys.executable, '-m', 'flatline', 'scan', str(path)],
                           cwd=str(FLATLINE), capture_output=True, text=True, timeout=120)
    except Exception as e:                                    # noqa: BLE001
        return {'status': 'ERROR', 'detail': f'could not run flatline: {e}'}
    out = (r.stdout or '') + (r.stderr or '')
    if r.returncode not in (0, 1):
        return {'status': 'ERROR', 'detail': out.strip()[:300] or f'exit {r.returncode}'}
    flags = [ln.strip() for ln in out.splitlines()
             if ln.strip().startswith('!!') or 'ZERO_VARIANCE' in ln
             or 'constant' in ln.lower() or 'no information' in ln.lower()]
    return {'status': 'FINDINGS' if flags else 'CLEAN',
            'count': len(flags), 'flags': flags[:12]}


def render(results, out_path: pathlib.Path, roots, checked=()):
    now = dt.datetime.now().isoformat(timespec='seconds')
    checked = set(checked)
    rows = []
    for path, res in sorted(results.items()):
        cls = {'FINDINGS': 'bad', 'ERROR': 'err', 'CLEAN': 'ok'}[res['status']]
        detail = html.escape(res.get('detail', '')) or '<br>'.join(
            html.escape(f) for f in res.get('flags', [])) or 'nothing to report'
        new = '<span class="new">new since last look</span>' if path in checked else ''
        rows.append(
            f'<tr class="{cls}"><td>{html.escape(pathlib.Path(path).name)}{new}</td>'
            f'<td>{res["status"]}</td><td>{detail}</td></tr>')
    findings = sum(1 for r in results.values() if r['status'] == 'FINDINGS')
    errors = sum(1 for r in results.values() if r['status'] == 'ERROR')
    watching = ', '.join(html.escape(str(r)) for r in roots)
    body = ''.join(rows) or '<tr><td colspan=3>No watched files have changed yet.</td></tr>'
    out_path.write_text(f"""<!doctype html><meta charset=utf8>
<title>What is in your files right now</title>
<style>
body{{font:15px/1.55 ui-sans-serif,system-ui,sans-serif;background:#FBFAF3;color:#211D14;
margin:0;padding:2.2rem 1.3rem;max-width:60rem;margin-inline:auto}}
h1{{font-size:1.5rem;margin:0 0 .3rem}}
.sub{{color:#5C5645;margin:0 0 1.4rem;font-size:.92rem}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}}
td{{border-top:1px solid #E4DFD1;padding:.55rem .6rem;vertical-align:top}}
tr.bad td:nth-child(2){{color:#B4452C;font-weight:600}}
tr.err td:nth-child(2){{color:#8A6A16;font-weight:600}}
tr.ok td:nth-child(2){{color:#1E7A47}}
.new{{display:inline-block;margin-left:.5rem;padding:.05rem .38rem;border-radius:.28rem;
background:#EFE6C8;color:#6B5410;font-size:.72rem;letter-spacing:.02em;vertical-align:.06em}}
.note{{margin-top:1.6rem;padding:.9rem 1.1rem;border:1px solid #E4DFD1;border-radius:.6rem;
background:#F4F1E8;color:#5C5645;font-size:.88rem}}</style>
<h1>What is in your files right now</h1>
<p class="sub">{len(results)} file(s) checked &middot; <b>{findings}</b> with findings &middot;
{errors} could not be checked &middot; last look {now}</p>
<table><tr><td><b>file</b></td><td><b>status</b></td><td><b>what</b></td></tr>
{body}</table>
<div class="note"><b>How to read this.</b> Only files whose contents actually changed are
re-checked &mdash; a file re-saved with identical bytes is not news. A row marked
<b>could not be checked</b> is not a pass: it means the check failed to run, and that is
reported rather than counted as clean. Watching: {watching}. Nothing leaves this machine.</div>
""", encoding='utf-8')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='canary', description=__doc__.splitlines()[0])
    ap.add_argument('roots', nargs='+', help='folders where exports get saved')
    ap.add_argument('--state', default='canary-state.json')
    ap.add_argument('--report', default='canary-report.html')
    ap.add_argument('--results', default='canary-results.json')
    ap.add_argument('--fail-on-findings', dest='fail_on_findings', action='store_true',
                    help='exit 2 when any watched file has findings. For scheduled runs: a guard '
                         'nobody hears is not a guard, and a nightly job that always exits 0 '
                         'teaches its scheduler it never has anything to say')
    a = ap.parse_args(argv)

    state_path = pathlib.Path(a.state)
    state = load_state(state_path)
    changes, found = changed_files(a.roots, state)

    rp = pathlib.Path(a.results)
    prev = {}
    if rp.exists():
        try:
            prev = json.loads(rp.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            prev = {}

    # A file whose last check ERRORED is re-examined even if its bytes did not
    # move. "We never managed to look at this one" is not a state that resolves
    # itself by being ignored, and leaving the stale ERROR in place would let
    # the report keep asserting a status nobody has re-tested in weeks.
    retries = [k for k, v in prev.items()
               if v.get('status') == 'ERROR' and k in found]
    to_check = list(dict.fromkeys([str(p) for p in changes] + retries))

    for key in to_check:
        prev[key] = analyze(pathlib.Path(key))
    # a file that vanished should stop being reported as if it were still there
    prev = {k: v for k, v in prev.items() if k in found}
    checked = [k for k in to_check if k in prev]

    render(prev, pathlib.Path(a.report), a.roots, checked)
    rp.write_text(json.dumps(prev, indent=1), encoding='utf-8')
    state_path.write_text(json.dumps(found, indent=1), encoding='utf-8')

    findings = sum(1 for r in prev.values() if r['status'] == 'FINDINGS')
    errors = sum(1 for r in prev.values() if r['status'] == 'ERROR')
    fresh = sum(1 for k in checked if prev[k]['status'] == 'FINDINGS')
    print(f'canary: {len(checked)} checked, {len(prev)} tracked, '
          f'{findings} with findings ({fresh} new), {errors} uncheckable -> {a.report}')
    # 1 beats 2: a check that could not RUN is a worse state than a check that
    # ran and found something, because the first hides an unknown number of the
    # second.
    if errors:
        return 1
    # NEWS, not inventory. The exit code fires on findings in files that were
    # actually re-examined this run. A known-bad file sitting unchanged is
    # already in the report; raising the alarm about it again every night is how
    # a nightly guard teaches its owner to stop reading -- the same muting
    # failure the claim-audit wrapper avoids from the opposite direction.
    return 2 if (fresh and a.fail_on_findings) else 0


if __name__ == '__main__':
    sys.exit(main())
