"""canary tests. The behaviours that matter are the ones that decide whether an
owner keeps reading the report: unchanged files stay quiet, a failed check is
never reported as clean, and a deleted file stops being reported at all."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import canary                                                  # noqa: E402


def _csv(p, text):
    p.write_text(text.strip() + '\n', encoding='utf-8')
    return p


def test_only_content_changes_count_as_changes(tmp_path):
    """Re-saving a file with identical bytes is not news. Reporting it anyway is
    how a tool teaches its owner to stop looking."""
    d = tmp_path / 'exports'
    d.mkdir()
    f = _csv(d / 'jobs.csv', 'job,status\nJ-1,NEW')
    changes, state = canary.changed_files([d], {})
    assert [p.name for p in changes] == ['jobs.csv']

    # same bytes, fresh mtime -> not a change
    f.write_text(f.read_text(encoding='utf-8'), encoding='utf-8')
    changes2, state2 = canary.changed_files([d], state)
    assert changes2 == []

    # real edit -> change
    _csv(f, 'job,status\nJ-1,NEW\nJ-2,CLOSED')
    changes3, _ = canary.changed_files([d], state2)
    assert [p.name for p in changes3] == ['jobs.csv']


def test_a_check_that_could_not_run_is_never_reported_as_clean(tmp_path, monkeypatch):
    """The whole company exists because software reports success while doing
    nothing. canary must not do it."""
    monkeypatch.setattr(canary, 'flatline_location', lambda: (None, False))
    res = canary.analyze(_csv(tmp_path / 'x.csv', 'a,b\n1,2'))
    assert res['status'] == 'ERROR'


def test_error_rows_are_counted_and_surfaced_in_the_report(tmp_path):
    out = tmp_path / 'r.html'
    canary.render({'a.csv': {'status': 'ERROR', 'detail': 'flatline missing'},
                   'b.csv': {'status': 'CLEAN', 'count': 0, 'flags': []}},
                  out, [tmp_path])
    text = out.read_text(encoding='utf-8')
    assert 'could not be checked' in text
    assert 'flatline missing' in text
    assert 'is not a pass' in text          # the report explains itself


def test_a_deleted_file_stops_being_reported(tmp_path):
    d = tmp_path / 'e'
    d.mkdir()
    f = _csv(d / 'gone.csv', 'a\n1')
    _, state = canary.changed_files([d], {})
    assert str(f) in state
    f.unlink()
    _, state2 = canary.changed_files([d], state)
    assert str(f) not in state2


def test_a_corrupt_state_file_re_examines_everything(tmp_path):
    """Failing open here would mean 'nothing has changed' forever -- a silent
    no-op wearing a green checkmark."""
    s = tmp_path / 'state.json'
    s.write_text('{not json', encoding='utf-8')
    assert canary.load_state(s) == {}


def test_the_exit_code_reports_news_not_inventory(tmp_path):
    """Two rules at once. A nightly guard that always exits 0 has nothing to
    say; a nightly guard that exits 2 forever over a finding its owner already
    saw gets muted, which is the same outcome by a longer road. So: raise the
    alarm when a re-examined file has findings, then go quiet until something
    actually moves."""
    d = tmp_path / 'exports'
    d.mkdir()
    rows = '\n'.join(f'J-{i},OPEN,NORTH' for i in range(1, 13))
    f = _csv(d / 'dead.csv', 'job,status,region\n' + rows)
    args = [str(d), '--state', str(tmp_path / 's.json'), '--fail-on-findings',
            '--report', str(tmp_path / 'r.html'), '--results', str(tmp_path / 'res.json')]

    assert canary.main(args) == 2                       # first look: all news
    assert canary.main(args) == 0                       # same bytes: not news twice
    assert 'FINDINGS' in (tmp_path / 'res.json').read_text(encoding='utf-8')  # still reported

    _csv(f, 'job,status,region\n' + rows + '\nJ-13,OPEN,NORTH')
    assert canary.main(args) == 2                       # it moved and is still bad -> news


def test_a_file_that_could_not_be_checked_is_retried_next_run(tmp_path, monkeypatch):
    """An ERROR is not a result, it is the absence of one. Letting it sit until
    the file happens to change would leave the report asserting a status nobody
    has retested."""
    d = tmp_path / 'exports'
    d.mkdir()
    _csv(d / 'x.csv', 'a,b\n1,2')
    args = [str(d), '--state', str(tmp_path / 's.json'),
            '--report', str(tmp_path / 'r.html'), '--results', str(tmp_path / 'res.json')]
    monkeypatch.setattr(canary, 'flatline_location', lambda: (None, False))
    assert canary.main(args) == 1
    monkeypatch.undo()
    # bytes never changed, but the file gets looked at again and clears
    assert canary.main(args) == 0


def test_an_uncheckable_file_outranks_findings_in_the_exit_code(tmp_path, monkeypatch):
    """A check that could not run hides an unknown number of findings, so it is
    the worse state and must win the exit code."""
    d = tmp_path / 'e'
    d.mkdir()
    _csv(d / 'x.csv', 'a,b\n1,2')
    monkeypatch.setattr(canary, 'flatline_location', lambda: (None, False))
    rc = canary.main([str(d), '--state', str(tmp_path / 's.json'),
                      '--report', str(tmp_path / 'r.html'),
                      '--results', str(tmp_path / 'res.json'), '--fail-on-findings'])
    assert rc == 1


def test_an_installed_flatline_beats_a_sibling_checkout(tmp_path, monkeypatch):
    """The property that decides whether canary can ship at all. Resolving the
    analysis by walking to a sibling directory works in exactly one repo layout
    -- this one -- so an installed flatline has to win, and the sibling path can
    only ever be a fallback for the tree these two were written in."""
    monkeypatch.setattr(canary, 'FLATLINE', tmp_path / 'sibling')
    (tmp_path / 'sibling').mkdir()

    def _pkg(root):
        (root / 'flatline').mkdir(parents=True)
        for m in canary._FLATLINE_MARKERS:
            (root / 'flatline' / m).touch()
        return root

    class _Spec:
        def __init__(self, loc): self.submodule_search_locations = [str(loc)]

    _pkg(tmp_path / 'sibling')
    ours = _Spec(tmp_path / 'installed' / 'flatline')
    _pkg(tmp_path / 'installed')

    monkeypatch.setattr(canary.importlib.util, 'find_spec', lambda n: ours)
    assert canary.flatline_location() == (None, True), 'installed flatline must win'

    monkeypatch.setattr(canary.importlib.util, 'find_spec', lambda n: None)
    assert canary.flatline_location() == (str(tmp_path / 'sibling'), True)

    monkeypatch.setattr(canary, 'FLATLINE', tmp_path / 'gone')
    assert canary.flatline_location() == (None, False)


def test_a_different_package_named_flatline_does_not_count(tmp_path, monkeypatch):
    """The name is taken on PyPI by an unrelated project. "Importable" is not
    "this is the checker canary delegates to", and believing otherwise would
    have canary shell out to a stranger's package and blame the result."""
    imposter = tmp_path / 'ghidra_thing' / 'flatline'
    imposter.mkdir(parents=True)
    (imposter / '__init__.py').touch()          # a real package, wrong one

    class _Spec:
        submodule_search_locations = [str(imposter)]

    monkeypatch.setattr(canary, 'FLATLINE', tmp_path / 'no-sibling')
    monkeypatch.setattr(canary.importlib.util, 'find_spec', lambda n: _Spec())
    assert canary.flatline_location() == (None, False)

    res = canary.analyze(_csv(tmp_path / 'x.csv', 'a,b\n1,2'))
    assert res['status'] == 'ERROR'
    assert 'different package named' in res['detail']


