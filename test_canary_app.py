"""Tests for the double-clickable build.

The app is where a promise is easiest to break quietly: a friendly dialog is
exactly the place a "could not check" turns into "all good". These pin the two
claims the product makes that the CLI's tests cannot cover -- that the packaged
analysis agrees with the real one, and that the sentence a non-technical owner
reads never softens a failure into a pass.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import canary                                                 # noqa: E402
import canary_app                                             # noqa: E402


def _csv(p, text):
    p.write_text(text.strip() + '\n', encoding='utf-8')
    return p


def _dead(tmp_path, name='export.csv'):
    rows = '\n'.join(f'J-{i},OPEN,NORTH' for i in range(1, 13))
    return _csv(tmp_path / name, 'job,status,region\n' + rows)


def test_the_packaged_analysis_agrees_with_the_real_one(tmp_path):
    """The load-bearing claim of the whole app. The frozen build cannot spawn
    `python -m flatline`, so it calls flatline's cli in-process instead. If
    those two paths ever read the same file differently, the product would be
    lying about the tool it ships."""
    f = _dead(tmp_path)
    subprocess_result = canary.analyze(f)          # the ordinary path
    frozen_result = canary._analyze_frozen(f)      # the path inside canary.exe

    assert subprocess_result['status'] == frozen_result['status'] == 'FINDINGS'
    assert subprocess_result['flags'] == frozen_result['flags']
    assert subprocess_result['count'] == frozen_result['count']


def test_both_paths_agree_a_clean_file_is_clean(tmp_path):
    f = _csv(tmp_path / 'ok.csv', 'a,b\n1,x\n2,y\n3,z\n4,w\n5,v\n6,u\n7,t\n8,s\n9,r')
    assert canary.analyze(f)['status'] == canary._analyze_frozen(f)['status']


def test_an_uncheckable_file_is_named_first_and_never_called_fine():
    """The dialog is the one surface a non-technical owner actually reads. An
    ERROR folded into a count of files that were fine would undo every honest
    thing in the report behind it."""
    kind, text = canary_app._summary_text({
        'a.csv': {'status': 'ERROR', 'detail': 'locked'},
        'b.csv': {'status': 'FINDINGS', 'count': 1, 'flags': ['x']},
        'c.csv': {'status': 'CLEAN', 'count': 0, 'flags': []},
    })
    assert kind == 'warn'
    assert 'COULD NOT BE CHECKED' in text
    assert text.index('COULD NOT BE CHECKED') < text.index('findings')
    assert 'not a pass' in text
    for lie in ('all good', 'all clear', 'everything looks'):
        assert lie not in text.lower()


def test_a_clean_run_says_what_was_actually_examined():
    kind, text = canary_app._summary_text({
        'a.csv': {'status': 'CLEAN', 'count': 0, 'flags': []},
    })
    assert kind == 'info'
    assert '1 file(s) checked' in text
    assert 'right now' in text        # scoped in time, not a permanent guarantee


def test_findings_are_reported_as_a_warning_not_an_informational_note():
    kind, _ = canary_app._summary_text({'a.csv': {'status': 'FINDINGS', 'count': 2, 'flags': []}})
    assert kind == 'warn'


def test_preferences_survive_a_round_trip_and_a_corrupt_file(tmp_path):
    canary_app._save_prefs(tmp_path, {'last_folder': 'C:/exports'})
    assert canary_app._load_prefs(tmp_path)['last_folder'] == 'C:/exports'

    (tmp_path / 'app.json').write_text('{not json', encoding='utf-8')
    assert canary_app._load_prefs(tmp_path) == {}, 'a corrupt pref file must not crash the app'


def test_the_state_dir_is_writable_and_not_beside_the_executable(tmp_path, monkeypatch):
    """People run downloads from Program Files, where writing beside the exe
    fails. A tool whose first act is an unexplained permission error does not
    get a second run."""
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    d = canary_app._state_dir()
    assert d.is_dir()
    (d / 'probe').write_text('ok', encoding='utf-8')
    assert str(tmp_path / 'local') in str(d)


def test_arguments_bypass_the_dialog_entirely(tmp_path, monkeypatch):
    """Passing arguments must behave exactly like the CLI. If the app ever
    opened a picker for a scripted run it would hang a scheduled job forever."""
    def _boom(*a, **k):
        raise AssertionError('the folder picker must not open when arguments are given')
    monkeypatch.setattr(canary_app, '_pick_folder', _boom)

    d = tmp_path / 'exports'
    d.mkdir()
    _dead(d)
    rc = canary_app.main([str(d), '--state', str(tmp_path / 's.json'),
                          '--report', str(tmp_path / 'r.html'),
                          '--results', str(tmp_path / 'res.json'), '--fail-on-findings'])
    assert rc == 2
    assert json.loads((tmp_path / 'res.json').read_text(encoding='utf-8'))


def test_cancelling_the_picker_does_nothing_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setattr(canary_app, '_pick_folder', lambda initial: '')
    said = []
    monkeypatch.setattr(canary_app, '_say', lambda *a, **k: said.append(a))
    assert canary_app.main([]) == 0
    assert said == [], 'cancelling must not lecture the person who cancelled'
