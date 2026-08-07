#!/usr/bin/env python3
"""Build the double-clickable canary app.

    python build_app.py

Reproducible on purpose. The first build of this was a command typed once into
a terminal, which means the next person -- including me next month -- would have
had to reconstruct the flags from memory, and would have got them wrong.

THE TWO FLAGS THAT ARE NOT OPTIONAL, and why:

  --collect-all flatline
      canary imports flatline inside a function, so PyInstaller's static
      analysis never sees it. Without this the app builds fine and then reports
      every file as "could not be checked" at runtime.

  --hidden-import tomllib
      flatline reads TOML, but lazily. This was found the honest way: the first
      build shipped, ran, and reported ERROR "flatline is not bundled in this
      build" -- which is exactly what should happen, and is why the failure was
      obvious in one run instead of quietly producing empty reports.

Console build rather than --windowed. A windowed build has no stdout, and this
program prints; on some Python builds that raises rather than being ignored,
turning a cosmetic choice into a crash. A console window that flashes costs
less than an app that will not start.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

ARGS = [
    sys.executable, '-m', 'PyInstaller',
    '--onefile',
    '--name', 'canary',
    '--collect-all', 'flatline',
    '--hidden-import', 'tomllib',
    '--noconfirm',
    str(HERE / 'canary_app.py'),
]


def main() -> int:
    try:
        import flatline                                       # noqa: F401
    except ImportError:
        print('build_app: flatline must be installed to bundle it.\n'
              '  pip install ../flatline    (or a checkout of '
              'github.com/automatedworkflowllc-design/flatline)')
        return 2

    rc = subprocess.run(ARGS, cwd=str(HERE)).returncode
    if rc != 0:
        return rc

    exe = HERE / 'dist' / ('canary.exe' if sys.platform == 'win32' else 'canary')
    if not exe.exists():
        print(f'build_app: PyInstaller reported success but {exe.name} is not there')
        return 1

    # Do not ship a build that has not been run. A bundle can compile perfectly
    # and still be missing a module it only imports at runtime -- that is this
    # program's entire subject, and shipping it unrun would be the joke writing
    # itself.
    print(f'\nbuilt {exe} ({exe.stat().st_size // 1024} KB)')
    print('SMOKE TEST IT BEFORE SHIPPING:')
    print(f'  {exe.name} <a folder with a csv in it> --results r.json')
    print('  then confirm r.json says FINDINGS or CLEAN -- not ERROR.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
