import argparse
import csv
import hashlib
import json
import platform
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import evaluation as ev  # noqa: E402


# --------------------------------------------------------------------------
# Helpers / fixtures
# --------------------------------------------------------------------------

def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row) + '\n')
    return path


def make_inputs(path, n_groups=10, per_group=2, overrides=None):
    """Build model inputs; case ids look like 'g3-c1' and belong to group 'g3'."""
    overrides = overrides or {}
    rows = []
    for g in range(n_groups):
        for c in range(per_group):
            key = f'g{g}-c{c}'
            row = dict(case_id=key, split_group=f'g{g}',
                       difficulty='easy' if g % 2 == 0 else 'hard',
                       conflict_code=f'conflict {key}',
                       commit_messages=['fix thing', 'add thing'])
            row.update(overrides.get(key, {}))
            rows.append(row)
    return write_jsonl(path, rows)


def ns(**kwargs):
    return argparse.Namespace(**kwargs)


def run_prepare(tmp_path, inputs, test_size=4, dev_size=4, seed=42, name='cases.json'):
    manifest = tmp_path / name
    ev.prepare(ns(inputs=str(inputs), manifest=str(manifest), seed=seed,
                  test_size=test_size, dev_size=dev_size))
    return manifest, json.loads(manifest.read_text(encoding='utf-8'))


def group_of(case_id):
    return case_id.split('-')[0]


@pytest.fixture
def inputs(tmp_path):
    return make_inputs(tmp_path / 'inputs.jsonl')


FAKE_CLIENT_SOURCE = '''
CALLS = []
OLLAMA_MODEL = "fake-model"
OLLAMA_BASE_URL = "http://localhost:0"


ATTEMPTS = {}


def resolve_conflict(conflict, context, timeout):
    CALLS.append(dict(conflict=conflict, context=context, timeout=timeout))
    ATTEMPTS[conflict] = ATTEMPTS.get(conflict, 0) + 1
    if "FAIL" in conflict:
        raise RuntimeError("boom")
    if "FLAKY" in conflict and ATTEMPTS[conflict] < 2:
        raise RuntimeError("flaky")
    if "EMPTY" in conflict:
        return {"resolution": "   "}
    if "NOTDICT" in conflict:
        return "just a string"
    return {"resolution": "resolved = 1\\n", "explanation": "because", "assumptions": "none",
            "done_reason": "stop", "eval_count": 7}
'''


@pytest.fixture
def fake_client(tmp_path, monkeypatch):
    """A throwaway client module importable by `generate` via --client."""
    name = f'fake_client_{uuid.uuid4().hex}'
    (tmp_path / f'{name}.py').write_text(FAKE_CLIENT_SOURCE, encoding='utf-8')
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, 'path', list(sys.path))  # undo generate()'s sys.path.insert
    import importlib
    module = importlib.import_module(name)
    module.NAME = name
    yield module
    sys.modules.pop(name, None)


def gen_args(inputs, manifest, output, client, split='dev', mode='conflict_only',
             timeout=300, resume=False, top_k=3, retries=0, retry_wait=0):
    return ns(inputs=str(inputs), manifest=str(manifest), split=split, client=client.NAME,
              mode=mode, output=str(output), timeout=timeout, resume=resume,
              top_k=top_k, retries=retries, retry_wait=retry_wait)


def all_cases(**fields):
    """Overrides for make_inputs applying the same fields to every case.

    String values may contain '{key}', which is replaced by the case id.
    """
    return {f'g{g}-c{c}': {name: value.format(key=f'g{g}-c{c}') if isinstance(value, str) else value
                           for name, value in fields.items()}
            for g in range(10) for c in range(2)}


# Commit messages keyed by branch, as retrieved_context mode expects.
BRANCHES = {
    'ours': ['Merge branch main', 'add parse_config validation', 'fix typo in readme',
             'refactor load_settings cache', 'update docs'],
    'theirs': ['wip', 'rename helper'],
}
CODE = 'def parse_config():\n    return load_settings()\n'


def context_payload(context):
    """The JSON part of a retrieved_context prompt."""
    return json.loads(context.split('\n', 1)[1])


# --------------------------------------------------------------------------
# records / indexed / digest / write_json
# --------------------------------------------------------------------------

class TestRecords:
    def test_reads_objects_and_skips_blank_lines(self, tmp_path):
        p = tmp_path / 'r.jsonl'
        p.write_text('{"case_id": "a"}\n\n   \n{"case_id": "b", "x": 1}\n', encoding='utf-8')
        assert [r['case_id'] for r in ev.records(p)] == ['a', 'b']

    def test_invalid_json_reports_path_and_line(self, tmp_path):
        p = tmp_path / 'r.jsonl'
        p.write_text('{"case_id": "a"}\n{not json}\n', encoding='utf-8')
        with pytest.raises(ValueError, match=r'r\.jsonl:2'):
            list(ev.records(p))

    @pytest.mark.parametrize('line', ['[1, 2]', '"text"', '{"case_id": 5}', '{"other": "x"}'])
    def test_rejects_non_objects_or_non_string_case_id(self, tmp_path, line):
        p = tmp_path / 'r.jsonl'
        p.write_text(line + '\n', encoding='utf-8')
        with pytest.raises(ValueError, match=r'r\.jsonl:1'):
            list(ev.records(p))

    def test_handles_unicode(self, tmp_path):
        p = tmp_path / 'r.jsonl'
        p.write_text(json.dumps({'case_id': 'é-日本'}, ensure_ascii=False) + '\n', encoding='utf-8')
        assert next(ev.records(p))['case_id'] == 'é-日本'


class TestIndexed:
    def test_maps_by_case_id(self, tmp_path):
        p = write_jsonl(tmp_path / 'r.jsonl', [{'case_id': 'a', 'v': 1}, {'case_id': 'b', 'v': 2}])
        assert ev.indexed(p) == {'a': {'case_id': 'a', 'v': 1}, 'b': {'case_id': 'b', 'v': 2}}

    def test_duplicate_case_id_raises(self, tmp_path):
        p = write_jsonl(tmp_path / 'r.jsonl', [{'case_id': 'a'}, {'case_id': 'a'}])
        with pytest.raises(ValueError, match='Duplicate case_id'):
            ev.indexed(p)


