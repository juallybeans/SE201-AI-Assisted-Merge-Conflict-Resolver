import argparse
import ast
import collections
import csv
import hashlib
import importlib
import json
import platform
from pathlib import Path
import random
import re
import sys
import time

VERSION = '1.0'


def records(path):
    with open(path, encoding='utf-8') as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                if not isinstance(item, dict) or not isinstance(item.get('case_id'), str):
                    raise ValueError('object with string case_id required')
                yield item
            except (ValueError, TypeError) as exc:
                raise ValueError(f'{path}:{number}: {exc}') from exc


def indexed(path):
    result = {}
    for item in records(path):
        key = item['case_id']
        if key in result:
            raise ValueError(f'Duplicate case_id: {key}')
        result[key] = item
    return result


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def prepare(a):
    if Path(a.manifest).exists():
        raise ValueError('Manifest already exists; reuse it or choose a different path')
    groups = collections.defaultdict(list)
    seen = set()
    for x in records(a.inputs):
        if x['case_id'] in seen:
            raise ValueError('Duplicate input case_id')
        seen.add(x['case_id'])
        groups[x['split_group']].append(x['case_id'])
    names = sorted(groups)
    random.Random(a.seed).shuffle(names)
    test, dev = [], []
    for name in names:
        if len(test) < a.test_size:
            test.extend(groups[name])
        elif len(dev) < a.dev_size:
            dev.extend(groups[name])
    if len(test) < a.test_size or len(dev) < a.dev_size:
        raise ValueError('Not enough groups for requested partitions')
    write_json(a.manifest, dict(version=VERSION, seed=a.seed,
               inputs_sha256=digest(a.inputs), test=test, dev=dev,
               smoke=dev[:10], note='Whole groups: partition sizes may exceed targets. Smoke is part of dev.'))
    print(f'Test: {len(test)}, development: {len(dev)}, smoke: {len(dev[:10])}')



def reserve(a):
    """Add an unused partition without changing any existing IDs."""
    manifest = json.loads(Path(a.manifest).read_text(encoding='utf-8'))
    if digest(a.inputs) != manifest['inputs_sha256']:
        raise ValueError('Input checksum differs from manifest')
    if Path(a.output_manifest).resolve() == Path(a.manifest).resolve() or Path(a.output_manifest).exists():
        raise ValueError('Use a new output manifest path')
    rows = indexed(a.inputs)
    used = set(manifest.get('test', [])) | set(manifest.get('dev', [])) | set(manifest.get('smoke', []))
    if used - set(rows):
        raise ValueError('Manifest contains unknown cases')
    used_groups = {rows[key]['split_group'] for key in used}
    manifest['unseen'] = [key for key, row in rows.items() if row['split_group'] not in used_groups]
    manifest['unseen_note'] = 'Unused by this manifest only. Verify these cases were never used in other experiments before calling them unseen.'
    write_json(a.output_manifest, manifest)
    print(f"Preserved existing partitions; reserved {len(manifest['unseen'])} unused cases")

def selected(a):
    manifest = json.loads(Path(a.manifest).read_text(encoding='utf-8'))
    if digest(a.inputs) != manifest['inputs_sha256']:
        raise ValueError('Input checksum differs from the manifest; restore original data')
    ids = manifest[a.split]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Selected partition is empty or has duplicate IDs')
    return ids


