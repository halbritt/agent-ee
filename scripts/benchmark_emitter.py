#!/usr/bin/env python3
"""Record pod-scale emission latency and native netlist parity; no CI timing gate.

Stock libraries are read at runtime and pinned by SHA-256 in the capture
manifest. Only this original synthetic connectivity is retained in the repo.
"""
import argparse
import hashlib
import json
import platform
import socket
import subprocess
import time
from pathlib import Path

from agent_ee.checks import parse_xml_netlist
from agent_ee.connectivity_ir import resolve_intent
from agent_ee.executor import execute
from agent_ee.schematic_emitter import emit_schematic
from agent_ee.symbol_capture import capture_symbols


PARTS = [('U1', 'RF_Module:ESP32-S3-WROOM-1'), ('U2', 'Interface_CAN_LIN:TJA1051T-3'),
         ('U3', 'Sensor_Motion:LSM6DSM'), ('U4', 'RF_GPS:NEO-M8N'),
         ('U5', 'Analog_ADC:MCP3204'), ('U6', 'Regulator_Switching:TPS5430DDA'),
         ('U7', 'Regulator_Switching:TPS62933')]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def workload(library_root, out):
    selections = {}
    for _, lib_id in PARTS + [('J', 'Connector_Generic:Conn_01x40'), ('C', 'Device:C')]:
        library, symbol = lib_id.split(':')
        selections.setdefault(library, set()).add(symbol)
    libraries, captured, receipts = [], {}, []
    for library, symbols in sorted(selections.items()):
        path = library + '.kicad_sym'
        source = (library_root / path).read_bytes()
        subset, receipt = capture_symbols(source, sorted(symbols), library=library, source_file=path)
        libraries.append(dict(name=library, path=path, sha256=sha(subset)))
        captured[path] = subset
        receipts.append(receipt)
        (out / 'libraries' / path).write_bytes(subset)
    components = [dict(id=ref, reference=ref, lib_id=lib_id, value=lib_id.split(':')[1],
                       footprint='', units=[1]) for ref, lib_id in PARTS]
    probe = dict(schema='agent-ee.connectivity-intent/1', libraries=libraries,
                 components=components, nets=[], no_connects=[])
    inventory = resolve_intent(probe, captured)
    if inventory['execution'] != 'ok' or len(inventory['components']) != len(PARTS):
        raise ValueError('benchmark IC inventory unavailable: ' + json.dumps(inventory['findings']))
    groups, no_connects = [], []
    for comp in inventory['components']:
        stacks = {}
        for pin in comp['pins']:
            if pin['electrical_type'] == 'no_connect':
                no_connects.append(dict(component=comp['id'], pin=dict(number=pin['number'])))
                continue
            stacks.setdefault((pin['unit'], pin['x_nm'], pin['y_nm']), []).append(pin['number'])
        groups.extend((comp['id'], sorted(numbers)) for numbers in stacks.values())
    connectors = (len(groups) + 39) // 40
    components += [dict(id=f'J{i}', reference=f'J{i}', lib_id='Connector_Generic:Conn_01x40',
                        value='Conn_01x40', footprint='', units=[1]) for i in range(1, connectors + 1)]
    nets = []
    def terminal(ref, pin):
        return dict(component=ref, pin=dict(number=pin))
    for index, (ref, numbers) in enumerate(groups):
        nets.append(dict(id=f'n{index}', name=f'SIG_{ref}_{numbers[0]}',
                         terminals=[terminal(ref, pin) for pin in numbers] +
                                   [terminal(f'J{index // 40 + 1}', str(index % 40 + 1))]))
    no_connects += [terminal(f'J{i // 40 + 1}', str(i % 40 + 1))
                   for i in range(len(groups), connectors * 40)]
    capacitors = 130 - len(components)
    components += [dict(id=f'C{i}', reference=f'C{i}', lib_id='Device:C', value='100n',
                        footprint='', units=[1]) for i in range(1, capacitors + 1)]
    nets += [dict(id=name.lower(), name=name, terminals=[terminal(f'C{i}', str(pin))
             for i in range(1, capacitors + 1)]) for pin, name in ((1, 'VCC'), (2, 'GND'))]
    intent = dict(schema='agent-ee.connectivity-intent/1', libraries=libraries,
                  components=components, nets=nets, no_connects=no_connects)
    write_json(out / 'subset-manifest.json', receipts)
    write_json(out / 'intent.json', intent)
    return intent, captured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--libraries', type=Path, default=Path('/usr/share/kicad/symbols'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('repeats must be between 1 and 10')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / 'libraries').mkdir()
    started = time.perf_counter()
    intent, captured = workload(args.libraries, args.out)
    capture_seconds = time.perf_counter() - started
    layout = dict(schema='agent-ee.schematic-layout/1', namespace='6f0f7f0e-5c43-5b8e-9a51-2a3f2c1d0e40',
                  project='pod')
    write_json(args.out / 'layout.json', layout)
    samples, previous = [], None
    for _ in range(args.repeats):
        started = time.perf_counter()
        result, files = emit_schematic(intent, captured, layout)
        samples.append(time.perf_counter() - started)
        if result['execution'] != 'ok' or files is None:
            raise ValueError('emission failed: ' + json.dumps(result))
        if previous is not None and files != previous:
            raise ValueError('repeated emission changed bytes')
        previous = files
    candidate = args.out / 'candidate'
    candidate.mkdir()
    for path, data in files.items():
        (candidate / path).write_bytes(data)
    native = execute(candidate, args.out / 'native', ['kicad-cli', 'sch', 'export', 'netlist',
                     '--format', 'kicadxml', '-o', '/work/pod.xml', '/candidate/pod.kicad_sch'], timeout=120)
    native_path = args.out / 'native/pod.xml'
    if native['execution'] != 'ok' or native['exit_code'] != 0 or not native_path.is_file():
        raise ValueError('native netlist export failed: ' + json.dumps(native))
    parsed = parse_xml_netlist(native_path)
    expected = {'/' + net['name']: sorted(t['component'] + '.' + t['pin']['number'] for t in net['terminals'])
                for net in intent['nets']}
    connected = {name: pins for name, pins in parsed['nets'].items() if name in expected}
    unconnected = sorted(tuple(pins) for name, pins in parsed['nets'].items() if name not in expected)
    expected_nc = sorted((t['component'] + '.' + t['pin']['number'],) for t in intent['no_connects'])
    if (connected != expected or unconnected != expected_nc or
            set(parsed['components']) != {c['reference'] for c in intent['components']}):
        raise ValueError('native netlist disagrees with benchmark intent')
    record = dict(host=socket.gethostname(), platform=platform.platform(), python=platform.python_version(),
                  kicad_cli=subprocess.check_output(['kicad-cli', 'version'], text=True).strip(),
                  emission_implementation=result['implementation'], components=len(intent['components']),
                  nets=len(intent['nets']), capture_and_workload_seconds=capture_seconds,
                  emission_seconds=samples, maximum_seconds=max(samples), target_seconds=60,
                  below_target=max(samples) < 60, paper=result['paper'], native_components=len(parsed['components']),
                  native_nets_equal=True, native_no_connects_equal=True,
                  native_execution=native['execution'], native_exit=native['exit_code'],
                  files=result['files'], manifest_sha256=sha((args.out / 'subset-manifest.json').read_bytes()),
                  measurement='perf_counter wall seconds; full emit_schematic call; no warmup; capture excluded')
    write_json(args.out / 'benchmark.json', record)
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
