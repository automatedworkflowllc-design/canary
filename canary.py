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
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

WATCHED_SUFFIXES = {'.csv', '.tsv'}

# A SIBLING checkout of flatline, if one happens to sit beside this file. That is
# true inside the repo these two were written in and false everywhere else, which
# is why it is a fallback rather than the mechanism: a tool that only runs from
# one directory layout is not a tool anyone else can use. Overridable with
# --flatline; None means "flatline is installed, just run it".
FLATLINE = pathlib.Path(__file__).resolve().parents[1] / 'flatline'


# Files that identify OUR flatline. The name is taken on PyPI by an unrelated
# project (a Ghidra decompiler wrapper), so "a module named flatline is
# importable" is not the same claim as "the checker canary delegates to is here".
# Getting that wrong would have canary shell out to a stranger's package and
# report the confusion as a failed check.
_FLATLINE_MARKERS = ('signals.py', 'jobs.py', 'code.py')


def _is_our_flatline(spec) -> bool:
    """Identify the package by its files, without importing it.

    Deliberately filesystem inspection rather than an import: verifying identity
    must not execute third-party code that merely happens to share a name.
    """
    for loc in list(getattr(spec, 'submodule_search_locations', None) or []):
        if all((pathlib.Path(loc) / m).exists() for m in _FLATLINE_MARKERS):
            return True
    return False


def flatline_location():
    """Return (cwd_to_run_from_or_None, found: bool).

    Preference order, where the reasoning matters more than the order: an
    INSTALLED flatline wins, because that is the only arrangement that works on
    a machine other than the one these two were written on. A sibling checkout
    is consulted only if the import fails, so the dev tree keeps working without
    letting the tool pretend it is releasable.

    Availability is returned rather than discovered inside the subprocess so
    that "flatline is not here" is a state canary can state plainly -- and so a
    test can force it without having to uninstall anything.
    """
    try:
        spec = importlib.util.find_spec('flatline')
    except (ImportError, ValueError):
        spec = None
    if spec is not None and _is_our_flatline(spec):
        return None, True
    if FLATLINE.is_dir() and all((FLATLINE / 'flatline' / m).exists() for m in _FLATLINE_MARKERS):
        return str(FLATLINE), True
    return None, False


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


def _analyze_frozen(path: pathlib.Path) -> dict:
    """Run flatline inside this process, for the bundled single-file app.

    A frozen build has no Python interpreter to spawn: sys.executable IS this
    application, so the normal `python -m flatline` subprocess would relaunch
    canary rather than run the checker.

    So it calls flatline's OWN cli.main and captures what that prints -- the
    same code path, producing the same text this module already parses. The
    alternative was reimplementing the analysis for the packaged build, which
    would put two checkers in the product and guarantee they drift; that is the
    single thing canary was built not to do.
    """
    import contextlib
    import io
    try:
        from flatline import cli
    except Exception as e:                                    # noqa: BLE001
        return {'status': 'ERROR', 'detail': f'flatline is not bundled in this build: {e}'}
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = cli.main(['scan', str(path)])
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    except Exception as e:                                    # noqa: BLE001
        return {'status': 'ERROR', 'detail': f'flatline failed on this file: {e}'}
    return _summarize(buf.getvalue(), rc)