def test_a_finding_is_written_for_the_person_who_owns_the_spreadsheet(tmp_path):
    """"flags: CONSTANT" names a column and a category and leaves the reader to
    work out whether it matters. The same fact told usefully says what the value
    is, how many rows, and what it probably means. Only the second gets acted
    on, and this tool is worth nothing if it is not acted on."""
    res = canary._summarize(
        "  !! status: CONSTANT\n"
        "       identical value 'OPEN' on all 412 rows; carries 0.00 bits\n", 1)
    assert res['plain'][0]['column'] == 'status'
    assert "identical value 'OPEN' on all 412 rows" in res['plain'][0]['detail']

    out = tmp_path / 'r.html'
    canary.render({'invoices.csv': res}, out, [tmp_path])
    text = out.read_text(encoding='utf-8')
    assert 'is <code>OPEN</code> on all 412 rows' in text
    assert 'whatever fills it in has stopped' in text


def test_worst_first_and_no_badge_when_it_would_sit_on_every_row(tmp_path):
    """Two ordering rules. A reader who scrolls past ten rows of "nothing to
    report" stops opening the report; and a badge on all 32 rows is furniture,
    which is the always-on-alarm failure committed in the UI instead of the
    exit code."""
    results = {
        'clean.csv': {'status': 'CLEAN', 'count': 0, 'flags': []},
        'bad.csv': {'status': 'FINDINGS', 'count': 1, 'flags': ['x: CONSTANT']},
        'broken.csv': {'status': 'ERROR', 'detail': 'locked'},
    }
    out = tmp_path / 'r.html'
    canary.render(results, out, [tmp_path], checked=list(results))
    text = out.read_text(encoding='utf-8')
    assert text.index('broken.csv') < text.index('bad.csv') < text.index('clean.csv')
    assert 'new since last look' not in text, 'a badge on every row says nothing'

    canary.render(results, out, [tmp_path], checked=['bad.csv'])
    assert 'new since last look' in out.read_text(encoding='utf-8')


def test_report_renders_findings_visibly(tmp_path):
    out = tmp_path / 'r.html'
    canary.render({'jobs.csv': {'status': 'FINDINGS', 'count': 2,
                                'flags': ['!! status: constant value', '!! notes: empty']}},
                  out, [tmp_path])
    text = out.read_text(encoding='utf-8')
    assert 'worth a look' in text
    assert 'constant value' in text
