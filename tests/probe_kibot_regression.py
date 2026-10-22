"""Run the owned probe pipeline and the external fome-mc KiBot export contract.

Invoke with PYTHONPATH=src python3 -m tests.probe_kibot_regression --help.
The external executable/config are read only; all outputs stay under --scratch.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

from agent_ee import probe_features as pf
from agent_ee.scaffold import sexpr
from agent_ee.schematic_emit import parse_quoted, Quoted
from agent_ee.storage import canonical
from agent_ee.symbol_library import children
from tests.test_probe_features import NativeProbeTests, mixed_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--kibot', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    test = NativeProbeTests('test_native_six_features_schematic_board_and_fab')
    test.root = scratch / 'fixture'
    test.root.mkdir()
    test.parts, test.intent, test.refs, test.deviations = mixed_fixture(test, test.root)
    test.test_native_six_features_schematic_board_and_fab()
    project = test.candidate / 'project'
    probes = pf.declarations(test.parts, test.root)
    env = dict(os.environ, KICAD_CONFIG_HOME=str(scratch / 'kicad-config'))
    env.pop('PYTHONPATH', None)
    version = subprocess.run([str(args.kibot.resolve()), '--version'], env=env,
                             capture_output=True, text=True, check=True, timeout=30)
    print(version.stdout.strip(), flush=True)
    for label in ('legacy', 'encoded'):
        destination = scratch / label
        shutil.copytree(project, destination)
        for filename, kind in (('probe.kicad_pcb', 'footprint'), ('probe.kicad_sch', 'symbol')):
            path = destination / filename
            tree = parse_quoted(path.read_bytes())
            count = 0
            for node in children(tree, kind):
                for prop in children(node, 'property'):
                    if prop[1] == pf.PROPERTY:
                        record = pf.parse_marker(prop[2])
                        prop[2] = Quoted(canonical(record).decode() if label == 'legacy' else pf.marker(record))
                        count += 1
            assert count == 6, (filename, count)
            path.write_bytes(sexpr(tree))
        for filename, validate in (('probe.kicad_pcb', pf.validate_board),
                                   ('probe.kicad_sch', pf.validate_schematic)):
            data = (destination / filename).read_bytes()
            if label == 'legacy':
                with test.assertRaisesRegex(ValueError,
                        'legacy AGENT_EE_Probe property requires forward-annotation migration'):
                    validate(data, probes)
            else:
                assert validate(data, probes) == set(probes)
        command = [str(args.kibot.resolve()), '-A', '-c', str(args.config.resolve()),
                   '-e', str(destination / 'probe.kicad_sch'),
                   '-b', str(destination / 'probe.kicad_pcb'),
                   '-d', str(destination / 'export'), 'gerbers', 'drill', 'position', 'bom_csv']
        result = subprocess.run(command, cwd=destination, env=env, capture_output=True,
                                text=True, timeout=180)
        (scratch / (label + '.log')).write_text(result.stdout + result.stderr)
        print(label, command, 'exit', result.returncode, flush=True)
        # Since AGENTEE-444 Footprint precedes custom fields, so KiBot exports the legacy
        # inline JSON too; legacy markers are refused at admission (above), not by KiBot.
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Footprint with more than one colon' not in result.stdout + result.stderr
        for directory, pattern in (('gerbers', '*-F_Cu.gbr'), ('gerbers', '*-F_Mask.gbr'),
                                   ('gerbers', '*.drl'), ('assembly', '*.csv'), ('bom', '*.csv')):
            assert list((destination / 'export' / directory).glob(pattern)), (label, directory, pattern)
    print('PASS: legacy refused at admission; legacy and encoded KiBot exit 0 with copper, mask, '
          'drill, position and BOM outputs')


if __name__ == '__main__':
    main()