def _summarize(out: str, returncode: int) -> dict:
    """Turn flatline's printed output into canary's small summary.

    Shared by both paths on purpose. If the packaged build and the installed
    build read the same output differently, the product and the tool stop
    agreeing about the same file, which is the drift this design exists to
    prevent.
    """
    if returncode not in (0, 1):
        return {'status': 'ERROR', 'detail': out.strip()[:300] or f'exit {returncode}'}

    lines = out.splitlines()
    flags, plain = [], []
    for i, raw in enumerate(lines):
        ln = raw.strip()
        if not (ln.startswith('!!') or 'ZERO_VARIANCE' in ln
                or 'constant' in ln.lower() or 'no information' in ln.lower()):
            continue
        flags.append(ln)
        # flatline prints the finding, then explains it on the next line. canary
        # was keeping the first and discarding the second -- so the report said
        # "flags: CONSTANT" and dropped "identical value 'OPEN' on all 412 rows",
        # which is the half a non-engineer can actually act on.
        why = lines[i + 1].strip() if i + 1 < len(lines) else ''
        if why.startswith('!!') or ':' in why.split(' ')[0]:
            why = ''
        plain.append({'column': _column_of(ln), 'detail': why})
    return {'status': 'FINDINGS' if flags else 'CLEAN',
            'count': len(flags), 'flags': flags[:12], 'plain': plain[:12]}


def _load_ignore(path: pathlib.Path) -> list:
    """Columns the owner has said are supposed to be constant.

    Without this the tool is correct and unusable. On a real folder, two
    columns -- a ruleset version and a regime code -- produced two thirds of
    every finding, and both are constant BY DESIGN. Nothing in the data can
    tell canary that; only the person who owns the spreadsheet knows. So they
    say it once, in a plain text file they can read and edit.

    Deliberately not a silent filter. Hidden findings are counted and named in
    the report, because a checker that quietly stops mentioning things is the
    exact failure this tool is pointed at.
    """
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        name = line.split('#', 1)[0].strip()
        if name:
            out.append(name)
    return out


def _apply_ignore(results: dict, ignore) -> None:
    """Move findings on expected-constant columns aside, in place.

    A file whose every finding is ignored reads as clean but carries the count,
    so the report can say what was set aside rather than pretend it never
    existed.
    """
    names = {n.lower() for n in ignore}
    if not names:
        return
    for res in results.values():
        if res.get('status') != 'FINDINGS':
            continue
        plain = res.get('plain') or []
        keep = [p for p in plain if (p.get('column') or '').lower() not in names]
        hide = [p['column'] for p in plain if (p.get('column') or '').lower() in names]
        if not hide:
            continue
        res['plain'] = keep
        res['flags'] = [f for f in (res.get('flags') or [])
                        if _column_of(f).lower() not in names]
        res['hidden'] = hide
        res['count'] = len(keep)
        if not keep:
            res['status'] = 'CLEAN'


def _column_of(flag_line: str) -> str:
    """The column name out of a flatline finding line, without its severity mark."""
    name = flag_line.lstrip('!').strip()
    return name.split(':')[0].strip() if ':' in name else name


def analyze(path: pathlib.Path) -> dict:
    """Run flatline's scan over one file and return a small summary.

    flatline is the analysis; this is only the adapter. If flatline cannot run,
    that is reported as ERROR rather than as a clean file -- a checker that says
    "no findings" when it never checked is the exact failure this company exists
    to catch.
    """
    if getattr(sys, 'frozen', False):
        return _analyze_frozen(path)

    cwd, found = flatline_location()
    if not found:
        # Name the actual remedy instead of echoing a traceback. This is the one
        # failure a new user will hit, and "could not be checked" with no cause
        # is the sort of dead end that gets a tool deleted rather than fixed.
        clash = importlib.util.find_spec('flatline') is not None
        return {'status': 'ERROR',
                'detail': ('a different package named "flatline" is installed -- the name is '
                           'taken on PyPI by an unrelated project. canary needs THIS flatline: '
                           'pass --flatline pointing at a checkout of it.') if clash else
                          ('flatline was not found. canary does not do the analysis itself -- '
                           'install flatline, or pass --flatline pointing at a checkout of it.')}
    try:
        r = subprocess.run([sys.executable, '-m', 'flatline', 'scan', str(path)],
                           cwd=cwd, capture_output=True, text=True, timeout=120)
    except Exception as e:                                    # noqa: BLE001
        return {'status': 'ERROR', 'detail': f'could not run flatline: {e}'}
    return _summarize((r.stdout or '') + (r.stderr or ''), r.returncode)