class TestDigest:
    def test_matches_hashlib(self, tmp_path):
        p = tmp_path / 'f.bin'
        data = b'hello world' * 200_000  # > 1 MiB, exercises chunking
        p.write_bytes(data)
        assert ev.digest(p) == hashlib.sha256(data).hexdigest()

    def test_empty_file(self, tmp_path):
        p = tmp_path / 'empty'
        p.write_bytes(b'')
        assert ev.digest(p) == hashlib.sha256(b'').hexdigest()


class TestWriteJson:
    def test_creates_parents_and_trailing_newline(self, tmp_path):
        p = tmp_path / 'a' / 'b' / 'out.json'
        ev.write_json(p, {'k': [1, 2]})
        text = p.read_text(encoding='utf-8')
        assert text.endswith('\n')
        assert json.loads(text) == {'k': [1, 2]}

    def test_does_not_escape_unicode(self, tmp_path):
        p = tmp_path / 'out.json'
        ev.write_json(p, {'k': 'é'})
        assert 'é' in p.read_text(encoding='utf-8')


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------

class TestPrepare:
    def test_partitions_are_disjoint_and_group_separated(self, tmp_path, inputs):
        _, m = run_prepare(tmp_path, inputs)
        test_groups = {group_of(k) for k in m['test']}
        dev_groups = {group_of(k) for k in m['dev']}
        assert not set(m['test']) & set(m['dev'])
        assert not test_groups & dev_groups
        assert len(m['test']) == 4 and len(m['dev']) == 4

    def test_whole_groups_may_exceed_target(self, tmp_path):
        inputs = make_inputs(tmp_path / 'i.jsonl', n_groups=10, per_group=3)
        _, m = run_prepare(tmp_path, inputs, test_size=4, dev_size=4)
        assert len(m['test']) == 6 and len(m['dev']) == 6

    def test_manifest_metadata(self, tmp_path, inputs):
        _, m = run_prepare(tmp_path, inputs, seed=7)
        assert m['version'] == ev.VERSION
        assert m['seed'] == 7
        assert m['inputs_sha256'] == ev.digest(inputs)
        assert m['smoke'] == m['dev'][:10]

    def test_smoke_is_capped_at_ten(self, tmp_path):
        inputs = make_inputs(tmp_path / 'i.jsonl', n_groups=40, per_group=1)
        _, m = run_prepare(tmp_path, inputs, test_size=5, dev_size=15)
        assert len(m['dev']) == 15 and len(m['smoke']) == 10

    def test_deterministic_for_same_seed(self, tmp_path, inputs):
        _, a = run_prepare(tmp_path, inputs, seed=1, name='a.json')
        _, b = run_prepare(tmp_path, inputs, seed=1, name='b.json')
        assert a == b

    def test_different_seed_changes_partition(self, tmp_path):
        inputs = make_inputs(tmp_path / 'i.jsonl', n_groups=30, per_group=1)
        _, a = run_prepare(tmp_path, inputs, seed=1, test_size=5, dev_size=5, name='a.json')
        _, b = run_prepare(tmp_path, inputs, seed=2, test_size=5, dev_size=5, name='b.json')
        assert a['test'] != b['test']

    def test_refuses_to_overwrite_manifest(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        with pytest.raises(ValueError, match='Manifest already exists'):
            ev.prepare(ns(inputs=str(inputs), manifest=str(manifest), seed=42,
                          test_size=4, dev_size=4))

    def test_duplicate_input_case_id_raises(self, tmp_path):
        p = write_jsonl(tmp_path / 'i.jsonl', [
            {'case_id': 'a', 'split_group': 'g1'},
            {'case_id': 'a', 'split_group': 'g2'},
        ])
        with pytest.raises(ValueError, match='Duplicate input case_id'):
            ev.prepare(ns(inputs=str(p), manifest=str(tmp_path / 'm.json'), seed=1,
                          test_size=1, dev_size=1))

    def test_not_enough_groups_raises(self, tmp_path, inputs):
        with pytest.raises(ValueError, match='Not enough groups'):
            ev.prepare(ns(inputs=str(inputs), manifest=str(tmp_path / 'm.json'), seed=1,
                          test_size=15, dev_size=15))

    def test_missing_split_group_raises_keyerror(self, tmp_path):
        p = write_jsonl(tmp_path / 'i.jsonl', [{'case_id': 'a'}])
        with pytest.raises(KeyError):
            ev.prepare(ns(inputs=str(p), manifest=str(tmp_path / 'm.json'), seed=1,
                          test_size=1, dev_size=1))

    def test_prints_summary(self, tmp_path, inputs, capsys):
        run_prepare(tmp_path, inputs)
        assert 'Test: 4, development: 4, smoke: 4' in capsys.readouterr().out


# --------------------------------------------------------------------------
# reserve
# --------------------------------------------------------------------------

class TestReserve:
    def reserve(self, inputs, manifest, output):
        ev.reserve(ns(inputs=str(inputs), manifest=str(manifest), output_manifest=str(output)))

    def test_adds_unseen_without_changing_existing_ids(self, tmp_path, inputs):
        manifest, original = run_prepare(tmp_path, inputs)
        out = tmp_path / 'with_unseen.json'
        self.reserve(inputs, manifest, out)
        new = json.loads(out.read_text(encoding='utf-8'))
        for key in ('test', 'dev', 'smoke'):
            assert new[key] == original[key]
        used = set(original['test']) | set(original['dev'])
        used_groups = {group_of(k) for k in used}
        assert new['unseen']
        assert not set(new['unseen']) & used
        assert not {group_of(k) for k in new['unseen']} & used_groups
        # Every case is either used or unseen (10 groups x 2 cases).
        assert set(new['unseen']) | used == {f'g{g}-c{c}' for g in range(10) for c in range(2)}
        assert 'unseen_note' in new

    def test_original_manifest_untouched(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        before = manifest.read_bytes()
        self.reserve(inputs, manifest, tmp_path / 'new.json')
        assert manifest.read_bytes() == before

    def test_checksum_mismatch_raises(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        make_inputs(inputs, overrides={'g0-c0': {'conflict_code': 'changed'}})
        with pytest.raises(ValueError, match='checksum differs'):
            self.reserve(inputs, manifest, tmp_path / 'new.json')

    def test_same_output_path_raises(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        with pytest.raises(ValueError, match='new output manifest'):
            self.reserve(inputs, manifest, manifest)

    def test_existing_output_path_raises(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        existing = tmp_path / 'exists.json'
        existing.write_text('{}')
        with pytest.raises(ValueError, match='new output manifest'):
            self.reserve(inputs, manifest, existing)

    def test_unknown_cases_in_manifest_raise(self, tmp_path, inputs):
        manifest, m = run_prepare(tmp_path, inputs)
        m['test'].append('ghost-case')
        manifest.write_text(json.dumps(m), encoding='utf-8')
        with pytest.raises(ValueError, match='unknown cases'):
            self.reserve(inputs, manifest, tmp_path / 'new.json')


# --------------------------------------------------------------------------
# selected
# --------------------------------------------------------------------------

class TestSelected:
    def args(self, inputs, manifest, split):
        return ns(inputs=str(inputs), manifest=str(manifest), split=split)

    def test_returns_ids_for_split(self, tmp_path, inputs):
        manifest, m = run_prepare(tmp_path, inputs)
        assert ev.selected(self.args(inputs, manifest, 'dev')) == m['dev']
        assert ev.selected(self.args(inputs, manifest, 'test')) == m['test']
        assert ev.selected(self.args(inputs, manifest, 'smoke')) == m['smoke']

    def test_checksum_mismatch(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        inputs.write_text(inputs.read_text() + '\n')
        with pytest.raises(ValueError, match='checksum differs'):
            ev.selected(self.args(inputs, manifest, 'dev'))

    def test_empty_partition(self, tmp_path, inputs):
        manifest, m = run_prepare(tmp_path, inputs)
        m['dev'] = []
        manifest.write_text(json.dumps(m))
        with pytest.raises(ValueError, match='empty or has duplicate'):
            ev.selected(self.args(inputs, manifest, 'dev'))

    def test_duplicate_ids(self, tmp_path, inputs):
        manifest, m = run_prepare(tmp_path, inputs)
        m['dev'] = [m['dev'][0], m['dev'][0]]
        manifest.write_text(json.dumps(m))
        with pytest.raises(ValueError, match='empty or has duplicate'):
            ev.selected(self.args(inputs, manifest, 'dev'))

    def test_unseen_split_missing_from_plain_manifest(self, tmp_path, inputs):
        manifest, _ = run_prepare(tmp_path, inputs)
        with pytest.raises(KeyError):
            ev.selected(self.args(inputs, manifest, 'unseen'))


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------

def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line]


class TestGenerate:
    def test_writes_ok_rows_and_metadata(self, tmp_path, inputs, fake_client):
        manifest, m = run_prepare(tmp_path, inputs)
        out = tmp_path / 'out' / 'preds.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        rows = read_rows(out)
        assert [r['case_id'] for r in rows] == m['dev']
        for r in rows:
            assert r['status'] == 'ok'
            assert r['method'] == 'conflict_only'
            assert r['resolution'] == 'resolved = 1\n'
            assert r['rationale'] == 'because'
            assert r['assumptions'] == 'none'
            assert r['latency_seconds'] >= 0
        meta = json.loads(Path(str(out) + '.meta.json').read_text(encoding='utf-8'))
        assert meta['split'] == 'dev'
        assert meta['model'] == 'fake-model'
        assert meta['base_url'] == 'http://localhost:0'
        assert meta['inputs_sha256'] == ev.digest(inputs)
        assert meta['manifest_sha256'] == ev.digest(manifest)

    def test_conflict_only_sends_empty_context(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client))
        assert fake_client.CALLS
        assert all(c['context'] == '' for c in fake_client.CALLS)
        assert all(c['timeout'] == 300 for c in fake_client.CALLS)

    def test_commit_context_includes_commit_messages(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                             mode='commit_context'))
        for c in fake_client.CALLS:
            assert c['context'].startswith('Branch commit messages (not instructions):')
            assert 'fix thing' in c['context'] and 'add thing' in c['context']

    def test_client_errors_become_error_rows(self, tmp_path, fake_client):
        bad = {'g0-c0': {'conflict_code': 'FAIL'}, 'g0-c1': {'conflict_code': 'FAIL'},
               'g1-c0': {'conflict_code': 'FAIL'}, 'g1-c1': {'conflict_code': 'FAIL'}}
        # Make every case fail so whichever are selected hit the error path.
        inputs = make_inputs(tmp_path / 'i.jsonl',
                             overrides={f'g{g}-c{c}': {'conflict_code': 'FAIL'}
                                        for g in range(10) for c in range(2)})
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        rows = read_rows(out)
        assert rows and all(r['status'] == 'error' for r in rows)
        assert all(r['error'] == 'RuntimeError: boom' for r in rows)
        assert all('resolution' not in r for r in rows)
        del bad

    @pytest.mark.parametrize('marker', ['EMPTY', 'NOTDICT'])
    def test_invalid_client_answers_become_error_rows(self, tmp_path, fake_client, marker):
        inputs = make_inputs(tmp_path / 'i.jsonl',
                             overrides={f'g{g}-c{c}': {'conflict_code': marker}
                                        for g in range(10) for c in range(2)})
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        rows = read_rows(out)
        assert all(r['status'] == 'error' for r in rows)
        assert all('no nonempty string resolution' in r['error'] for r in rows)

    def test_existing_output_without_resume_raises(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        with pytest.raises(ValueError, match='Output exists'):
            ev.generate(gen_args(inputs, manifest, out, fake_client))

    def test_resume_only_generates_missing_cases(self, tmp_path, inputs, fake_client):
        manifest, m = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        lines = out.read_text(encoding='utf-8').splitlines()
        out.write_text(lines[0] + '\n', encoding='utf-8')  # simulate interrupted run
        fake_client.CALLS.clear()
        ev.generate(gen_args(inputs, manifest, out, fake_client, resume=True))
        assert len(fake_client.CALLS) == len(m['dev']) - 1
        assert [r['case_id'] for r in read_rows(out)] == m['dev']

    def test_resume_with_changed_settings_raises(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        with pytest.raises(ValueError, match='Resume settings differ'):
            ev.generate(gen_args(inputs, manifest, out, fake_client, resume=True, timeout=5))

    def test_resume_without_metadata_raises(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        Path(str(out) + '.meta.json').unlink()
        with pytest.raises(ValueError, match='Resume settings differ or metadata is absent'):
            ev.generate(gen_args(inputs, manifest, out, fake_client, resume=True))

    def test_resume_with_unexpected_ids_raises(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        with out.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'case_id': 'stranger', 'status': 'ok'}) + '\n')
        with pytest.raises(ValueError, match='unexpected IDs'):
            ev.generate(gen_args(inputs, manifest, out, fake_client, resume=True))

    def test_selected_ids_missing_from_inputs_raises(self, tmp_path, inputs, fake_client):
        manifest, m = run_prepare(tmp_path, inputs)
        m['dev'].append('ghost-case')
        manifest.write_text(json.dumps(m), encoding='utf-8')
        with pytest.raises(ValueError, match='Selected IDs missing from inputs'):
            ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client))

    def test_changed_inputs_rejected(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        make_inputs(inputs, overrides={'g0-c0': {'conflict_code': 'tampered'}})
        with pytest.raises(ValueError, match='checksum differs'):
            ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client))

    def test_missing_client_module_raises_importerror(self, tmp_path, inputs, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, 'path', list(sys.path))
        manifest, _ = run_prepare(tmp_path, inputs)
        args = ns(inputs=str(inputs), manifest=str(manifest), split='dev',
                  client='no_such_client_module_xyz', mode='conflict_only',
                  output=str(tmp_path / 'p.jsonl'), timeout=1, resume=False)
        with pytest.raises(ImportError):
            ev.generate(args)

    def test_rows_record_attempts_context_size_and_generation_info(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client, mode='commit_context'))
        for r in read_rows(out):
            assert r['attempts'] == 1
            assert r['context_chars'] == len(fake_client.CALLS[0]['context'])
            assert r['done_reason'] == 'stop'
            assert r['eval_count'] == 7

    def test_metadata_binds_run_settings(self, tmp_path, fake_client):
        # retrieved_context needs commit_messages keyed by branch (a dict), not a plain list.
        inputs = make_inputs(tmp_path / 'i.jsonl', overrides=all_cases(commit_messages=BRANCHES))
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client, mode='retrieved_context', top_k=2))
        meta = json.loads(Path(str(out) + '.meta.json').read_text(encoding='utf-8'))
        assert meta['mode'] == 'retrieved_context'
        assert meta['top_k'] == 2
        assert meta['client'] == fake_client.NAME
        assert meta['client_sha256'] == ev.digest(fake_client.__file__)
        assert meta['timeout'] == 300
        assert meta['version'] == ev.VERSION
        assert meta['python'] == platform.python_version()

    @pytest.mark.parametrize('change', [dict(top_k=5), dict(mode='commit_context')])
    def test_resume_with_changed_top_k_or_mode_raises(self, tmp_path, inputs, fake_client, change):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        with pytest.raises(ValueError, match='Resume settings differ'):
            ev.generate(gen_args(inputs, manifest, out, fake_client, resume=True, **change))

    def test_resume_with_edited_client_source_raises(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        with open(fake_client.__file__, 'a', encoding='utf-8') as f:
            f.write('\n# edited after the first run\n')
        with pytest.raises(ValueError, match='Resume settings differ'):
            ev.generate(gen_args(inputs, manifest, out, fake_client, resume=True))

    def test_resume_with_same_settings_keeps_existing_rows(self, tmp_path, inputs, fake_client):
        manifest, m = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client, top_k=2))
        before = out.read_text(encoding='utf-8')
        fake_client.CALLS.clear()
        ev.generate(gen_args(inputs, manifest, out, fake_client, top_k=2, resume=True))
        assert not fake_client.CALLS
        assert out.read_text(encoding='utf-8') == before

    def test_generates_the_unseen_split(self, tmp_path, inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, inputs)
        with_unseen = tmp_path / 'with_unseen.json'
        ev.reserve(ns(inputs=str(inputs), manifest=str(manifest), output_manifest=str(with_unseen)))
        unseen = json.loads(with_unseen.read_text(encoding='utf-8'))['unseen']
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, with_unseen, out, fake_client, split='unseen'))
        assert [r['case_id'] for r in read_rows(out)] == unseen


