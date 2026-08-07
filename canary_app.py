#!/usr/bin/env python3
"""canary, as something you double-click.

WHY THIS EXISTS. The command-line tool asks the owner to install Python, run
two pip commands, and then type a path. Our own /flatline/ page says the people
who need this "do not read READMEs and would not recognize the problem by
name" -- and then we handed them pip. This is the version for the person who
actually saves the export.

WHAT IT DOES NOT CHANGE. It is the same canary and the same flatline; this file
adds a folder picker and opens the report. No analysis lives here. If the app
and the tool ever disagreed about a file, the product would be lying about the
tool.

THE PROMISE IT KEEPS. Still local. There is no endpoint to upload to, no
account, and no network call -- which is precisely why this is a desktop app
and not a web service. A hosted version would be easier to ship and would break
the only thing that makes it trustworthy.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import webbrowser

import canary


def _state_dir() -> pathlib.Path:
    """Somewhere writable that is not next to the .exe.

    People run downloads from Downloads, from a USB stick, from Program Files.
    Writing beside the executable fails on the last one and litters on the
    others, and a tool whose first act is an unexplained permission error does
    not get a second run.
    """
    base = (os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_STATE_HOME')
            or os.path.expanduser('~'))
    d = pathlib.Path(base) / 'canary'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_prefs(d: pathlib.Path) -> dict:
    try:
        return json.loads((d / 'app.json').read_text(encoding='utf-8'))
    except Exception:                                         # noqa: BLE001
        return {}


def _save_prefs(d: pathlib.Path, prefs: dict) -> None:
    try:
        (d / 'app.json').write_text(json.dumps(prefs, indent=1), encoding='utf-8')
    except OSError:
        pass          # a lost preference is not worth failing a check over


def _pick_folder(initial):
    """Ask for the folder. Returns '' if the person cancels."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.update()
    try:
        return filedialog.askdirectory(
            title='Choose the folder where your exports get saved',
            initialdir=initial or os.path.expanduser('~'))
    finally:
        root.destroy()


def _say(title: str, message: str, kind: str = 'info') -> None:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    root.update()
    try:
        (messagebox.showwarning if kind == 'warn' else messagebox.showinfo)(title, message)
    finally:
        root.destroy()


def _summary_text(results: dict):
    """The sentence someone reads instead of the report. Two rules.

    An uncheckable file is named FIRST and never folded into a count of things
    that were fine -- the whole product is that a check which did not run is not
    a pass. And a clean result says what was actually examined, because "all
    good" over an empty examination is the lie this exists to catch.
    """
    findings = [k for k, v in results.items() if v['status'] == 'FINDINGS']
    errors = [k for k, v in results.items() if v['status'] == 'ERROR']
    n = len(results)
    if errors:
        return ('warn',
                f'{len(errors)} of {n} file(s) COULD NOT BE CHECKED.\n\n'
                'That is not a pass -- it means the check did not run, so anything wrong in '
                'those files is still there and unseen.\n\n'
                f'{len(findings)} other file(s) have findings. The report lists all of it.')
    if findings:
        return ('warn', f'{len(findings)} of {n} file(s) have findings.\n\n'
                        'The report explains each one in plain words.')
    return ('info', f'{n} file(s) checked, nothing to report.\n\n'
                    'That covers the spreadsheets in that folder as they are right now.')


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return canary.main(argv)          # anyone passing arguments wants the CLI

    d = _state_dir()
    prefs = _load_prefs(d)
    try:
        folder = _pick_folder(prefs.get('last_folder'))
    except Exception as e:                                    # noqa: BLE001
        print(f'canary: no window system available ({e}). Pass a folder as an argument instead.')
        return 2
    if not folder:
        return 0                          # cancelled; say nothing, do nothing

    prefs['last_folder'] = folder
    _save_prefs(d, prefs)

    report = d / 'canary-report.html'
    rc = canary.main([folder,
                      '--state', str(d / 'canary-state.json'),
                      '--results', str(d / 'canary-results.json'),
                      '--report', str(report)])

    try:
        results = json.loads((d / 'canary-results.json').read_text(encoding='utf-8'))
    except Exception:                                         # noqa: BLE001
        results = {}

    if not results:
        _say('canary', 'No spreadsheets found in that folder.\n\n'
                       'canary looks at .csv and .tsv files. Nothing was checked, which is not '
                       'the same as nothing being wrong.', 'warn')
        return rc

    if report.exists():
        webbrowser.open(report.as_uri())
    kind, text = _summary_text(results)
    _say('canary', text, kind)
    return rc


if __name__ == '__main__':
    sys.exit(main())