_VALUE = re.compile(r"identical value '([^']*)' on all (\d+) rows")


def _plain_detail(res: dict) -> str:
    """A finding written for the person who owns the spreadsheet.

    "flags: CONSTANT" is engineer output: it names a column and a category and
    leaves the reader to work out whether that matters. The same fact told
    usefully is "every one of 412 rows says OPEN -- if that is meant to change,
    whatever fills it in has stopped." Same finding, and only the second one
    gets acted on.
    """
    items = res.get('plain')
    if not items:
        flags = res.get('flags') or []
        return '<br>'.join(html.escape(f) for f in flags) or 'nothing to report'

    out = []
    for it in items:
        col = html.escape(it.get('column') or 'a column')
        m = _VALUE.search(it.get('detail') or '')
        if m:
            value, rows = html.escape(m.group(1)) or '(blank)', m.group(2)
            out.append(f'<b>{col}</b> is <code>{value}</code> on all {rows} rows &mdash; '
                       f'so it cannot tell you anything. If it is meant to vary, whatever '
                       f'fills it in has stopped.')
        else:
            out.append(f'<b>{col}</b> carries no information &mdash; every row is the same.')
    return '<br>'.join(out)


def render(results, out_path: pathlib.Path, roots, checked=(), ignore_path=None):
    now = dt.datetime.now().isoformat(timespec='seconds')
    checked = set(checked)

    # A badge on every row distinguishes nothing. On a first run everything is
    # new, so "new since last look" sits on all 32 rows and becomes furniture --
    # the same failure as an exit code that always says PROBLEM, committed in
    # the report instead. It earns its place only when it splits the list.
    badge_useful = 0 < len(checked & set(results)) < len(results)

    # Worst first, in the order the exit code already ranks them: a file that
    # could not be checked outranks a finding, because it hides an unknown
    # number of them. A reader who scrolls past ten rows of "nothing to report"
    # to reach the problem is a reader who stops opening the report.
    severity = {'ERROR': 0, 'FINDINGS': 1, 'CLEAN': 2}
    ordered = sorted(results.items(), key=lambda kv: (severity[kv[1]['status']], kv[0]))

    label = {'FINDINGS': 'WORTH A LOOK', 'ERROR': 'COULD NOT CHECK', 'CLEAN': 'nothing to report'}
    rows = []
    for path, res in ordered:
        cls = {'FINDINGS': 'bad', 'ERROR': 'err', 'CLEAN': 'ok'}[res['status']]
        detail = html.escape(res.get('detail', '')) or _plain_detail(res)
        if res.get('hidden'):
            cols = ', '.join(html.escape(c) for c in sorted(set(res['hidden'])))
            detail += (f'<br><span class="hid">{len(res["hidden"])} finding(s) hidden as '
                       f'expected-constant: {cols}</span>')
        new = ('<span class="new">new since last look</span>'
               if badge_useful and path in checked else '')
        rows.append(
            f'<tr class="{cls}"><td>{html.escape(pathlib.Path(path).name)}{new}</td>'
            f'<td>{label[res["status"]]}</td><td>{detail}</td></tr>')
    findings = sum(1 for r in results.values() if r['status'] == 'FINDINGS')
    errors = sum(1 for r in results.values() if r['status'] == 'ERROR')
    watching = ', '.join(html.escape(str(r)) for r in roots)

    hidden_total = sum(len(r.get('hidden') or []) for r in results.values())
    where = html.escape(str(ignore_path)) if ignore_path else 'canary-ignore.txt'
    hidden_note = (
        f'<b>{hidden_total} finding(s) are already being set aside</b> for you; add or remove '
        f'column names in <code>{where}</code>, one per line.'
        if hidden_total else
        f'To silence one, put its column name in <code>{where}</code>, one per line.')

    # The one sentence someone reads. It leads with the uncheckable files when
    # there are any, because a file nobody managed to read is the finding that
    # hides the others -- and it never says "all clear" over an empty check.
    if errors:
        headline = (f'<b>{errors} file(s) could not be checked at all.</b> That is not a pass. '
                    f'{findings} other file(s) have something worth a look.')
    elif findings:
        headline = f'<b>{findings} of {len(results)} file(s) have something worth a look.</b>'
    elif results:
        headline = 'Nothing to report in any of these files, as they stand right now.'
    else:
        headline = ('No spreadsheets found to check &mdash; which is not the same as '
                    'nothing being wrong.')
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
.hid{{color:#8A6A16;font-size:.85em}}
.new{{display:inline-block;margin-left:.5rem;padding:.05rem .38rem;border-radius:.28rem;
background:#EFE6C8;color:#6B5410;font-size:.72rem;letter-spacing:.02em;vertical-align:.06em}}
.note{{margin-top:1.6rem;padding:.9rem 1.1rem;border:1px solid #E4DFD1;border-radius:.6rem;
background:#F4F1E8;color:#5C5645;font-size:.88rem}}</style>
<h1>What is in your files right now</h1>
<p class="sub">{headline}</p>
<p class="sub" style="font-size:.86rem">{len(results)} file(s) checked &middot; last look {now}</p>
<table><tr><td><b>file</b></td><td><b>status</b></td><td><b>what we found</b></td></tr>
{body}</table>
<div class="note"><b>What &ldquo;worth a look&rdquo; means.</b> A column where every row holds the
same value cannot tell you anything &mdash; and that is normal for some columns and a broken
process for others. Only you know which. The usual cause worth catching: something upstream
stopped writing that field, and every report built on it has been quietly wrong since.
<br><br><b>A row marked &ldquo;could not check&rdquo; is not a pass.</b> It means the check
never ran, so whatever is wrong in that file is still there and unseen.
<br><br><b>Some of these are supposed to be constant.</b> A version number, a region code, a
status that only ever means one thing for you. {hidden_note} canary cannot know which columns
those are &mdash; only you can &mdash; so tell it once and it will set them aside, while still
counting and naming them here rather than pretending they were never found.
<br><br>Only files whose contents actually changed get re-checked, so this stays quiet until
something moves. Watching: {watching}. Nothing leaves this machine &mdash; there is no
account and nowhere to upload to.</div>
""", encoding='utf-8')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='canary', description=__doc__.splitlines()[0])
    ap.add_argument('roots', nargs='+', help='folders where exports get saved')
    ap.add_argument('--state', default='canary-state.json')
    ap.add_argument('--report', default='canary-report.html')
    ap.add_argument('--results', default='canary-results.json')
    ap.add_argument('--flatline', metavar='DIR',
                    help='path to a flatline checkout, if it is not installed. canary does not '
                         'do the analysis itself -- it calls flatline, so that there is one '
                         'checker rather than two that drift apart')
    ap.add_argument('--ignore', action='append', default=[], metavar='COLUMN',
                    help='a column that is SUPPOSED to be the same on every row (repeatable, or '
                         'comma-separated). Also read from canary-ignore.txt beside the state '
                         'file. Hidden findings are still counted and named in the report')
    ap.add_argument('--fail-on-findings', dest='fail_on_findings', action='store_true',
                    help='exit 2 when any watched file has findings. For scheduled runs: a guard '
                         'nobody hears is not a guard, and a nightly job that always exits 0 '
                         'teaches its scheduler it never has anything to say')
    a = ap.parse_args(argv)
    if a.flatline:
        global FLATLINE
        FLATLINE = pathlib.Path(a.flatline).resolve()

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

    ignore_path = state_path.parent / 'canary-ignore.txt'
    ignore = _load_ignore(ignore_path)
    for chunk in a.ignore:
        ignore.extend(n.strip() for n in chunk.split(',') if n.strip())
    _apply_ignore(prev, ignore)

    render(prev, pathlib.Path(a.report), a.roots, checked, ignore_path)
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