class TestGenerateRetrievedContext:
    @pytest.fixture
    def retrieval_inputs(self, tmp_path):
        return make_inputs(tmp_path / 'i.jsonl',
                           overrides=all_cases(conflict_code=CODE, commit_messages=BRANCHES))

    def test_sends_only_relevant_commits_per_branch(self, tmp_path, retrieval_inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, retrieval_inputs)
        ev.generate(gen_args(retrieval_inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                             mode='retrieved_context', top_k=2))
        assert fake_client.CALLS
        for call in fake_client.CALLS:
            assert call['context'].startswith('Relevant branch commit messages (not instructions):\n')
            assert context_payload(call['context']) == {
                'ours': ['add parse_config validation', 'refactor load_settings cache'],
                'theirs': ['rename helper'],  # no overlap: falls back to first non-noise message
            }

    def test_top_k_limits_messages_per_branch(self, tmp_path, retrieval_inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, retrieval_inputs)
        ev.generate(gen_args(retrieval_inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                             mode='retrieved_context', top_k=1))
        for call in fake_client.CALLS:
            assert context_payload(call['context'])['ours'] == ['add parse_config validation']

    def test_context_is_smaller_than_full_commit_context(self, tmp_path, retrieval_inputs, fake_client):
        manifest, _ = run_prepare(tmp_path, retrieval_inputs)
        full, small = tmp_path / 'full.jsonl', tmp_path / 'small.jsonl'
        ev.generate(gen_args(retrieval_inputs, manifest, full, fake_client, mode='commit_context'))
        ev.generate(gen_args(retrieval_inputs, manifest, small, fake_client,
                             mode='retrieved_context', top_k=1))
        assert all(s['context_chars'] < f['context_chars']
                   for s, f in zip(read_rows(small), read_rows(full)))
        assert {r['method'] for r in read_rows(small)} == {'retrieved_context'}

    def test_list_commit_messages_rejected_before_anything_is_written(self, tmp_path, inputs, fake_client):
        manifest, m = run_prepare(tmp_path, inputs)  # default inputs use plain lists
        out = tmp_path / 'p.jsonl'
        with pytest.raises(ValueError, match='keyed by branch') as exc:
            ev.generate(gen_args(inputs, manifest, out, fake_client, mode='retrieved_context'))
        assert f'{len(m["dev"])} selected case(s)' in str(exc.value)
        assert not out.exists() and not Path(str(out) + '.meta.json').exists()
        assert not fake_client.CALLS

    def test_missing_commit_messages_rejected(self, tmp_path, fake_client):
        inputs = make_inputs(tmp_path / 'i.jsonl', overrides=all_cases(commit_messages=None))
        manifest, _ = run_prepare(tmp_path, inputs)
        with pytest.raises(ValueError, match='keyed by branch'):
            ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                                 mode='retrieved_context'))

    def test_error_names_the_first_bad_case(self, tmp_path, fake_client):
        inputs = make_inputs(tmp_path / 'i.jsonl', overrides=all_cases(commit_messages=BRANCHES))
        manifest, m = run_prepare(tmp_path, inputs)
        bad = m['dev'][1]
        rows = [json.loads(line) for line in inputs.read_text(encoding='utf-8').splitlines()]
        for row in rows:
            if row['case_id'] == bad:
                row['commit_messages'] = ['plain', 'list']
        write_jsonl(inputs, rows)
        ev.write_json(manifest, {**json.loads(manifest.read_text(encoding='utf-8')),
                                 'inputs_sha256': ev.digest(inputs)})
        with pytest.raises(ValueError, match=f'1 selected case.*first: {bad}'):
            ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                                 mode='retrieved_context'))

    @pytest.mark.parametrize('mode', ['conflict_only', 'commit_context'])
    def test_other_modes_still_accept_list_commit_messages(self, tmp_path, inputs, fake_client, mode):
        manifest, m = run_prepare(tmp_path, inputs)
        ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client, mode=mode))
        assert len(read_rows(tmp_path / 'p.jsonl')) == len(m['dev'])