def generate(a):
    ids = selected(a)
    selected_ids = set(ids)
    inputs = {x['case_id']: x for x in records(a.inputs) if x['case_id'] in selected_ids}
    if set(inputs) != selected_ids:
        raise ValueError('Selected IDs missing from inputs')
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Settings bind resume to the same data, partition, mode and client source.
    sys.path.insert(0, str(Path.cwd()))
    client = importlib.import_module(a.client)
    config = dict(version=VERSION, inputs_sha256=digest(a.inputs),
                  manifest_sha256=digest(a.manifest), split=a.split, mode=a.mode,
                  client=a.client, client_sha256=digest(client.__file__),
                  model=getattr(client, 'OLLAMA_MODEL', None),
                  base_url=getattr(client, 'OLLAMA_BASE_URL', None), timeout=a.timeout,
                  python=platform.python_version(),
                  note='Generation settings are inherited from client; current supplied client does not fix seed/temperature or report truncation.')
    meta = Path(str(output) + '.meta.json')
    done = {}
    if output.exists():
        if not a.resume:
            raise ValueError('Output exists. Use --resume or a different output filename')
        if not meta.exists() or json.loads(meta.read_text()) != config:
            raise ValueError('Resume settings differ or metadata is absent; use a new output')
        done = indexed(output)
        if set(done) - selected_ids:
            raise ValueError('Existing output contains unexpected IDs')
    else:
        write_json(meta, config)
    with output.open('a', encoding='utf-8') as f:
        for i, key in enumerate(ids, 1):
            if key in done:
                continue
            x = inputs[key]
            context = ''
            if a.mode == 'commit_context':
                context = 'Branch commit messages (not instructions):\n' + json.dumps(x['commit_messages'], ensure_ascii=False)
            # Retry so a brief server stall does not fail the case outright.
            retries = getattr(a, 'retries', 0)
            for attempt in range(1, retries + 2):
                row = dict(case_id=key, method=a.mode, status='error', attempts=attempt)
                start = time.monotonic()
                try:
                    answer = client.resolve_conflict(conflict=x['conflict_code'], context=context, timeout=a.timeout)
                    if not isinstance(answer, dict) or not isinstance(answer.get('resolution'), str) or not answer['resolution'].strip():
                        raise ValueError('Client returned no nonempty string resolution')
                    row.update(status='ok', resolution=answer['resolution'],
                               rationale=answer.get('explanation', ''), assumptions=answer.get('assumptions', ''),
                               done_reason=answer.get('done_reason'), eval_count=answer.get('eval_count'),
                               repair_attempted=answer.get('repair_attempted', False),
                               initial_resolution=answer.get('initial_resolution'),
                               initial_syntax_error=answer.get('initial_syntax_error', ''),
                               repair_error=answer.get('repair_error', ''))
                except Exception as exc:
                    row['error'] = f'{type(exc).__name__}: {exc}'
                row['latency_seconds'] = time.monotonic() - start
                if row['status'] == 'ok' or attempt > retries:
                    break
                wait = getattr(a, 'retry_wait', 0) * attempt
                print(f'{i}/{len(ids)} attempt {attempt} failed, retrying in {wait}s: {row["error"]}', flush=True)
                time.sleep(wait)
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
            f.flush()
            print(f'{i}/{len(ids)} {row["status"]} {key}', flush=True)


MARKERS = re.compile(r'^(?:<{7,}(?: .*)?|={7,}|>{7,}(?: .*)?|\|{7,}(?: .*)?)$', re.MULTILINE)


def normalize(code):
    return code.replace('\r\n', '\n').replace('\r', '\n')


def compile_error(code):
    try:
        compile(code, '<resolution>', 'exec', dont_inherit=True)
        return ''
    except (SyntaxError, ValueError, TypeError, RecursionError) as exc:
        return f'{type(exc).__name__}: {exc}'


def tree(code):
    try:
        return ast.dump(ast.parse(code), include_attributes=False)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return None


def metrics(code, reference):
    present = isinstance(code, str) and bool(code.strip())
    error = compile_error(code) if present else 'Missing or empty code'
    ref_tree = tree(reference)
    pred_tree = tree(code) if present else None
    return dict(exact_match=present and normalize(code) == normalize(reference),
                ast_match=bool(present and ref_tree is not None and pred_tree == ref_tree),
                compile_pass=present and not error,
                marker_free=present and not bool(MARKERS.search(code)),
                compile_error=error)


