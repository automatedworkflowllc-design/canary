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
    monkeypatch.setattr(canary, 'FLATLINE', tmp_path / 'nowhere')
    res = canary.analyze(_csv(tmp_path / 'x.csv', 'a,b\n1,2'))
    assert res['status'] == 'ERROR'


def test_error_rows_are_counted_and_surfaced_in_the_report(tmp_path):
    out = tmp_path / 'r.html'
    canary.render({'a.csv': {'status': 'ERROR', 'detail': 'flatline missing'},
                   'b.csv': {'status': 'CLEAN', 'count': 0, 'flags': []}},
                  out, [tmp_path])
    text = out.read_text(encoding='utf-8')
    assert '1 could not be checked' in text
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
    monkeypatch.setattr(canary, 'FLATLINE', tmp_path / 'nowhere')
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
    monkeypatch.setattr(canary, 'FLATLINE', tmp_path / 'nowhere')
    rc = canary.main([str(d), '--state', str(tmp_path / 's.json'),
                      '--report', str(tmp_path / 'r.html'),
                      '--results', str(tmp_path / 'res.json'), '--fail-on-findings'])
    assert rc == 1


def test_report_renders_findings_visibly(tmp_path):
    out = tmp_path / 'r.html'
    canary.render({'jobs.csv': {'status': 'FINDINGS', 'count': 2,
                                'flags': ['!! status: constant value', '!! notes: empty']}},
                  out, [tmp_path])
    text = out.read_text(encoding='utf-8')
    assert '<b>1</b> with findings' in text
    assert 'constant value' in text