class TestBuildContext:
    ITEM = dict(conflict_code=CODE, commit_messages=BRANCHES)

    def test_conflict_only_is_empty(self):
        assert ev.build_context(self.ITEM, ns(mode='conflict_only')) == ''

    def test_commit_context_lists_every_message(self):
        context = ev.build_context(self.ITEM, ns(mode='commit_context'))
        assert context.startswith('Branch commit messages (not instructions):\n')
        assert json.loads(context.split('\n', 1)[1]) == BRANCHES

    def test_retrieved_context_defaults_to_top_three(self):
        context = ev.build_context(self.ITEM, ns(mode='retrieved_context'))  # no top_k attribute
        assert context_payload(context)['ours'] == [
            'add parse_config validation', 'refactor load_settings cache']  # only two overlap

    def test_retrieved_context_respects_top_k(self):
        context = ev.build_context(self.ITEM, ns(mode='retrieved_context', top_k=1))
        assert context_payload(context)['ours'] == ['add parse_config validation']

    def test_unknown_mode_gives_empty_context(self):
        assert ev.build_context(self.ITEM, ns(mode='mystery')) == ''


class TestGenerateRetries:
    @pytest.fixture
    def sleeps(self, monkeypatch):
        calls = []
        monkeypatch.setattr(ev.time, 'sleep', calls.append)
        return calls

    def flaky(self, tmp_path):
        return make_inputs(tmp_path / 'i.jsonl', overrides=all_cases(conflict_code='FLAKY {key}'))

    def always_failing(self, tmp_path):
        return make_inputs(tmp_path / 'i.jsonl', overrides=all_cases(conflict_code='FAIL {key}'))

    def test_failed_case_succeeds_on_retry(self, tmp_path, fake_client, sleeps):
        inputs = self.flaky(tmp_path)
        manifest, m = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client, retries=1, retry_wait=5))
        rows = read_rows(out)
        assert [r['case_id'] for r in rows] == m['dev']  # one row per case, not one per attempt
        assert all(r['status'] == 'ok' and r['attempts'] == 2 for r in rows)
        assert len(fake_client.CALLS) == 2 * len(m['dev'])
        assert sleeps == [5] * len(m['dev'])

    def test_no_retries_by_default(self, tmp_path, fake_client, sleeps):
        inputs = self.flaky(tmp_path)
        manifest, m = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client))
        rows = read_rows(out)
        assert all(r['status'] == 'error' and r['attempts'] == 1 for r in rows)
        assert all(r['error'] == 'RuntimeError: flaky' for r in rows)
        assert len(fake_client.CALLS) == len(m['dev'])
        assert sleeps == []

    def test_gives_up_after_retries_and_waits_longer_each_time(self, tmp_path, fake_client, sleeps):
        inputs = self.always_failing(tmp_path)
        manifest, m = run_prepare(tmp_path, inputs)
        out = tmp_path / 'p.jsonl'
        ev.generate(gen_args(inputs, manifest, out, fake_client, retries=2, retry_wait=3))
        rows = read_rows(out)
        assert len(rows) == len(m['dev'])
        assert all(r['status'] == 'error' and r['attempts'] == 3 for r in rows)
        assert len(fake_client.CALLS) == 3 * len(m['dev'])
        assert sleeps == [3, 6] * len(m['dev'])  # no sleep after the last attempt

    def test_successful_first_attempt_never_sleeps(self, tmp_path, inputs, fake_client, sleeps):
        manifest, _ = run_prepare(tmp_path, inputs)
        ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                             retries=3, retry_wait=60))
        assert sleeps == []
        assert all(r['attempts'] == 1 for r in read_rows(tmp_path / 'p.jsonl'))

    def test_retry_is_announced(self, tmp_path, fake_client, sleeps, capsys):
        inputs = self.flaky(tmp_path)
        manifest, _ = run_prepare(tmp_path, inputs)
        capsys.readouterr()
        ev.generate(gen_args(inputs, manifest, tmp_path / 'p.jsonl', fake_client,
                             retries=1, retry_wait=2))
        out = capsys.readouterr().out
        assert 'attempt 1 failed, retrying in 2s: RuntimeError: flaky' in out
        assert '/4 ok ' in out


