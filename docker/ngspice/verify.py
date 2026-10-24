#!/usr/bin/env python3
"""Check the installed simulator against independent switch-divider values."""
import argparse
import json
import math
import subprocess
import tempfile
from pathlib import Path


def verify(executable, directory):
    # Both threshold polarities and both resistance orderings. Every endpoint,
    # midpoint and outside-threshold value follows Ohm's law; no vendor model.
    cases = [(ron, roff, von, voff)
             for ron, roff in ((1e-3, 1e6), (1e6, 1e-6))
             for von, voff in ((1e-3, 0), (0, 1e-3))]
    models, circuit = [], ['* Switch voltage divider qualification',
                           f'.include {directory}/switches.lib',
                           'Vdrive drive 0 1', 'Vcontrol control 0 0']
    for i, (ron, roff, von, voff) in enumerate(cases):
        models += [f'.subckt switch{i} a b c', 'S1 a b c 0 device',
                   f'.model device VSWITCH Ron={ron:.17g} Roff={roff:.17g} '
                   f'Von={von:.17g} Voff={voff:.17g}', f'.ends switch{i}']
        circuit += [f'X{i} drive out{i} control switch{i}', f'R{i} out{i} 0 1']
    circuit += ['.dc Vcontrol -0.0005 0.0015 0.0005', '.end']
    (directory / 'switches.lib').write_text('\n'.join(models) + '\n')
    (directory / 'deck.cir').write_text('\n'.join(circuit) + '\n')
    outputs = ' '.join(f'v(out{i})' for i in range(len(cases)))
    (directory / 'control.sp').write_text(
        '* Switch qualification\n.control\nset ngbehavior=ps\nset numdgt=17\n'
        f'source {directory}/deck.cir\nrun\nset wr_singlescale\n'
        f'wrdata {directory}/values.tsv {outputs}\nquit\n.endc\n.end\n')
    result = subprocess.run([executable, '-n', '-b', str(directory / 'control.sp')],
                            capture_output=True, text=True, timeout=30)
    (directory / 'ngspice.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    rows = [[float(x) for x in line.split()]
            for line in (directory / 'values.tsv').read_text().splitlines()]
    if len(rows) != 5 or any(len(row) != 5 for row in rows):
        raise ValueError('incomplete switch qualification output')
    failures, measurements = [], []
    for index, row in enumerate(rows):
        control = (index - 1) * .0005
        if not math.isclose(row[0], control, abs_tol=1e-15):
            raise ValueError('unexpected control sweep')
        for i, (ron, roff, von, voff) in enumerate(cases):
            # At the midpoint the documented logarithmic switch interpolates
            # to the geometric mean resistance. The other points are endpoints.
            if index == 2:
                resistance = math.sqrt(ron * roff)
            else:
                on = control >= von if von > voff else control <= von
                resistance = ron if on else roff
            expected = 1 / (1 + resistance)
            observed = row[i + 1]
            passed = math.isfinite(observed) and math.isclose(
                observed, expected, rel_tol=1e-6, abs_tol=1e-12)
            item = dict(case=i, control_v=control, expected_v=expected,
                        observed_v=observed, passed=passed)
            measurements.append(item)
            if not passed:
                failures.append(item)
    report = dict(schema='agent-ee.ngspice-switch-qualification/1',
                  passed=not failures, measurements=measurements, failures=failures)
    (directory / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', default='/usr/bin/ngspice')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.out:
        args.out.mkdir(parents=True, exist_ok=False)
        report = verify(args.executable, args.out.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix='ngspice-verify-') as tmp:
            report = verify(args.executable, Path(tmp))
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