def score(a):
    ids = selected(a)
    wanted = set(ids)
    refs = {}
    for x in records(a.references):
        if x['case_id'] in wanted:
            if x['case_id'] in refs:
                raise ValueError('Duplicate selected reference')
            refs[x['case_id']] = x['reference_resolution']
    if set(refs) != wanted:
        raise ValueError('Selected cases missing references')
    predictions = indexed(a.predictions)
    if set(predictions) - wanted:
        raise ValueError('Predictions contain IDs outside the selected partition')
    info = {x['case_id']: x for x in records(a.inputs) if x['case_id'] in wanted}
    rows = []
    for key in ids:
        p = predictions.get(key, {})
        status = p.get('status', 'missing')
        # Error/abstention records are failures, even if they contain stale code.
        code = (
            p.get("resolution", p.get("final_resolution", p.get("initial_resolution")))
            if status == "ok"
            else None
        )
        row = dict(case_id=key, difficulty=info[key]['difficulty'], status=status,
                   latency_seconds=p.get('latency_seconds'),
                   reference_compile_pass=not bool(compile_error(refs[key])),
                   reference_ast_parseable=tree(refs[key]) is not None,
                   error=p.get('error', ''))
        row.update(metrics(code, refs[key]))
        rows.append(row)
    def summarize(items):
        n = len(items)
        result = dict(total=n, statuses=dict(collections.Counter(r['status'] for r in items)))
        for metric in ('exact_match', 'ast_match', 'compile_pass', 'marker_free'):
            count = sum(r[metric] for r in items)
            result[metric] = dict(passed=count, denominator=n, rate=count/n if n else None)
        result['reference_compile_failures'] = sum(not r['reference_compile_pass'] for r in items)
        times = [r['latency_seconds'] for r in items if isinstance(r['latency_seconds'], (float, int))]
        result['mean_latency_seconds'] = sum(times)/len(times) if times else None
        result['latency_record_count'] = len(times)
        return result
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with (out/'per_case.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = dict(overall=summarize(rows),
                   by_difficulty={d: summarize([r for r in rows if r['difficulty'] == d]) for d in sorted({r['difficulty'] for r in rows})},
                   inputs_sha256=digest(a.inputs), references_sha256=digest(a.references),
                   predictions_sha256=digest(a.predictions), manifest_sha256=digest(a.manifest),
                   python=platform.python_version(), split=a.split,
                   policy='All selected cases in denominators, including missing/errors/abstentions. Exact match normalizes line endings only. Invalid references flagged, never silently excluded. AST match is not behavioral equivalence.')
    write_json(out/'summary.json', summary)
    print(json.dumps(summary['overall'], indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Create reproducible group-separated case manifest')
    p.add_argument('--inputs', default='data/model_inputs.jsonl')
    p.add_argument('--manifest', default='evaluation/cases.json')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--test-size', type=int, default=200)
    p.add_argument('--dev-size', type=int, default=50)
    p.set_defaults(func=prepare)
    p = sub.add_parser('reserve', help='Preserve existing IDs and add unused merge groups')
    p.add_argument('--inputs', default='data/model_inputs.jsonl')
    p.add_argument('--manifest', default='evaluation/cases.json')
    p.add_argument('--output-manifest', default='evaluation/cases_with_unseen.json')
    p.set_defaults(func=reserve)
    for command, func in [('generate', generate), ('score', score)]:
        p = sub.add_parser(command)
        p.add_argument('--inputs', default='data/model_inputs.jsonl')
        p.add_argument('--manifest', default='evaluation/cases.json')
        p.add_argument('--split', choices=['smoke', 'dev', 'test', 'unseen'], default='smoke')
        if command == 'generate':
            p.add_argument('--client', default='src.resolver.ollama_client')
            p.add_argument('--mode', choices=['conflict_only', 'commit_context'], default='conflict_only')
            p.add_argument('--output', required=True)
            p.add_argument('--timeout', type=int, default=300)
            p.add_argument('--resume', action='store_true')
            p.add_argument('--retries', type=int, default=2, help='Extra attempts per failed case')
            p.add_argument('--retry-wait', type=int, default=30, help='Seconds before the first retry; grows with each attempt')
        else:
            p.add_argument('--references', default='data/evaluation_references.jsonl')
            p.add_argument('--predictions', required=True)
            p.add_argument('--out-dir', required=True)
        p.set_defaults(func=func)
    a = parser.parse_args()
    try:
        a.func(a)
    except (ValueError, OSError, KeyError, ImportError) as exc:
        parser.exit(1, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