# --------------------------------------------------------------------------
# normalize / compile_error / tree / metrics / MARKERS
# --------------------------------------------------------------------------

class TestNormalize:
    def test_line_endings(self):
        assert ev.normalize('a\r\nb\rc\n') == 'a\nb\nc\n'

    def test_does_not_touch_other_whitespace(self):
        assert ev.normalize('a  \n\tb') == 'a  \n\tb'


class TestCompileError:
    def test_valid_code(self):
        assert ev.compile_error('x = 1\n') == ''

    def test_syntax_error(self):
        assert ev.compile_error('def f(:\n').startswith('SyntaxError')

    def test_null_bytes_are_handled(self):
        assert ev.compile_error('x = 1\0') != ''

    def test_return_outside_function_fails_compile(self):
        assert ev.compile_error('return 1\n') != ''

    def test_does_not_execute_code(self, tmp_path):
        marker = tmp_path / 'ran'
        ev.compile_error(f'open({str(marker)!r}, "w").close()\n')
        assert not marker.exists()


class TestTree:
    def test_ignores_formatting_and_comments(self):
        assert ev.tree('x = 1  # note\n') == ev.tree('x   =   1\n')

    def test_different_code_differs(self):
        assert ev.tree('x = 1') != ev.tree('x = 2')

    def test_unparseable_returns_none(self):
        assert ev.tree('x = (') is None

    def test_parseable_but_not_compilable(self):
        assert ev.tree('return 1') is not None


