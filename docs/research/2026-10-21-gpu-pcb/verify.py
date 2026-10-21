"""Validate committed evidence identities without CUDA or mutable repo imports."""
import ast
import gzip
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'results/run-manifest.json').read_text())
    for relative, expected in manifest['files_sha256'].items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'Artifact changed: {relative}')
    for source in (root / 'experiments').glob('*.py'):
        ast.parse(source.read_text(), filename=str(source))

    def read(name):
        return json.loads((root / name).read_text())

    def digest(name):
        return hashlib.sha256((root / name).read_bytes()).hexdigest()

    placement = read('results/placement-result.json')
    assert placement['input_sha256'] == digest('experiments/placement-input.json')
    assert all(r['all_scores_exact'] for r in placement['results'])
    assert all(len(t['samples_ms']) == 7 for r in placement['results']
               for t in r['timings'].values())
    for result, source in [('patterns-native-result.json', 'native-input.npz'),
                           ('patterns-pad-only-result.json', 'route-input.npz'),
                           ('prefix-result.json', 'native-input.npz')]:
        assert read('results/' + result)['input_sha256'] == digest('experiments/' + source)
    pattern = read('results/patterns-native-result.json')
    assert pattern['cuda_correctness']['passed']
    assert len(pattern['cuda_correctness']['cases']) == 5
    assert all(b['all_candidate_costs_equal'] and len(b['records']) == 7
               for b in pattern['batches'])
    assert all(q['status'] == 'found' for q in pattern['oracle'])
    assert sum(q['pattern_cost'] is None for q in pattern['oracle']) == 9
    assert read('results/prefix-result.json')['all_elements_equal']
    assert read('results/placement-python-result.json')['all_scores_exact']
    assert all(r['matches_eager_order'] for r in
               read('results/placement-lazy-result.json')['rows'])
    board = gzip.decompress((root / 'experiments/candidate.kicad_pcb.gz').read_bytes())
    assert hashlib.sha256(board).hexdigest() == read('experiments/native-input.json')['uncompressed_sha256']
    print('Evidence hashes, result links, correctness assertions and script syntax verified.')


if __name__ == '__main__':
    main()