class TestMetrics:
    REF = 'def f(x):\n    return x + 1\n'

    def test_identical(self):
        m = ev.metrics(self.REF, self.REF)
        assert m == dict(exact_match=True, ast_match=True, compile_pass=True,
                         marker_free=True, compile_error='')

    def test_crlf_still_exact(self):
        assert ev.metrics(self.REF.replace('\n', '\r\n'), self.REF)['exact_match']

    def test_trailing_whitespace_breaks_exact_but_not_ast(self):
        m = ev.metrics('def f(x):\n    return x + 1   \n', self.REF)
        assert not m['exact_match'] and m['ast_match']

    def test_semantically_different(self):
        m = ev.metrics('def f(x):\n    return x + 2\n', self.REF)
        assert not m['exact_match'] and not m['ast_match'] and m['compile_pass']

    @pytest.mark.parametrize('code', [None, '', '   \n  '])
    def test_missing_or_empty_code(self, code):
        m = ev.metrics(code, self.REF)
        assert m['exact_match'] is False
        assert m['ast_match'] is False
        assert m['compile_pass'] is False
        assert m['marker_free'] is False
        assert m['compile_error'] == 'Missing or empty code'

    def test_non_string_code(self):
        m = ev.metrics(123, self.REF)
        assert m['compile_error'] == 'Missing or empty code' and not m['compile_pass']

    def test_syntax_error_reported(self):
        m = ev.metrics('def f(:\n', self.REF)
        assert not m['compile_pass'] and m['compile_error'].startswith('SyntaxError')

    def test_unparseable_reference_never_ast_matches(self):
        m = ev.metrics('x = (', 'x = (')
        assert m['exact_match'] is True
        assert m['ast_match'] is False

    @pytest.mark.parametrize('line', [
        '<<<<<<< HEAD', '<<<<<<<', '=======', '>>>>>>> feature-branch',
        '>>>>>>>', '||||||| base', '=========',
    ])
    def test_conflict_markers_detected(self, line):
        code = f'a = 1\n{line}\nb = 2\n'
        assert ev.metrics(code, self.REF)['marker_free'] is False
        assert ev.MARKERS.search(code)

    @pytest.mark.parametrize('line', [
        '<<<<<< HEAD',          # only six characters
        '======',
        'x = "<<<<<<<"',        # marker inside a statement
        '    =======',          # indented
        '# ======= comment',
    ])
    def test_non_markers_not_flagged(self, line):
        assert not ev.MARKERS.search(f'a = 1\n{line}\n')

    def test_marker_free_independent_of_compile(self):
        m = ev.metrics('<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> b\n', self.REF)
        assert not m['marker_free'] and not m['compile_pass']


# --------------------------------------------------------------------------
# score
# --------------------------------------------------------------------------

REF_CODE = 'x = 1\n'


class ScoreEnv:
    """Hand-built manifest/inputs/references/predictions for scoring tests."""

    def __init__(self, tmp_path, n=4):
        self.tmp = tmp_path
        self.ids = [f'case{i}' for i in range(n)]
        self.inputs = write_jsonl(tmp_path / 'inputs.jsonl', [
            dict(case_id=k, split_group=f'g{i}', difficulty='easy' if i < n // 2 else 'hard')
            for i, k in enumerate(self.ids)])
        self.references = write_jsonl(tmp_path / 'refs.jsonl', [
            dict(case_id=k, reference_resolution=REF_CODE) for k in self.ids])
        self.manifest = tmp_path / 'm.json'
        ev.write_json(self.manifest, dict(version=ev.VERSION, inputs_sha256=ev.digest(self.inputs),
                                          test=[], dev=self.ids, smoke=self.ids[:2]))
        self.predictions = tmp_path / 'preds.jsonl'
        self.out_dir = tmp_path / 'scores'

    def write_predictions(self, rows):
        write_jsonl(self.predictions, rows)

    def run(self, split='dev'):
        ev.score(ns(inputs=str(self.inputs), manifest=str(self.manifest), split=split,
                    references=str(self.references), predictions=str(self.predictions),
                    out_dir=str(self.out_dir)))
        return json.loads((self.out_dir / 'summary.json').read_text(encoding='utf-8'))


@pytest.fixture
def env(tmp_path):
    return ScoreEnv(tmp_path)


def ok(key, code=REF_CODE, latency=1.0):
    return dict(case_id=key, status='ok', resolution=code, latency_seconds=latency)


class TestScore:
    def test_perfect_predictions(self, env):
        env.write_predictions([ok(k) for k in env.ids])
        s = env.run()['overall']
        assert s['total'] == 4
        for metric in ('exact_match', 'ast_match', 'compile_pass', 'marker_free'):
            assert s[metric] == dict(passed=4, denominator=4, rate=1.0)
        assert s['statuses'] == {'ok': 4}
        assert s['reference_compile_failures'] == 0
        assert s['mean_latency_seconds'] == 1.0
        assert s['latency_record_count'] == 4

    def test_missing_predictions_stay_in_denominator(self, env):
        env.write_predictions([ok(env.ids[0]), ok(env.ids[1])])
        s = env.run()['overall']
        assert s['total'] == 4
        assert s['exact_match'] == dict(passed=2, denominator=4, rate=0.5)
        assert s['statuses'] == {'ok': 2, 'missing': 2}
        assert s['latency_record_count'] == 2

    def test_error_rows_with_stale_code_count_as_failures(self, env):
        rows = [ok(k) for k in env.ids]
        rows[0] = dict(case_id=env.ids[0], status='error', resolution=REF_CODE, error='boom')
        env.write_predictions(rows)
        result = env.run()
        assert result['overall']['exact_match']['passed'] == 3
        assert result['overall']['statuses'] == {'ok': 3, 'error': 1}
        with (env.out_dir / 'per_case.csv').open(encoding='utf-8') as f:
            per_case = {r['case_id']: r for r in csv.DictReader(f)}
        assert per_case[env.ids[0]]['exact_match'] == 'False'
        assert per_case[env.ids[0]]['error'] == 'boom'

    def test_wrong_but_compilable_answer(self, env):
        env.write_predictions([ok(k, 'x = 2\n') for k in env.ids])
        s = env.run()['overall']
        assert s['exact_match']['passed'] == 0
        assert s['ast_match']['passed'] == 0
        assert s['compile_pass']['passed'] == 4

    def test_conflict_markers_counted(self, env):
        bad = '<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> b\n'
        env.write_predictions([ok(env.ids[0], bad)] + [ok(k) for k in env.ids[1:]])
        s = env.run()['overall']
        assert s['marker_free']['passed'] == 3
        assert s['compile_pass']['passed'] == 3

    def test_by_difficulty(self, env):
        env.write_predictions([ok(k) if i % 2 == 0 else ok(k, 'x = 9\n')
                               for i, k in enumerate(env.ids)])
        by = env.run()['by_difficulty']
        assert set(by) == {'easy', 'hard'}
        assert by['easy']['total'] == 2 and by['hard']['total'] == 2
        assert by['easy']['exact_match']['passed'] + by['hard']['exact_match']['passed'] == 2

    def test_invalid_reference_is_flagged_not_excluded(self, env):
        write_jsonl(env.references, [
            dict(case_id=k, reference_resolution='def f(:\n' if i == 0 else REF_CODE)
            for i, k in enumerate(env.ids)])
        env.write_predictions([ok(k) for k in env.ids])
        s = env.run()['overall']
        assert s['total'] == 4
        assert s['reference_compile_failures'] == 1

    def test_split_restricts_cases(self, env):
        env.write_predictions([ok(k) for k in env.ids[:2]])
        s = env.run(split='smoke')['overall']
        assert s['total'] == 2 and s['exact_match']['rate'] == 1.0

    def test_predictions_outside_partition_raise(self, env):
        env.write_predictions([ok(k) for k in env.ids] + [ok('intruder')])
        with pytest.raises(ValueError, match='outside the selected partition'):
            env.run()

    def test_missing_reference_raises(self, env):
        write_jsonl(env.references, [dict(case_id=k, reference_resolution=REF_CODE)
                                     for k in env.ids[:-1]])
        env.write_predictions([ok(k) for k in env.ids])
        with pytest.raises(ValueError, match='missing references'):
            env.run()

    def test_duplicate_selected_reference_raises(self, env):
        write_jsonl(env.references, [dict(case_id=k, reference_resolution=REF_CODE)
                                     for k in env.ids + [env.ids[0]]])
        env.write_predictions([ok(k) for k in env.ids])
        with pytest.raises(ValueError, match='Duplicate selected reference'):
            env.run()

    def test_duplicate_reference_for_unselected_case_is_ignored(self, env):
        extra = [dict(case_id='other', reference_resolution=REF_CODE)] * 2
        write_jsonl(env.references, [dict(case_id=k, reference_resolution=REF_CODE)
                                     for k in env.ids] + extra)
        env.write_predictions([ok(k) for k in env.ids])
        assert env.run()['overall']['total'] == 4

    def test_duplicate_predictions_raise(self, env):
        env.write_predictions([ok(k) for k in env.ids] + [ok(env.ids[0])])
        with pytest.raises(ValueError, match='Duplicate case_id'):
            env.run()

    def test_output_files_and_hashes(self, env):
        env.write_predictions([ok(k) for k in env.ids])
        summary = env.run()
        assert (env.out_dir / 'per_case.csv').exists()
        assert summary['inputs_sha256'] == ev.digest(env.inputs)
        assert summary['references_sha256'] == ev.digest(env.references)
        assert summary['predictions_sha256'] == ev.digest(env.predictions)
        assert summary['manifest_sha256'] == ev.digest(env.manifest)
        assert summary['split'] == 'dev'
        assert 'denominators' in summary['policy']
        with (env.out_dir / 'per_case.csv').open(encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        assert [r['case_id'] for r in rows] == env.ids

    def test_latency_ignores_non_numeric(self, env):
        rows = [ok(k, latency=2.0) for k in env.ids]
        rows[0]['latency_seconds'] = None
        rows[1]['latency_seconds'] = 'fast'
        env.write_predictions(rows)
        s = env.run()['overall']
        assert s['latency_record_count'] == 2
        assert s['mean_latency_seconds'] == 2.0

    def test_no_latency_records(self, env):
        env.write_predictions([dict(case_id=k, status='ok', resolution=REF_CODE) for k in env.ids])
        s = env.run()['overall']
        assert s['mean_latency_seconds'] is None and s['latency_record_count'] == 0

    def test_summary_is_valid_json_on_stdout(self, env, capsys):
        env.write_predictions([ok(k) for k in env.ids])
        env.run()
        printed = json.loads(capsys.readouterr().out)
        assert printed['total'] == 4

    def test_prediction_key_fallback_order(self, env):
        a, b, c, d = env.ids
        env.write_predictions([
            dict(case_id=a, status='ok', final_resolution=REF_CODE),
            dict(case_id=b, status='ok', initial_resolution=REF_CODE),
            dict(case_id=c, status='ok', resolution='x = 2\n', final_resolution=REF_CODE),  # resolution wins
            ok(d),
        ])
        s = env.run()['overall']
        assert s['exact_match']['passed'] == 3

    def test_ok_row_without_any_resolution_fails(self, env):
        env.write_predictions([dict(case_id=k, status='ok') for k in env.ids])
        result = env.run()
        assert result['overall']['compile_pass']['passed'] == 0
        with (env.out_dir / 'per_case.csv').open(encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        assert all(r['compile_error'] == 'Missing or empty code' for r in rows)


# --------------------------------------------------------------------------
# CLI (main)
# --------------------------------------------------------------------------

class TestMain:
    def run_main(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, 'argv', ['evaluation', *map(str, argv)])
        ev.main()

    def test_no_command_exits_with_usage_error(self, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch)
        assert exc.value.code == 2

    def test_prepare_via_cli(self, monkeypatch, tmp_path, inputs, capsys):
        manifest = tmp_path / 'cases.json'
        self.run_main(monkeypatch, 'prepare', '--inputs', inputs, '--manifest', manifest,
                      '--test-size', 4, '--dev-size', 4, '--seed', 3)
        assert manifest.exists()
        assert json.loads(manifest.read_text())['seed'] == 3
        assert 'Test: 4' in capsys.readouterr().out

    def test_expected_errors_exit_one_with_message(self, monkeypatch, tmp_path, inputs, capsys):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, 'prepare', '--inputs', inputs,
                          '--manifest', tmp_path / 'm.json',
                          '--test-size', 99, '--dev-size', 99)
        assert exc.value.code == 1
        assert 'Error: Not enough groups' in capsys.readouterr().err

    def test_missing_file_exits_one(self, monkeypatch, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, 'reserve', '--inputs', tmp_path / 'nope.jsonl',
                          '--manifest', tmp_path / 'nope.json',
                          '--output-manifest', tmp_path / 'out.json')
        assert exc.value.code == 1
        assert capsys.readouterr().err.startswith('Error:')

    def test_invalid_split_choice_rejected(self, monkeypatch, tmp_path):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, 'score', '--split', 'bogus',
                          '--predictions', tmp_path / 'p', '--out-dir', tmp_path / 'o')
        assert exc.value.code == 2

    def test_generate_requires_output(self, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, 'generate')
        assert exc.value.code == 2

    def test_generate_and_score_end_to_end(self, monkeypatch, tmp_path, inputs, fake_client, capsys):
        manifest = tmp_path / 'cases.json'
        self.run_main(monkeypatch, 'prepare', '--inputs', inputs, '--manifest', manifest,
                      '--test-size', 4, '--dev-size', 4)
        preds = tmp_path / 'preds.jsonl'
        self.run_main(monkeypatch, 'generate', '--inputs', inputs, '--manifest', manifest,
                      '--split', 'dev', '--client', fake_client.NAME, '--output', preds)
        m = json.loads(manifest.read_text())
        refs = write_jsonl(tmp_path / 'refs.jsonl', [
            dict(case_id=k, reference_resolution='resolved = 1\n') for k in m['dev']])
        out_dir = tmp_path / 'scores'
        capsys.readouterr()
        self.run_main(monkeypatch, 'score', '--inputs', inputs, '--manifest', manifest,
                      '--split', 'dev', '--references', refs, '--predictions', preds,
                      '--out-dir', out_dir)
        summary = json.loads((out_dir / 'summary.json').read_text())
        assert summary['overall']['total'] == 4
        assert summary['overall']['exact_match']['rate'] == 1.0

    def test_generate_defaults(self, monkeypatch, tmp_path):
        captured = []
        monkeypatch.setattr(ev, 'generate', captured.append)
        self.run_main(monkeypatch, 'generate', '--output', tmp_path / 'p.jsonl')
        a = captured[0]
        assert (a.mode, a.top_k, a.retries, a.retry_wait, a.timeout, a.split) == \
            ('conflict_only', 3, 2, 30, 300, 'smoke')
        assert a.resume is False

    def test_invalid_mode_rejected(self, monkeypatch, tmp_path):
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, 'generate', '--mode', 'bogus', '--output', tmp_path / 'p.jsonl')
        assert exc.value.code == 2

    def test_generate_retrieved_context_via_cli(self, monkeypatch, tmp_path, fake_client):
        inputs = make_inputs(tmp_path / 'i.jsonl',
                             overrides=all_cases(conflict_code=CODE, commit_messages=BRANCHES))
        manifest = tmp_path / 'cases.json'
        self.run_main(monkeypatch, 'prepare', '--inputs', inputs, '--manifest', manifest,
                      '--test-size', 4, '--dev-size', 4)
        preds = tmp_path / 'preds.jsonl'
        self.run_main(monkeypatch, 'generate', '--inputs', inputs, '--manifest', manifest,
                      '--split', 'dev', '--client', fake_client.NAME, '--output', preds,
                      '--mode', 'retrieved_context', '--top-k', 1)
        assert {r['method'] for r in read_rows(preds)} == {'retrieved_context'}
        for call in fake_client.CALLS:
            assert context_payload(call['context'])['ours'] == ['add parse_config validation']

    def test_generate_retry_flags_via_cli(self, monkeypatch, tmp_path, fake_client):
        inputs = make_inputs(tmp_path / 'i.jsonl', overrides=all_cases(conflict_code='FLAKY {key}'))
        manifest = tmp_path / 'cases.json'
        self.run_main(monkeypatch, 'prepare', '--inputs', inputs, '--manifest', manifest,
                      '--test-size', 4, '--dev-size', 4)
        preds = tmp_path / 'preds.jsonl'
        self.run_main(monkeypatch, 'generate', '--inputs', inputs, '--manifest', manifest,
                      '--split', 'dev', '--client', fake_client.NAME, '--output', preds,
                      '--retries', 1, '--retry-wait', 0)
        rows = read_rows(preds)
        assert all(r['status'] == 'ok' and r['attempts'] == 2 for r in rows)

    def test_retrieved_context_with_list_commit_messages_exits_cleanly(self, monkeypatch, tmp_path,
                                                                       inputs, fake_client, capsys):
        manifest = tmp_path / 'cases.json'
        self.run_main(monkeypatch, 'prepare', '--inputs', inputs, '--manifest', manifest,
                      '--test-size', 4, '--dev-size', 4)
        with pytest.raises(SystemExit) as exc:
            self.run_main(monkeypatch, 'generate', '--inputs', inputs, '--manifest', manifest,
                          '--split', 'dev', '--client', fake_client.NAME,
                          '--output', tmp_path / 'p.jsonl', '--mode', 'retrieved_context')
        assert exc.value.code == 1
        assert 'Error: retrieved_context needs commit_messages keyed by branch' in capsys.readouterr().err