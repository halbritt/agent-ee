"""Tests for AGENTEE-29 JLC assembly-readiness check (`src/agent_ee/assembly_readiness.py`).

Schematics are original fixtures generated here and loaded by the real
kicad-cli; components reach the check only through the native XML netlist,
exported inside the same sandbox the check uses.
"""

from datetime import datetime, timedelta, timezone
import contextlib
import copy
import hashlib
import io
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_ee import executor
from agent_ee.assembly_readiness import (
    AssemblyReadinessExecutionError,
    evaluate_assembly_readiness,
    campaign_stock_context,
    verify_stock_context,
    load_catalog_snapshot,
    read_netlist_components,
    verify_netlist_coverage,
    verify_schematic_files,
)
from agent_ee.candidate import capture
from agent_ee.capabilities import describe_check
from agent_ee.checks import run_checks, sheet_instances
from agent_ee.jlc_stock_capture import DEFAULT_ENDPOINT, capture_jlc_stock, ids_from_netlist
from agent_ee.storage import digest, read_json, write_json
from tests.test_jlc_stock_capture import JlcStub, row, search
from tests.toolchain import require_toolchain


HIDDEN = "(effects (font (size 1.27 1.27)) (hide yes))"
SHOWN = "(effects (font (size 1.27 1.27)))"
LIB_PINS = {"R": 2, "C": 2, "D": 2, "Q": 3, "J": 2, "MH": 1, "U": 3}


def _uuid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"agent-ee-ee29/{seed}"))


def _lib_symbol(name, pins, power=False):
    short = name.split(":", 1)[1]
    if power:
        pin_text = (
            '(pin power_in line (at 0 0 0) (length 0) hide (name "1" ' + SHOWN + ') (number "1" ' + SHOWN + "))"
        )
    else:
        pin_text = "".join(
            f'(pin passive line (at 0 {-2.54 * i:.2f} 0) (length 2.54) (name "{i + 1}" {SHOWN}) '
            f'(number "{i + 1}" {SHOWN}))'
            for i in range(pins)
        )
    return (
        f'(symbol "{name}" {"(power) " if power else ""}(pin_names (offset 0)) (in_bom yes) (on_board yes) '
        f'(property "Reference" "X" (at 0 0 0) {HIDDEN}) (property "Value" "{short}" (at 0 0 0) {HIDDEN}) '
        f'(symbol "{short}_1_1" {pin_text}))'
    )


LIB_SYMBOLS = "".join(
    [_lib_symbol(f"ee29:{name}", pins) for name, pins in LIB_PINS.items()]
    + [_lib_symbol("power:GND", 1, power=True), _lib_symbol("power:PWR_FLAG", 1, power=True)]
)


def part(ref, lib="R", *, value="10k", footprint="ee29:R_0603", lcsc="C25804", manufacturer="Example Mfr",
         part_number=("PartNumber", "PN-GENERIC"), rating=("Power", "0.1W"), extra=None,
         in_bom=True, on_board=True, dnp=False):
    """One placed symbol; ``None`` drops a field."""
    fields = [("Value", value)]
    for name, val in (("Footprint", footprint), ("LCSC", lcsc), ("Manufacturer", manufacturer)):
        if val is not None:
            fields.append((name, val))
    for pair in (part_number, rating):
        if pair is not None:
            fields.append(pair)
    fields.extend((extra or {}).items())
    return {"ref": ref, "lib": lib, "fields": fields, "in_bom": in_bom, "on_board": on_board, "dnp": dnp}


def power(ref, lib="power:GND", value="GND"):
    return {"ref": ref, "lib": lib, "fields": [("Value", value)], "in_bom": True, "on_board": True, "dnp": False}


def _placed(item, x, instance_refs):
    lib = item["lib"] if ":" in item["lib"] else f"ee29:{item['lib']}"
    pins = 1 if lib.startswith("power:") else LIB_PINS[item["lib"]]
    seed = f"{item['ref']}-{x}"
    props = "".join(
        f'(property "{k}" "{v}" (at 0 0 0) {HIDDEN})' for k, v in [("Reference", item["ref"])] + item["fields"]
    )
    pin_uuids = "".join(f'(pin "{i + 1}" (uuid "{_uuid(seed + str(i))}"))' for i in range(pins))
    paths = "".join(f'(path "{path}" (reference "{ref}") (unit 1))' for path, ref in instance_refs)
    yn = lambda flag: "yes" if flag else "no"  # noqa: E731
    return (
        f'(symbol (lib_id "{lib}") (at {x} 50 0) (unit 1) (in_bom {yn(item["in_bom"])}) '
        f'(on_board {yn(item["on_board"])}) (dnp {yn(item["dnp"])}) (uuid "{_uuid(seed)}") '
        f'{props}{pin_uuids}(instances (project "ee29" {paths})))'
    )


def schematic(name, parts, *, sheets=(), instance_paths=None):
    """KiCad 10 schematic text; ``instance_paths`` lists (path, block) for a reused child sheet (U1 -> U101)."""
    sheet_uuid = _uuid(f"sheet-{name}")
    body = []
    for index, item in enumerate(parts):
        if instance_paths is None:
            refs = [(f"/{sheet_uuid}", item["ref"])]
        else:
            refs = [(path, re.sub(r"^(\D+)(\d+)$", lambda m, b=block: f"{m[1]}{b}{int(m[2]):02d}", item["ref"]))
                    for path, block in instance_paths]
        body.append(_placed(item, 10 + 5 * index, refs))
    for index, (sheet_name, sheet_file, child_uuid, root_uuid) in enumerate(sheets):
        x = 100 + 20 * index
        body.append(
            f'(sheet (at {x} 20) (size 10 10) (fields_autoplaced yes) (stroke (width 0.1524) (type solid)) '
            f'(fill (color 0 0 0 0.0)) (uuid "{child_uuid}") '
            f'(property "Sheetname" "{sheet_name}" (at {x} 19 0) {SHOWN}) '
            f'(property "Sheetfile" "{sheet_file}" (at {x} 31 0) {SHOWN}) '
            f'(instances (project "ee29" (path "/{root_uuid}" (page "{index + 2}")))))'
        )
    return (
        f'(kicad_sch (version 20250114) (generator "eeschema") (uuid "{sheet_uuid}") (paper "A4") '
        f"(lib_symbols {LIB_SYMBOLS})\n" + "\n".join(body) + '\n(sheet_instances (path "/" (page "1"))))\n'
    )


def _write_sqlite_catalog(dir_path: Path, parts_rows, *, snapshot_date="2026-10-15", stale_before="2026-10-08"):
    db_path = dir_path / "catalog.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE component_base ("
            "lcsc TEXT PRIMARY KEY, mfr TEXT, mpn TEXT, package TEXT, category TEXT, "
            "stock INTEGER, price1 REAL, is_basic INTEGER, is_preferred INTEGER, "
            "last_seen TEXT, attributes TEXT, normalized TEXT, provenance TEXT)"
        )
        for row in parts_rows:
            conn.execute(
                "INSERT INTO component_base VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["lcsc"], row.get("mfr", "GenericMfr"), row.get("mpn", f"MPN-{row['lcsc']}"),
                    row.get("package", "0603"), row.get("category", "Passives"), row.get("stock", 500),
                    row.get("price1", 0.01), 1 if row.get("is_basic", True) else 0,
                    1 if row.get("is_preferred", False) else 0, row.get("last_seen", snapshot_date),
                    "{}", "{}", "{}",
                ),
            )
        conn.commit()
    finally:
        conn.close()
    write_json(dir_path / "catalog-manifest.json", {
        "schema": "agent-ee.catalog/1",
        "snapshot_date": snapshot_date,
        "stale_before": stale_before,
        "database": {"path": "catalog.sqlite", "sha256": digest(db_path), "size_bytes": db_path.stat().st_size},
    })


class AssemblyReadinessTests(unittest.TestCase):
    def setUp(self):
        require_toolchain(tools=("kicad-cli",))
        self.tmp = tempfile.TemporaryDirectory(prefix="agent-ee-ee29-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exports = 0
        self.roots = {}

    def catalog(self, parts, *, snapshot_date="2026-10-15", stale_before="2026-10-08", name="catalog.json"):
        doc = {"schema": "agent-ee.catalog-snapshot/1", "snapshot_date": snapshot_date, "parts": parts}
        if stale_before is not None:
            doc["stale_before"] = stale_before
        write_json(self.root / name, doc)
        return name

    def export(self, name, text, children=()):
        """Write a schematic (plus child sheets) and export its native XML netlist in the sandbox."""
        (self.root / name).write_text(text, encoding="utf-8")
        for child_name, child_text in children:
            (self.root / child_name).write_text(child_text, encoding="utf-8")
        self.exports += 1
        work = self.root.parent / f"{self.root.name}-work{self.exports}"
        work.mkdir()
        self.addCleanup(shutil.rmtree, work, True)
        record = executor.execute(
            self.root, work,
            ["kicad-cli", "sch", "export", "netlist", "--format", "kicadxml", "-o", "/work/design.xml",
             f"/candidate/{name}"],
            timeout=120,
        )
        self.assertEqual(record["execution"], "ok", record)
        self.roots[work / "design.xml"] = name
        return work / "design.xml"

    def evaluate(self, netlist, params):
        """Evaluate as the check does: exported netlists are bound to their captured hierarchy."""
        if netlist not in self.roots:
            return evaluate_assembly_readiness(self.root, netlist, params)
        files, instances, missing = sheet_instances(self.root, self.roots[netlist])
        self.assertEqual(missing, [])
        return evaluate_assembly_readiness(self.root, netlist, params, expected_sheets=instances,
                                           hierarchy_files=files)

    # ------------------------------------------------------------------ LCSC binding
    def handfit(self, mpn='776228-1', assembly='consigned', lcsc=None):
        return {'requirement_id': 'POD-AC-5', 'original': {'mpn': mpn, 'lcsc': lcsc},
                'assembly': assembly, 'reason': 'Hand-fitted after delivery per captured decision.',
                'purchase_source': 'Original fixture supplier purchase list, line 1',
                'stock_deviation': {'reason': 'Not offered for JLC assembly in this synthetic capture.'},
                'evidence_refs': [{'path': 'purchase.txt', 'locator': 'line 1'}]}

    def handfit_case(self, declarations=None, *, lcsc='NO_LCSC', marker='not_assembled', stock=None):
        (self.root / 'purchase.txt').write_text('Original synthetic procurement declaration; not a live quote.\n')
        declarations = [self.handfit()] if declarations is None else declarations
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1',
                                                  'deviations': declarations})
        rows = [{'lcsc': 'C25804', 'stock': 1000, 'is_basic': True, 'last_seen': '2026-10-15'}]
        if stock is not None:
            rows.append({'lcsc': 'C90099', 'mpn': '776228-1', 'stock': stock,
                         'is_basic': False, 'last_seen': '2026-10-15'})
        cat = self.catalog(rows)
        netlist = self.export('pod.kicad_sch', schematic('pod', [part('R1'),
            part('J1', 'J', lcsc=lcsc, part_number=('Partnumber', '776228-1'), rating=None,
                 extra={'JLCPCB_Assembly': marker} if marker is not None else {})]))
        return netlist, {'catalog': cat, 'deviations': 'deviations.json'}

    def test_consigned_deviation_listed(self):
        netlist, params = self.handfit_case()
        report = self.evaluate(netlist, params)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(report['parts']['C25804']['references'], ['R1'])
        self.assertEqual(report['parts']['C25804']['stock_state'], 'ok')
        self.assertEqual(report['counts']['unique_lcsc'], 1)
        self.assertEqual(report['counts']['declared_deviation'], 1)
        entry = report['declared_deviation'][0]
        self.assertEqual((entry['reference'], entry['mpn'], entry['assembly']), ('J1', '776228-1', 'consigned'))
        self.assertEqual(entry['stock_state'], 'declared_deviation')
        self.assertEqual(entry['deviations_sha256'], digest(self.root / 'deviations.json'))
        self.assertEqual(entry['evidence'][0]['sha256'], digest(self.root / 'purchase.txt'))
        self.assertEqual(report['invalid_lcsc'], {})
        self.assertIn('1 declared hand-fit deviation', report['summary'])
        # The exemption cannot hide a defect in the remaining BOM.
        self.catalog([{'lcsc': 'C25804', 'stock': 0, 'is_basic': True, 'last_seen': '2026-10-15'}])
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['findings'][0]['code'], 'out_of_stock')

    def test_undeclared_no_lcsc_defect(self):
        netlist, params = self.handfit_case([])
        for configured in (params, {'catalog': params['catalog']}):
            with self.subTest(configured=configured):
                report = self.evaluate(netlist, configured)
                self.assertEqual(report['outcome'], 'defect')
                self.assertEqual(report['declared_deviation'], [])
                self.assertEqual(report['invalid_lcsc'], {'J1': 'NO_LCSC'})
                self.assertIn('undeclared_assembly_marker', [f['code'] for f in report['findings']])
        netlist, params = self.handfit_case([], marker=None)
        self.assertEqual(self.evaluate(netlist, params)['outcome'], 'defect')

    def test_deviation_for_stocked_part_refused(self):
        for lcsc in ('NO_LCSC', 'C90099'):
            with self.subTest(lcsc=lcsc):
                netlist, params = self.handfit_case(lcsc=lcsc, stock=10)
                report = self.evaluate(netlist, params)
                self.assertEqual(report['outcome'], 'defect')
                self.assertEqual(report['declared_deviation'], [])
                self.assertIn('stocked part refused', report['findings'][0]['message'])

    def test_handfit_missing_source_reason_evidence_identity_or_marker_refused(self):
        mutations = [lambda d: d.pop('purchase_source'), lambda d: d.update(purchase_source='  '),
                     lambda d: d.update(stock_deviation={'reason': ''}), lambda d: d.update(reason=''),
                     lambda d: d.update(evidence_refs=[]),
                     lambda d: d.update(evidence_refs=[{'path': 'absent', 'locator': 'x'}]),
                     lambda d: d.update(evidence_refs=[{'path': '../escape', 'locator': 'x'}]),
                     lambda d: d.update(original={'mpn': 'OTHER', 'lcsc': None}),
                     lambda d: d.update(assembly='jlc_assembled'),
                     lambda d: d.update(replacement={'mpn': 'OTHER', 'lcsc': None})]
        for mutate in mutations:
            declaration = self.handfit()
            mutate(declaration)
            with self.subTest(declaration=declaration):
                netlist, params = self.handfit_case([declaration])
                report = self.evaluate(netlist, params)
                self.assertEqual(report['outcome'], 'defect')
                self.assertEqual(report['declared_deviation'], [])
        for marker in (None, 'assembled', 'consigned'):
            netlist, params = self.handfit_case(marker=marker)
            report = self.evaluate(netlist, params)
            self.assertEqual(report['outcome'], 'defect')
            self.assertEqual(report['declared_deviation'], [])

    def test_duplicate_deviation_is_defect_and_missing_capture_never_passes(self):
        netlist, params = self.handfit_case([self.handfit(), self.handfit()])
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertIn('duplicate assembly deviation', report['findings'][0]['message'])
        (self.root / 'deviations.json').write_text('{')
        with self.assertRaises(AssemblyReadinessExecutionError):
            self.evaluate(netlist, params)
        (self.root / 'deviations.json').unlink()
        with self.assertRaises(AssemblyReadinessExecutionError):
            self.evaluate(netlist, params)

    def test_stocked_native_id_cannot_hide_behind_a_different_catalog_mpn(self):
        netlist, params = self.handfit_case(lcsc='C25804')
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['declared_deviation'], [])
        self.assertIn('stocked part refused', report['findings'][0]['message'])

    def test_unknown_stock_and_failed_jlc_observation_cannot_be_exempted(self):
        netlist, params = self.handfit_case(lcsc='C90099', stock=0)
        doc = read_json(self.root / 'catalog.json')
        doc['parts'][-1]['stock'] = None
        write_json(self.root / 'catalog.json', doc)
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['declared_deviation'], [])
        doc = self.jlc_capture(self.root, {'C25804': (200, search(row('C25804', 500))),
                                          'C90099': (503, b'busy')}, ['C25804', 'C90099'])
        params.update(catalog='jlc/catalog.json', as_of=self.shifted(doc['as_of'], 1), max_age_hours=12)
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'failed requests'):
            self.evaluate(netlist, params)

    def test_jlc_raw_capture_controls_deviation_for_zero_not_found_and_positive_stock(self):
        netlist, params = self.handfit_case(lcsc='C90099')
        for index, stock in enumerate((0, None, 10)):
            with self.subTest(stock=stock):
                parent = self.root / f'capture-{index}'
                parent.mkdir()
                response = search() if stock is None else search(row('C90099', stock, componentModelEn='776228-1'))
                doc = self.jlc_capture(parent, {'C25804': (200, search(row('C25804', 500))),
                                               'C90099': (200, response)}, ['C25804', 'C90099'])
                params.update(catalog=f'capture-{index}/jlc/catalog.json',
                              as_of=self.shifted(doc['as_of'], 1), max_age_hours=12)
                report = self.evaluate(netlist, params)
                self.assertEqual(report['outcome'], 'defect' if stock else 'pass', report)
                self.assertEqual(len(report['declared_deviation']), 0 if stock else 1)
                if stock:
                    self.assertIn('stocked part refused', report['findings'][0]['message'])
                    self.assertEqual(report['parts']['C90099']['references'], ['J1'])
                else:
                    self.assertNotIn('C90099', report['parts'])

    def test_deviations_must_be_captured_and_native_assembly_fields_agree(self):
        from agent_ee.assembly_readiness import capability
        netlist, params = self.handfit_case()
        with self.assertRaisesRegex(ValueError, 'captured input'):
            capability(params, {'catalog.json': {}}, self.root)
        desc = capability(params, {'catalog.json': {}, 'deviations.json': {}}, self.root)
        self.assertNotIn('deviations.json', desc['immutable_inputs'])
        # The exact marker and sourcing fields cannot use a conflicting XML representation.
        import xml.etree.ElementTree as ET
        tree = ET.parse(netlist)
        comp = tree.find("./components/comp[@ref='J1']")
        prop = comp.find("property[@name='JLCPCB_Assembly']")
        if prop is None:
            prop = ET.SubElement(comp, 'property', name='JLCPCB_Assembly')
        prop.set('value', 'assembled')
        tree.write(netlist)
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'contradictory native property'):
            self.evaluate(netlist, params)

    def test_neo_and_sma_hand_soldered_without_lcsc(self):
        declarations = [self.handfit(mpn, 'hand_soldered') for mpn in ('NEO-M9N', 'OWNED-SMA')]
        netlist, params = self.handfit_case(declarations)
        netlist = self.export('gnss.kicad_sch', schematic('gnss', [part('R1')] + [
            part(ref, lib, lcsc=None, part_number=('Partnumber', mpn), rating=None,
                 extra={'JLCPCB_Assembly': 'not_assembled'})
            for ref, lib, mpn in [('U1', 'U', 'NEO-M9N'), ('J1', 'J', 'OWNED-SMA')]]))
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'pass', report)
        self.assertEqual({d['mpn'] for d in report['declared_deviation']}, {'NEO-M9N', 'OWNED-SMA'})
        self.assertEqual(report['missing_lcsc_refs'], [])

    # ------------------------------------------------------------------ owner hand-fit policy (AGENTEE-239)
    DECISION = ('Owned synthetic owner decision record; not a copy of any project specification.\n'
                'Decision 7: the 776228-1 header is hand-fitted after delivery whatever JLCPCB stocks.\n')
    QUOTE = 'the 776228-1 header is hand-fitted after delivery whatever JLCPCB stocks.'

    def policy_case(self, *, stock=260, policy=True, identities=None, lcsc='C90099', last_seen='2026-10-15'):
        """J1-like exact identity with positive captured stock, declared under the cited owner decision."""
        declaration = self.handfit(lcsc=lcsc)
        declaration['stock_deviation'] = {'reason': 'Owner hand-fit policy; not a stock exception.'}
        if policy:
            declaration['policy'] = 'owner_hand_fit'
        netlist, params = self.handfit_case([declaration], lcsc=lcsc or 'NO_LCSC', stock=stock)
        if last_seen != '2026-10-15':
            doc = read_json(self.root / 'catalog.json')
            doc['parts'][-1]['last_seen'] = last_seen
            write_json(self.root / 'catalog.json', doc)
        (self.root / 'decision.md').write_text(self.DECISION)
        params['hand_fit_policy'] = {
            'source': 'decision.md', 'sha256': digest(self.root / 'decision.md'), 'line': 2, 'quote': self.QUOTE,
            'identities': [{'mpn': '776228-1', 'lcsc': 'C90099'}] if identities is None else identities}
        return netlist, params

    def test_owner_hand_fit_policy_admits_stocked_exact_identity(self):
        for stock in (260, 0):
            with self.subTest(stock=stock):
                netlist, params = self.policy_case(stock=stock)
                report = self.evaluate(netlist, params)
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
                entry, = report['declared_deviation']
                self.assertEqual((entry['reference'], entry['mpn'], entry['lcsc'], entry['stock_state'],
                                  entry['policy']), ('J1', '776228-1', 'C90099', 'owner_hand_fit', 'owner_hand_fit'))
                self.assertEqual(entry['catalog_observation']['stock'], stock)
                self.assertEqual(entry['policy_citation'], {'source': 'decision.md', 'line': 2, 'quote': self.QUOTE,
                                                            'sha256': digest(self.root / 'decision.md')})
                self.assertEqual(report['hand_fit_policy']['identities'], [{'mpn': '776228-1', 'lcsc': 'C90099'}])
                self.assertNotIn('C90099', report['parts'])
                self.assertEqual(report['parts']['C25804']['references'], ['R1'])
                self.assertEqual({c['reference']: c['lcsc_status'] for c in report['components']}['J1'],
                                 'declared_deviation')

    def test_stocked_identity_without_policy_citation_is_refused(self):
        # The deviation omits the class: today's stock-unavailable refusal applies unchanged.
        netlist, params = self.policy_case(policy=False)
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['declared_deviation'], [])
        self.assertIn('stocked part refused', report['findings'][0]['message'])
        # The deviations file alone cannot assert the class without the scope's captured citation.
        netlist, params = self.policy_case()
        del params['hand_fit_policy']
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['declared_deviation'], [])
        self.assertIn('cannot assert it', report['findings'][0]['message'])
        self.assertIsNone(report['hand_fit_policy'])

    def test_policy_for_an_identity_the_decision_does_not_name_is_refused(self):
        for identities in ([{'mpn': '776228-1', 'lcsc': 'C90100'}], [{'mpn': '776228-1', 'lcsc': None}]):
            with self.subTest(identities=identities):
                netlist, params = self.policy_case(identities=identities)
                report = self.evaluate(netlist, params)
                self.assertEqual(report['outcome'], 'defect')
                self.assertEqual(report['declared_deviation'], [])
                self.assertIn('not named by the owner hand-fit policy', report['findings'][0]['message'])
        # A scope citation cannot list an identity its exact quote does not name.
        netlist, params = self.policy_case(identities=[{'mpn': 'NEO-M9N', 'lcsc': None}])
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'does not name') as caught:
            self.evaluate(netlist, params)
        self.assertEqual(caught.exception.execution, 'unsupported')

    def test_neo_like_stocked_part_claiming_unavailability_still_refused_with_policy_configured(self):
        declaration = self.handfit('NEO-M9N', 'hand_soldered', lcsc='C90098')
        netlist, params = self.policy_case()
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [declaration]})
        doc = read_json(self.root / 'catalog.json')
        doc['parts'].append({'lcsc': 'C90098', 'mpn': 'NEO-M9N', 'stock': 40, 'is_basic': False,
                             'last_seen': '2026-10-15'})
        write_json(self.root / 'catalog.json', doc)
        netlist = self.export('neo.kicad_sch', schematic('neo', [part('R1'),
            part('U1', 'U', lcsc='C90098', part_number=('Partnumber', 'NEO-M9N'), rating=None,
                 extra={'JLCPCB_Assembly': 'not_assembled'})]))
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['declared_deviation'], [])
        self.assertIn('stocked part refused', report['findings'][0]['message'])
        declaration['policy'] = 'owner_hand_fit'
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [declaration]})
        report = self.evaluate(netlist, params)
        self.assertEqual(report['declared_deviation'], [])
        self.assertIn('not named by the owner hand-fit policy', report['findings'][0]['message'])

    def test_policy_still_requires_marker_fresh_known_stock_exact_catalog_identity_and_supported_class(self):
        netlist, params = self.policy_case()
        declarations = read_json(self.root / 'deviations.json')
        for name, mutate, message in (
                ('unsupported class', lambda d: d.update(policy='owner_any'), 'unsupported assembly deviation policy'),
                ('no purchase source', lambda d: d.update(purchase_source=' '), 'nonempty text'),
                ('no evidence', lambda d: d.update(evidence_refs=[]), 'captured evidence')):
            with self.subTest(name):
                declaration = copy.deepcopy(declarations['deviations'][0])
                mutate(declaration)
                write_json(self.root / 'deviations.json', {**declarations, 'deviations': [declaration]})
                report = self.evaluate(netlist, params)
                self.assertEqual((report['outcome'], report['declared_deviation']), ('defect', []))
                self.assertIn(message, report['findings'][0]['message'])
        for name, kwargs, message in (('null stock', {'stock': 0}, 'unknown stock telemetry'),
                                      ('stale stock', {'last_seen': '2026-10-01'}, 'stale or unverified'),
                                      ('no catalog row', {'lcsc': 'C90100', 'identities': [
                                          {'mpn': '776228-1', 'lcsc': 'C90100'}]}, 'no captured catalog')):
            with self.subTest(name):
                netlist, params = self.policy_case(**kwargs)
                if name == 'null stock':
                    doc = read_json(self.root / 'catalog.json')
                    doc['parts'][-1]['stock'] = None
                    write_json(self.root / 'catalog.json', doc)
                report = self.evaluate(netlist, params)
                self.assertEqual((report['outcome'], report['declared_deviation']), ('defect', []))
                self.assertIn(message, report['findings'][0]['message'])
        netlist, params = self.policy_case()
        doc = read_json(self.root / 'catalog.json')
        doc['parts'][-1]['mpn'] = '776228-2'
        write_json(self.root / 'catalog.json', doc)
        report = self.evaluate(netlist, params)
        self.assertIn('differs from the policy identity', report['findings'][0]['message'])
        # The marker is required and an unlisted lcsc-less identity cannot cover a catalog MPN.
        netlist, params = self.policy_case()
        netlist = self.export('unmarked.kicad_sch', schematic('unmarked', [part('R1'),
            part('J1', 'J', lcsc='C90099', part_number=('Partnumber', '776228-1'), rating=None)]))
        report = self.evaluate(netlist, params)
        self.assertEqual((report['outcome'], report['declared_deviation']), ('defect', []))
        self.assertIn('JLCPCB_Assembly=not_assembled', report['findings'][0]['message'])
        netlist, params = self.policy_case(lcsc=None, identities=[{'mpn': '776228-1', 'lcsc': None}])
        report = self.evaluate(netlist, params)
        self.assertEqual((report['outcome'], report['declared_deviation']), ('defect', []))
        self.assertIn('must name its LCSC', report['findings'][0]['message'])

    def test_policy_citation_is_pinned_captured_immutable_and_exact(self):
        from agent_ee.assembly_readiness import capability
        netlist, params = self.policy_case()
        files = {'catalog.json': {}, 'deviations.json': {}}
        with self.assertRaisesRegex(ValueError, 'hand_fit_policy.source must be a captured input'):
            capability(params, files, self.root)
        desc = capability(params, {**files, 'decision.md': {}}, self.root)
        self.assertIn('decision.md', desc['immutable_inputs'])
        self.assertNotIn('deviations.json', desc['immutable_inputs'])
        for name, change, execution, message in (
                ('hash', {'sha256': '0' * 64}, 'failed', 'sha256 mismatch'),
                ('line', {'line': 1}, 'failed', 'quote is absent'),
                ('past end', {'line': 9}, 'failed', 'quote is absent'),
                ('paraphrase', {'quote': 'the 776228-1 header is hand-fitted.'}, 'failed', 'quote is absent'),
                ('mutable source', {'source': 'deviations.json'}, 'unsupported', 'mutable deviations'),
                ('missing source', {'source': 'absent.md'}, 'failed', 'missing hand_fit_policy source'),
                ('extra key', {'class': 'owner_hand_fit'}, 'unsupported', 'expected fields'),
                ('duplicate', {'identities': [{'mpn': '776228-1', 'lcsc': 'C90099'}] * 2}, 'unsupported',
                 'duplicate'),
                ('empty', {'identities': []}, 'unsupported', 'at least one'),
                ('multi-line quote', {'quote': self.QUOTE + '\nmore'}, 'unsupported', 'one source line')):
            with self.subTest(name):
                changed = dict(params, hand_fit_policy={**params['hand_fit_policy'], **change})
                with self.assertRaisesRegex(AssemblyReadinessExecutionError, message) as caught:
                    self.evaluate(netlist, changed)
                self.assertEqual(caught.exception.execution, execution)
        (self.root / 'decision.md').write_text(self.DECISION.replace('whatever', 'regardless of what'))
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'sha256 mismatch'):
            self.evaluate(netlist, params)

    SPEC = Path(__file__).resolve().parents[1] / 'fixtures' / 'telemetry-pod' / 'spec.md'
    SPEC_QUOTE = ('Parts JLCPCB does not stock or assemble (the 776228-1 header, and the NEO-M9N or SMA '
                  'connector if unstocked) are hand-fitted after delivery')

    def spec_citation(self, params, quote, identities):
        shutil.copyfile(self.SPEC, self.root / 'spec.md')
        params['hand_fit_policy'] = {'source': 'spec.md', 'sha256': digest(self.root / 'spec.md'), 'line': 76,
                                     'quote': quote, 'identities': identities}

    def test_canonical_spec_line_admits_unconditional_header_whatever_the_quote_slice(self):
        # Acceptance criterion 5 names the header unconditionally; a truncated quote cannot change the outcome.
        for quote in (self.SPEC_QUOTE, 'the 776228-1 header', 'Parts JLCPCB does not stock or assemble (the 776228-1'):
            with self.subTest(quote=quote):
                netlist, params = self.policy_case()
                self.spec_citation(params, quote, [{'mpn': '776228-1', 'lcsc': 'C90099'}])
                report = self.evaluate(netlist, params)
                self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
                self.assertEqual(report['declared_deviation'][0]['stock_state'], 'owner_hand_fit')
                self.assertEqual(report['hand_fit_policy']['line'], 76)

    def test_conditionally_named_spec_identity_cannot_use_owner_hand_fit(self):
        # The same line names NEO-M9N and SMA only "if unstocked": full and truncated quotes both fail closed.
        netlist, params = self.policy_case()
        doc = read_json(self.root / 'catalog.json')
        doc['parts'].append({'lcsc': 'C90098', 'mpn': 'NEO-M9N', 'stock': 40, 'is_basic': False,
                             'last_seen': '2026-10-15'})
        write_json(self.root / 'catalog.json', doc)
        declaration = self.handfit('NEO-M9N', 'hand_soldered', lcsc='C90098')
        declaration['policy'] = 'owner_hand_fit'
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [declaration]})
        netlist = self.export('neo_policy.kicad_sch', schematic('neo_policy', [part('R1'),
            part('U1', 'U', lcsc='C90098', part_number=('Partnumber', 'NEO-M9N'), rating=None,
                 extra={'JLCPCB_Assembly': 'not_assembled'})]))
        neo = {'mpn': 'NEO-M9N', 'lcsc': 'C90098'}
        for quote, identities in (
                (self.SPEC_QUOTE, [neo]),
                ('Parts JLCPCB does not stock or assemble (the 776228-1 header, and the NEO-M9N', [neo]),
                ('the NEO-M9N', [neo]),
                (self.SPEC_QUOTE, [{'mpn': '776228-1', 'lcsc': 'C90099'}, neo]),
                (self.SPEC_QUOTE, [{'mpn': 'SMA', 'lcsc': None}])):
            with self.subTest(quote=quote, identities=identities):
                self.spec_citation(params, quote, identities)
                with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'named only conditionally') as caught:
                    self.evaluate(netlist, params)
                self.assertEqual(caught.exception.execution, 'failed')
        # Without the class it is the stock-unavailable declaration, and positive stock still refuses it.
        del declaration['policy']
        write_json(self.root / 'deviations.json', {'schema': 'agent-ee.part-deviations/1', 'deviations': [declaration]})
        self.spec_citation(params, self.SPEC_QUOTE, [{'mpn': '776228-1', 'lcsc': 'C90099'}])
        report = self.evaluate(netlist, params)
        self.assertEqual((report['outcome'], report['declared_deviation']), ('defect', []))
        self.assertIn('stocked part refused', report['findings'][0]['message'])

    def test_policy_statement_with_unbounded_or_ambiguous_condition_is_refused(self):
        header = 'the 776228-1 header'
        for name, text, message in (
                ('leading condition', f'If JLCPCB lists no stock, {header} is hand-fitted.', "qualifier 'If'"),
                ('trailing condition', f'The 776228-1 header is hand-fitted, unless JLCPCB stocks it.', 'unless'),
                ('predicate conjunct', f'Parts ({header}, and hand-fitted only if unavailable) are hand-fitted.',
                 "qualifier 'only'"),
                ('paragraph above', f'When JLCPCB lists no stock,\n{header} is hand-fitted.', "qualifier 'When'"),
                ('list lead-in', f'Hand-fitted parts:\n\n- the X-1 part.\n- {header} is hand-fitted.', 'lead-in'),
                ('nested item', f'- Out of stock parts\n  - {header} is hand-fitted.', 'nested list item'),
                ('item continuation', f'5. The 776228-1 header is hand-fitted\nwhen unstocked.', "'when'"),
                ('heading', f'## Hand-fit if unstocked\n\n{header} is hand-fitted.', 'governing heading'),
                ('unbalanced', f'Parts ({header} are hand-fitted.', 'unbalanced parentheses'),
                ('negated', f'The 776228-1 header is not hand-fitted.', 'affirmatively declare'),
                ('negated by hand', f'The 776228-1 header is never fitted by hand.', 'affirmatively declare'),
                ('no hand-fit', f'The 776228-1 header is assembled by JLCPCB.', 'affirmatively declare'),
                ('named twice', f'{header} is hand-fitted; {header} is listed.', 'named 2 times'),
                ('code block', f'```\n{header} is hand-fitted.', 'code block')):
            with self.subTest(name):
                netlist, params = self.policy_case()
                (self.root / 'decision.md').write_text(text + '\n')
                lines = text.split('\n')
                line = next(i for i, l in enumerate(lines, 1) if '776228-1' in l)
                params['hand_fit_policy'].update(sha256=digest(self.root / 'decision.md'), line=line,
                                                 quote='776228-1')
                with self.assertRaisesRegex(AssemblyReadinessExecutionError, message) as caught:
                    self.evaluate(netlist, params)
                self.assertEqual(caught.exception.execution, 'failed')

    def test_policy_with_raw_jlc_capture_admits_positive_stock_and_still_gates_freshness(self):
        netlist, params = self.policy_case()
        doc = self.jlc_capture(self.root, {'C25804': (200, search(row('C25804', 500))),
                                          'C90099': (200, search(row('C90099', 260, componentModelEn='776228-1')))},
                               ['C25804', 'C90099'])
        params.update(catalog='jlc/catalog.json', as_of=self.shifted(doc['as_of'], 1), max_age_hours=12)
        report = self.evaluate(netlist, params)
        self.assertEqual((report['execution'], report['outcome']), ('ok', 'pass'), report)
        self.assertEqual(report['declared_deviation'][0]['catalog_observation']['stock'], 260)
        params.update(as_of=self.shifted(doc['as_of'], 13))
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'stale'):
            self.evaluate(netlist, params)

    def test_native_bom_exclusion_cannot_hide_an_undeclared_no_lcsc_part(self):
        netlist, params = self.handfit_case([])
        netlist = self.export('excluded.kicad_sch', schematic('excluded', [part('R1'),
            part('J1', 'J', lcsc='NO_LCSC', part_number=('Partnumber', '776228-1'), in_bom=False)]))
        report = self.evaluate(netlist, params)
        self.assertEqual(report['outcome'], 'defect')
        self.assertEqual(report['excluded_from_bom'], ['J1'])
        self.assertEqual(report['findings'][0]['code'], 'invalid_assembly_exclusion')

    def test_lcsc_binding_missing_invalid_and_unknown_in_catalog(self):
        cat = self.catalog([{"lcsc": "C25804", "stock": 1000, "is_basic": True, "last_seen": "2026-10-15"}])
        netlist = self.export("design.kicad_sch", schematic("design", [
            part("R1", lcsc=None),
            part("R2", lcsc="C999999"),
            part("R3", lcsc="BAD_ID"),
            part("R4"),
        ]))
        report = self.evaluate(netlist, {"catalog": cat})
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "defect"))
        self.assertEqual(report["missing_lcsc_refs"], ["R1"])
        self.assertEqual(report["unknown_lcsc_in_catalog"], {"R2": "C999999"})
        self.assertEqual(report["invalid_lcsc"], {"R3": "BAD_ID"})
        by_code = {f["code"]: f for f in report["findings"]}
        self.assertEqual(by_code["missing_lcsc"]["reference"], "R1")
        self.assertIn("R1", by_code["missing_lcsc"]["message"])
        self.assertEqual(by_code["unknown_lcsc_in_catalog"]["lcsc"], "C999999")
        self.assertIn("C999999", by_code["unknown_lcsc_in_catalog"]["message"])
        self.assertEqual(report["parts"]["C25804"]["references"], ["R4"])
        self.assertEqual(report["catalog"]["sha256"], digest(self.root / cat))
        self.assertEqual(report["netlist"]["sha256"], digest(netlist))

    # ------------------------------------------------------------------ stock
    def test_stock_zero_and_below_quantity_times_boards_are_defects_with_snapshot_date(self):
        cat = self.catalog([
            {"lcsc": "C10001", "stock": 0, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C10002", "stock": 15, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C10003", "stock": 20, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        netlist = self.export("design.kicad_sch", schematic("design", [
            part("R1", lcsc="C10001"),
            part("R2", lcsc="C10002"), part("R3", lcsc="C10002"),
            part("R4", lcsc="C10003"), part("R5", lcsc="C10003"),
        ]))
        report = self.evaluate(netlist, {"catalog": cat, "boards": 10})
        self.assertEqual(report["outcome"], "defect")
        oos = next(f for f in report["findings"] if f["code"] == "out_of_stock")
        self.assertEqual((oos["lcsc"], oos["available"], oos["required"], oos["snapshot_date"]),
                         ("C10001", 0, 10, "2026-10-15"))
        short = [f for f in report["findings"] if f["code"] == "insufficient_stock"]
        self.assertEqual(len(short), 1)
        self.assertEqual((short[0]["lcsc"], short[0]["references"], short[0]["available"], short[0]["required"]),
                         ("C10002", ["R2", "R3"], 15, 20))
        self.assertIn("2026-10-15", short[0]["message"])
        self.assertIn("available 15 < required 20", short[0]["message"])
        # Exactly covered stock (2 x 10 = 20) is ok.
        self.assertEqual(report["parts"]["C10003"]["stock_state"], "ok")

        one_board = self.evaluate(netlist, {"catalog": cat, "boards": 1})
        self.assertEqual([f["code"] for f in one_board["findings"] if f["severity"] == "defect"], ["out_of_stock"])

    def test_missing_stale_future_or_undeclared_stock_freshness_is_unknown_never_pass(self):
        netlist = self.export("design.kicad_sch", schematic("design", [part("R1")]))

        def outcome(parts, params=None, **catalog_kw):
            report = self.evaluate(netlist, {"catalog": self.catalog(parts, **catalog_kw),
                                             **(params or {})})
            return report["outcome"], [f["code"] for f in report["findings"]]

        fresh = {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}
        self.assertEqual(outcome([fresh]), ("pass", []))
        self.assertEqual(outcome([{**fresh, "stock": None}]), ("unknown", ["missing_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": None}]), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": "2026-10-01"}]), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": "2026-10-10"}], {"max_stock_age_days": 3}),
                         ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(
            outcome([{**fresh, "last_seen": "2026-10-14T00:00:00Z"}],
                    {"as_of": "2026-10-15T12:00:00Z", "max_age_hours": 24}),
            ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([{**fresh, "last_seen": "2026-10-20"}]), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(
            outcome([{**fresh, "last_seen": "2026-10-15T13:00:00Z"}],
                    {"as_of": "2026-10-15T12:00:00Z", "max_age_hours": 24}),
            ("unknown", ["stale_stock_telemetry"]))
        # No declared maximum age anywhere: freshness cannot be established.
        self.assertEqual(outcome([fresh], stale_before=None), ("unknown", ["stale_stock_telemetry"]))
        self.assertEqual(outcome([fresh], {"max_stock_age_days": 3}, stale_before=None), ("pass", []))
        # Stale stock never masks an explicit zero.
        self.assertEqual(outcome([{**fresh, "stock": 0, "last_seen": "2026-01-01"}]), ("defect", ["out_of_stock"]))

    # ------------------------------------------------------------------ property policy (clean_boost-like)
    def test_clean_boost_like_part_number_only_policy_reports_3_of_9_and_any_of_only_c1(self):
        # The ticket says "4 of 9"; its source document lists only three (C1, R2, U1). See
        # docs/assembly-readiness.md. This original fixture has the same shape and gives 3.
        cat = self.catalog([
            {"lcsc": f"C1000{i}", "stock": 200, "is_basic": True, "last_seen": "2026-10-15"} for i in range(1, 10)
        ])
        volts = ("Voltage", "25V")
        netlist = self.export("clean_boost.kicad_sch", schematic("clean_boost", [
            part("C1", "C", lcsc="C10001", part_number=None, rating=volts),
            part("C2", "C", lcsc="C10002", part_number=("PartNumber", "CAP-22U-25V"), rating=volts),
            part("C3", "C", lcsc="C10003", part_number=("PartNumber", "CAP-100N-25V"), rating=volts),
            part("J1", "J", lcsc="C10004", part_number=("PartNumber", "HDR-1X2-IN"), rating=None),
            part("J2", "J", lcsc="C10005", part_number=("PartNumber", "HDR-1X2-OUT"), rating=None),
            part("R1", lcsc="C10006", part_number=("PartNumber", "RES-1M")),
            part("R2", lcsc="C10007", part_number=("MPN", "RES-100K")),
            part("R3", lcsc="C10008", part_number=("PartNumber", "RES-4K7")),
            part("U1", "U", footprint="ee29:SOT-23-6", lcsc="C10009", part_number=("MPN", "BOOST-REG-X"),
                 rating=None),
            power("#PWR01"), power("#FLG01", "power:PWR_FLAG", "PWR_FLAG"),
        ]))
        part_number_only = self.evaluate(netlist, {"catalog": cat, "required_properties": ["PartNumber"]})
        self.assertEqual(part_number_only["counts"]["populated"], 9)
        self.assertEqual(sorted(part_number_only["missing_properties"]), ["C1", "R2", "U1"])
        missing = [f["reference"] for f in part_number_only["findings"] if f["code"] == "missing_property"]
        self.assertEqual(missing, ["C1", "R2", "U1"])

        for policy in (["PartNumber|MPN|CompanyPN"],
                       ["Footprint", "LCSC", "Manufacturer", {"any_of": ["PartNumber", "MPN", "CompanyPN"]}]):
            with self.subTest(policy=policy):
                any_of = self.evaluate(netlist, {"catalog": cat, "required_properties": policy})
                self.assertEqual(any_of["counts"]["populated"], 9)
                self.assertEqual(list(any_of["missing_properties"]), ["C1"])
                self.assertEqual([f["reference"] for f in any_of["findings"] if f["severity"] != "info"], ["C1"])

    # ------------------------------------------------------------------ DNP vs exclude_from_bom (di_box-like)
    def test_di_box_like_dnp_only_on_native_attribute_and_in_bom_no_reported_separately(self):
        cat = self.catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C17024", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        base = [
            part("R1"),
            part("C1", "C", lcsc="C17024", rating=("Voltage", "50V")),
            part("R3", lcsc=None, part_number=None, manufacturer=None, dnp=True),
            part("TP1", "J", lcsc=None, part_number=None, manufacturer=None, rating=None, in_bom=False),
            part("H1", "MH", footprint="ee29:MountingHole_M3", lcsc=None, part_number=None, manufacturer=None,
                 rating=None, in_bom=False, on_board=False),
            part("H2", "MH", footprint="ee29:MountingHole_M3", lcsc=None, part_number=None, manufacturer=None,
                 rating=None, in_bom=False),
            power("#PWR01"), power("#FLG01", "power:PWR_FLAG", "PWR_FLAG"),
        ]
        netlist = self.export("di_box.kicad_sch", schematic("di_box", base))
        extracted = read_netlist_components(netlist)
        self.assertEqual([c["reference"] for c in extracted["populated"]], ["C1", "R1"])
        self.assertEqual([c["reference"] for c in extracted["excluded_dnp"]], ["R3"])
        self.assertEqual([c["reference"] for c in extracted["excluded_from_bom"]], ["H2", "TP1"])
        self.assertEqual([c["reference"] for c in extracted["ignored_non_assembly"]], ["H1"])

        report = self.evaluate(netlist, {"catalog": cat})
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "pass"))
        self.assertEqual(report["excluded_dnp"], ["R3"])
        self.assertEqual(report["excluded_from_bom"], ["H2", "TP1"])
        self.assertEqual({f["code"] for f in report["findings"]}, {"exclude_from_bom"})
        self.assertTrue(all(f["severity"] == "info" for f in report["findings"]))
        # Power symbols are never components; they are only the exporter-omitted occurrences.
        self.assertNotIn("#PWR01", str({k: v for k, v in report.items() if k != "netlist"}))
        self.assertEqual(report["netlist"]["hierarchy_coverage"],
                         {"bound_components": 6, "exporter_omitted": ["#FLG01", "#PWR01"]})

        # A field-level DNP convention is not a native DNP: the part stays populated and is a defect.
        marked = self.export("di_box_marked.kicad_sch", schematic("di_box_marked", base + [
            part("R5", extra={"DNP": "yes"}),
            part("R6", extra={"dnp": "yes"}),
            part("R7", value="DNP"),
        ]))
        marked_report = self.evaluate(marked, {"catalog": cat})
        self.assertEqual(marked_report["excluded_dnp"], ["R3"])
        self.assertEqual(marked_report["populated_refs"], ["C1", "R1", "R5", "R6", "R7"])
        flagged = {f["reference"]: f["markers"] for f in marked_report["findings"]
                   if f["code"] == "non_native_assembly_marker"}
        self.assertEqual(flagged, {"R5": ["DNP"], "R6": ["dnp"], "R7": ["Value=DNP"]})
        self.assertEqual(marked_report["outcome"], "defect")

    # ------------------------------------------------------------------ rating properties
    def test_passive_rating_properties_required_for_r_and_c(self):
        cat = self.catalog([
            {"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
            {"lcsc": "C17024", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"},
        ])
        unrated = self.export("unrated.kicad_sch", schematic("unrated", [
            part("R1", rating=None), part("C1", "C", lcsc="C17024", rating=None),
            part("D1", "D", rating=None),
        ]))
        report = self.evaluate(unrated, {"catalog": cat})
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual(report["missing_rating_refs"], ["C1", "R1"])
        self.assertEqual(self.evaluate(unrated, {"catalog": cat, "require_passive_ratings": False})["outcome"],
                         "pass")

        rated = self.export("rated.kicad_sch", schematic("rated", [
            part("R1", rating=("PARAM_power", "0.1W")), part("C1", "C", lcsc="C17024", rating=("PARAM_voltage", "50V")),
        ]))
        self.assertEqual(self.evaluate(rated, {"catalog": cat})["outcome"], "pass")

    # ------------------------------------------------------------------ empty
    def test_empty_schematic_gives_info_with_zero_components_not_pass(self):
        cat = self.catalog([{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}])
        for name, parts in (("empty", []), ("power_only", [power("#PWR01"), power("#FLG01", "power:PWR_FLAG")])):
            with self.subTest(name=name):
                netlist = self.export(f"{name}.kicad_sch", schematic(name, parts))
                report = self.evaluate(netlist, {"catalog": cat})
                self.assertEqual((report["execution"], report["outcome"]), ("ok", "info"))
                self.assertEqual(report["counts"]["populated"], 0)
                self.assertEqual(report["netlist"]["components"], 0)
                self.assertIn("0 populated assembly components", report["summary"])

    # ------------------------------------------------------------------ fail closed
    def test_unparseable_inputs_and_missing_snapshot_fail_instead_of_empty_findings(self):
        cat = self.catalog([{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}])
        good = schematic("good", [part("R1")])
        (self.root / "good.kicad_sch").write_text(good, encoding="utf-8")
        (self.root / "truncated.kicad_sch").write_text(good[: len(good) // 2], encoding="utf-8")
        (self.root / "other.kicad_sch").write_text("(kicad_pcb (version 1))", encoding="utf-8")
        self.assertEqual(list(verify_schematic_files(self.root, ["good.kicad_sch"])), ["good.kicad_sch"])
        for bad in ("truncated.kicad_sch", "other.kicad_sch", "absent.kicad_sch"):
            with self.subTest(schematic=bad), self.assertRaises(AssemblyReadinessExecutionError) as caught:
                verify_schematic_files(self.root, ["good.kicad_sch", bad])
            self.assertEqual(caught.exception.execution, "failed")

        (self.root / "garbage.xml").write_text("<export><components><comp ref=", encoding="utf-8")
        (self.root / "nocomponents.xml").write_text("<export><design/></export>", encoding="utf-8")
        (self.root / "duplicate.xml").write_text(
            '<export><components><comp ref="R1"/><comp ref="R1"/></components></export>', encoding="utf-8")
        for name in ("garbage.xml", "nocomponents.xml", "duplicate.xml", "absent.xml"):
            with self.subTest(netlist=name), self.assertRaises(AssemblyReadinessExecutionError) as caught:
                self.evaluate(self.root / name, {"catalog": cat})
            self.assertEqual(caught.exception.execution, "failed")

        netlist = self.export("design.kicad_sch", schematic("design", [part("R1")]))
        with self.assertRaises(AssemblyReadinessExecutionError) as sheets:
            evaluate_assembly_readiness(self.root, netlist, {"catalog": cat}, expected_sheets=2)
        self.assertEqual(sheets.exception.execution, "failed")
        with self.assertRaises(AssemblyReadinessExecutionError) as missing:
            self.evaluate(netlist, {"catalog": "does-not-exist.json"})
        self.assertEqual(missing.exception.execution, "failed")
        (self.root / "dup.json").write_text(
            '{"schema": "agent-ee.catalog-snapshot/1", "schema": "x", "snapshot_date": "2026-10-15", "parts": []}',
            encoding="utf-8")
        with self.assertRaises(AssemblyReadinessExecutionError) as dup:
            load_catalog_snapshot(self.root, "dup.json")
        self.assertEqual(dup.exception.execution, "failed")
        with self.assertRaises(AssemblyReadinessExecutionError) as params:
            self.evaluate(netlist, {"catalog": cat, "boards": 0})
        self.assertEqual(params.exception.execution, "unsupported")

    # ------------------------------------------------------------------ hierarchy coverage
    def test_child_sheet_kicad_cannot_load_fails_closed_naming_dropped_references(self):
        # kicad-cli exports the root with exit 0 and both sheets but drops every component of a
        # child sheet it cannot load; the occurrence audit must name what is missing.
        cat = self.catalog([{"lcsc": "C25804", "stock": 500, "is_basic": True, "last_seen": "2026-10-15"}])
        root_uuid, a_uuid = _uuid("sheet-top"), _uuid("inst-a")
        child = schematic("stage", [part("R5", lcsc="C0BAD"), part("R6", lcsc=None)],
                          instance_paths=[(f"/{root_uuid}/{a_uuid}", 1)])
        top = schematic("top", [part("R1"), power("#PWR01")],
                        sheets=[("A", "stage.kicad_sch", a_uuid, root_uuid)])

        control = self.export("top.kicad_sch", top, children=[("stage.kicad_sch", child)])
        report = self.evaluate(control, {"catalog": cat})
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "defect"))
        self.assertEqual(report["populated_refs"], ["R1", "R105", "R106"])
        self.assertEqual(report["invalid_lcsc"], {"R105": "C0BAD"})
        self.assertEqual(report["missing_lcsc_refs"], ["R106"])
        self.assertEqual(report["netlist"]["hierarchy_coverage"],
                         {"bound_components": 3, "exporter_omitted": ["#PWR01"]})

        for label, broken in (("bad_bool", child.replace("(in_bom yes)", "(in_bom maybe)", 1)),
                              ("unknown_token", child.replace("(in_bom yes)", "(in_bom yes) (bogus_token 1)", 1))):
            with self.subTest(label):
                netlist = self.export("top.kicad_sch", top, children=[("stage.kicad_sch", broken)])
                # The sheet still parses as an S-expression and still counts as a sheet ...
                self.assertEqual(list(verify_schematic_files(self.root, ["top.kicad_sch", "stage.kicad_sch"])),
                                 ["top.kicad_sch", "stage.kicad_sch"])
                extracted = read_netlist_components(netlist)
                self.assertEqual((extracted["sheets"], extracted["components"]), (2, 1))
                # ... so without the occurrence audit the check would pass on R1 alone.
                self.assertEqual(evaluate_assembly_readiness(self.root, netlist, {"catalog": cat},
                                                             expected_sheets=2)["outcome"], "pass")
                with self.assertRaises(AssemblyReadinessExecutionError) as caught:
                    self.evaluate(netlist, {"catalog": cat})
                self.assertEqual(caught.exception.execution, "failed")
                message = str(caught.exception)
                self.assertIn("unexplained_omission", message)
                self.assertIn("['R105', 'R106']", message)
                self.assertIn("stage.kicad_sch", message)

        # Reverse direction: a netlist component that is no occurrence of the captured hierarchy.
        (self.root / "stage.kicad_sch").write_text(child, encoding="utf-8")
        other = self.export("other.kicad_sch", schematic("other", [part("R9")]))
        with self.assertRaises(AssemblyReadinessExecutionError) as foreign:
            verify_netlist_coverage(self.root, ["top.kicad_sch", "stage.kicad_sch"], read_netlist_components(other))
        self.assertEqual(foreign.exception.execution, "failed")
        self.assertIn("native_identity_unmatched", str(foreign.exception))
        self.assertIn("R9", str(foreign.exception))

    # ------------------------------------------------------------------ pipeline
    def test_sqlite_catalog_reused_sheet_extended_limit_and_run_checks_pipeline(self):
        source = self.root / "src_tree"
        source.mkdir()
        _write_sqlite_catalog(source, [
            {"lcsc": "C25804", "stock": 1000, "is_basic": True},
            {"lcsc": "C17024", "stock": 800, "is_basic": False, "is_preferred": True},
            {"lcsc": "C90001", "stock": 120, "is_basic": False},
            {"lcsc": "C90002", "stock": 80, "is_basic": False},
        ])
        root_uuid = _uuid("sheet-top")
        a_uuid, b_uuid = _uuid("inst-a"), _uuid("inst-b")
        # One child sheet reused twice: U1 appears as U101 and U201, so 2 per board.
        child = schematic("stage", [
            part("U1", "U", footprint="ee29:SOIC-8", lcsc="C90002", part_number=("MPN", "OPAMP-X"), rating=None),
        ], instance_paths=[(f"/{root_uuid}/{a_uuid}", 1), (f"/{root_uuid}/{b_uuid}", 2)])
        (source / "stage.kicad_sch").write_text(child, encoding="utf-8")
        top = schematic("top", [
            part("R1"),
            part("C1", "C", lcsc="C17024", rating=("Voltage", "50V")),
            part("U9", "U", footprint="ee29:SOIC-8", lcsc="C90001", rating=None),
            power("#PWR01"),
        ], sheets=[("A", "stage.kicad_sch", a_uuid, root_uuid), ("B", "stage.kicad_sch", b_uuid, root_uuid)])
        (source / "top.kicad_sch").write_text(top, encoding="utf-8")
        (source / "top.kicad_pcb").write_text("(kicad_pcb (version 20221018))", encoding="utf-8")
        (source / "req.md").write_text("# Requirements\nR-ASM: JLC assembly readiness\n", encoding="utf-8")

        def run(name, params):
            scope = {
                "schema": "agent-ee.scope/3", "name": "assembly-test", "scope": "assembly readiness verification",
                "inputs": [{"path": str(source), "at": "design"}],
                "schematic": "design/top.kicad_sch", "board": "design/top.kicad_pcb", "packet_max_bytes": 16384,
                "checks": [{"id": "jlc_assembly", "kind": "assembly_readiness",
                            "params": {"catalog": "design/catalog-manifest.json", **params}}],
                "baseline": {
                    "requirements": [{
                        "id": "R-ASM", "intent": "acceptance",
                        "statement": "All populated parts are stocked for the build with a bounded Extended count",
                        "source": {"path": "design/req.md", "locator": "R-ASM"}, "checks": ["jlc_assembly"],
                    }],
                    "targets": ["design/req.md"], "reference_architectures": ["design/req.md"],
                },
            }
            case = self.root / name
            case.mkdir()
            write_json(case / "scope.json", scope)
            run_dir = case / "run"
            run_dir.mkdir()
            manifest = capture(case / "scope.json", run_dir)
            return scope, manifest, run_dir

        scope, manifest, run_dir = run("pass", {"boards": 5, "max_extended_parts": 2})
        baseline = read_json(run_dir / "baseline.json")
        self.assertIn("design/catalog.sqlite", baseline["files"])
        desc = describe_check(scope["checks"][0], check_specs=scope["checks"], files=manifest["files"],
                              scope=scope, inputs=run_dir / "inputs")
        self.assertEqual(desc["status"], "supported")
        self.assertIn("assembly:extended-parts-limit", desc["predicates"])

        res = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((res["execution"], res["outcome"]), ("ok", "pass"), res["summary"])
        self.assertIn("work/jlc_assembly/design.xml", res["evidence"])
        self.assertIn("work/jlc_assembly/assembly-readiness.json", res["evidence"])
        details = res["details"]
        self.assertEqual(details["populated_refs"], ["C1", "R1", "U9", "U101", "U201"])
        self.assertEqual(details["parts"]["C90002"]["quantity_per_board"], 2)
        self.assertEqual(details["parts"]["C90002"]["required_stock"], 10)
        self.assertEqual(details["tier_counts"]["extended_lcsc"], ["C90001", "C90002"])
        self.assertEqual(details["schematic"]["sheet_instances"], 3)
        self.assertEqual(details["schematic"]["files"], {
            "design/top.kicad_sch": digest(source / "top.kicad_sch"),
            "design/stage.kicad_sch": digest(source / "stage.kicad_sch"),
        })
        self.assertEqual(details["netlist"]["sha256"], digest(run_dir / "work/jlc_assembly/design.xml"))
        self.assertEqual(details["netlist"]["hierarchy_coverage"],
                         {"bound_components": 5, "exporter_omitted": ["#PWR01"]})
        self.assertEqual(details["catalog"]["database_sha256"], digest(source / "catalog.sqlite"))

        # 2 per board x 50 boards = 100 > 80 available; 2 Extended > 1.
        _, _, short_dir = run("short", {"boards": 50, "max_extended_parts": 1})
        short = run_checks(short_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((short["execution"], short["outcome"]), ("ok", "defect"))
        codes = {f["code"]: f for f in short["details"]["findings"]}
        self.assertEqual((codes["insufficient_stock"]["available"], codes["insufficient_stock"]["required"]),
                         (80, 100))
        self.assertEqual(codes["extended_parts_limit_exceeded"]["extended_unique"], 2)

        # kicad-cli exports a truncated child sheet with exit 0 and no components; the check must fail instead.
        truncated = (run_dir / "inputs/design/stage.kicad_sch").read_text()
        (run_dir / "inputs/design/stage.kicad_sch").write_text(truncated[:400], encoding="utf-8")
        failed = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((failed["execution"], failed["outcome"]), ("failed", "unknown"), failed["summary"])
        self.assertIn("does not parse", failed["summary"])

        # A complete child sheet kicad-cli cannot load is dropped with exit 0; the check must still fail.
        (run_dir / "inputs/design/stage.kicad_sch").write_text(
            truncated.replace("(in_bom yes)", "(in_bom maybe)", 1), encoding="utf-8")
        dropped = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((dropped["execution"], dropped["outcome"]), ("failed", "unknown"), dropped["summary"])
        self.assertIn("['U101', 'U201']", dropped["summary"])

        (run_dir / "inputs/design/top.kicad_sch").write_text("(kicad_sch (unclosed", encoding="utf-8")
        failed_root = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((failed_root["execution"], failed_root["outcome"]), ("failed", "unknown"))

        (run_dir / "inputs/design/catalog-manifest.json").unlink()
        (run_dir / "inputs/design/top.kicad_sch").write_text(top, encoding="utf-8")
        (run_dir / "inputs/design/stage.kicad_sch").write_text(truncated, encoding="utf-8")
        no_catalog = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((no_catalog["execution"], no_catalog["outcome"]), ("failed", "unknown"))


    # ------------------------------------------------------------------ AGENTEE-155 producer capture
    def jlc_capture(self, parent, routes, ids):
        """Run the owner producer against an owned loopback stub (never JLCPCB)."""
        server = JlcStub(routes)
        self.addCleanup(server.close)
        doc = capture_jlc_stock(ids, parent / "jlc", allow_network=True, endpoint=server.url, min_interval=1.0)
        self.assertEqual(len(server.requests), len(ids))
        # Owned synthetic bytes exercise the consumer's production source contract.
        doc['source']['endpoint'] = DEFAULT_ENDPOINT
        write_json(parent / "jlc/catalog.json", doc)
        return doc

    @staticmethod
    def shifted(stamp, hours):
        return (datetime.fromisoformat(stamp) + timedelta(hours=hours)).isoformat()

    def test_jlc_capture_bound_to_run_refuses_stale_or_uncovered_and_rederives_bytes(self):
        doc = self.jlc_capture(self.root, {
            "C25804": (200, search(row("C25804", 1000))),
            "C17024": (200, search(row("C17024", 40, library="expand", preferred=True))),
            "C90001": (200, search()),
            "C90002": (503, b'{"code":503}'),
        }, ["C25804", "C17024", "C90001", "C90002"])
        cat = "jlc/catalog.json"
        netlist = self.export("design.kicad_sch", schematic("design", [
            part("R1"), part("C1", "C", lcsc="C17024", rating=("Voltage", "50V"))]))
        self.assertEqual(ids_from_netlist(netlist), ["C17024", "C25804"])
        run = self.shifted(doc["as_of"], 2)
        params = {"catalog": cat, "as_of": run, "max_age_hours": 24, "boards": 20}

        report = self.evaluate(netlist, params)
        self.assertEqual((report["execution"], report["outcome"]), ("ok", "pass"), report["summary"])
        self.assertEqual(report["catalog"]["schema"], "agent-ee.jlc-catalog/1")
        self.assertEqual(report["catalog"]["as_of"], run)
        self.assertEqual(report["catalog"]["capture"], {
            "as_of": doc["as_of"], "completed_at": doc["completed_at"],
            "requested": ["C17024", "C25804", "C90001", "C90002"], "not_found": ["C90001"], "failed": ["C90002"],
            "response_verdicts": {r["lcsc"]: {"path": r["response"]["path"], "sha256": r["response"]["sha256"],
                                                 "verdict": "verified"} for r in doc["requests"]}})
        self.assertEqual(report["parts"]["C17024"]["tier"], "Preferred")
        self.assertEqual(report["parts"]["C25804"]["last_seen"],
                         next(r["fetched_at"] for r in doc["requests"] if r["lcsc"] == "C25804"))
        short = self.evaluate(netlist, {**params, "boards": 41})
        self.assertEqual([f["code"] for f in short["findings"] if f["severity"] == "defect"], ["insufficient_stock"])

        def refused(execution, needle, check_params, design=netlist):
            with self.assertRaises(AssemblyReadinessExecutionError) as caught:
                self.evaluate(design, check_params)
            self.assertEqual(caught.exception.execution, execution)
            self.assertIn(needle, str(caught.exception))

        # as_of must be bound to the run and a staleness declared; the capture's own as_of is not used.
        refused("unsupported", "params.as_of", {"catalog": cat, "max_age_hours": 24})
        refused("unsupported", "declared staleness", {"catalog": cat, "as_of": run})
        refused("failed", "stale", {**params, "as_of": self.shifted(doc["as_of"], 25)})
        refused("failed", "stale", {"catalog": cat, "as_of": self.shifted(doc["as_of"], 49), "max_stock_age_days": 1})
        refused("failed", "stale", {"catalog": cat, "as_of": self.shifted(doc["as_of"], 49),
                                    "stale_before": self.shifted(doc["as_of"], 48)[:10]})
        refused("failed", "after the run", {**params, "as_of": self.shifted(doc["as_of"], -1)})

        # An observed not-found is evidence of absence; an unrequested or failed id is not.
        not_found = self.export("nf.kicad_sch", schematic("nf", [part("R1"), part("R2", lcsc="C90001")]))
        report = self.evaluate(not_found, params)
        self.assertEqual((report["outcome"], report["unknown_lcsc_in_catalog"]), ("defect", {"R2": "C90001"}))
        failed = self.export("failed.kicad_sch", schematic("failed", [part("R1"), part("R2", lcsc="C90002")]))
        refused("failed", "C90002': 'HTTP 503'", params, failed)
        unrequested = self.export("unreq.kicad_sch", schematic("unreq", [part("R1"), part("R2", lcsc="C12345")]))
        refused("failed", "requested LCSC id(s) ['C12345']", params, unrequested)

        # The check re-derives every observation from the captured raw bytes.
        response = self.root / "jlc/responses/C25804.json"
        original = response.read_bytes()
        response.write_bytes(search(row("C25804", 999999)))
        refused("failed", "size/sha256", params)
        summary = self.root / "jlc/catalog.json"
        forged = read_json(summary)
        forged["parts"]["C25804"]["stock"] = 999999
        response.write_bytes(original)
        write_json(summary, forged)
        refused("failed", "re-derived", params)
        response.unlink()
        refused("failed", "missing captured JLC response", params)

    def test_jlc_capture_responses_are_immutable_inputs_through_run_checks(self):
        source = self.root / "src_tree"
        source.mkdir()
        doc = self.jlc_capture(source, {"C25804": (200, search(row("C25804", 500))),
                                        "C17024": (200, search(row("C17024", 500, library="expand")))},
                               ["C25804", "C17024"])
        top = schematic("top", [part("R1"), part("C1", "C", lcsc="C17024", rating=("Voltage", "50V"))])
        (source / "top.kicad_sch").write_text(top, encoding="utf-8")
        (source / "top.kicad_pcb").write_text("(kicad_pcb (version 20221018))", encoding="utf-8")
        (source / "req.md").write_text("# Requirements\nR-ASM: JLC assembly readiness\n", encoding="utf-8")
        scope = {
            "schema": "agent-ee.scope/3", "name": "jlc-capture", "scope": "assembly readiness from a JLC capture",
            "inputs": [{"path": str(source), "at": "design"}],
            "schematic": "design/top.kicad_sch", "board": "design/top.kicad_pcb", "packet_max_bytes": 16384,
            "checks": [{"id": "jlc_assembly", "kind": "assembly_readiness",
                        "params": {"catalog": "design/jlc/catalog.json", "boards": 10,
                                   "as_of": self.shifted(doc["as_of"], 1), "max_age_hours": 12}}],
            "baseline": {
                "requirements": [{
                    "id": "R-ASM", "intent": "acceptance", "statement": "All populated parts are stocked",
                    "source": {"path": "design/req.md", "locator": "R-ASM"}, "checks": ["jlc_assembly"]}],
                "targets": ["design/req.md"], "reference_architectures": ["design/req.md"],
            },
        }
        write_json(self.root / "scope.json", scope)
        run_dir = self.root / "run"
        run_dir.mkdir()
        manifest = capture(self.root / "scope.json", run_dir)
        baseline = read_json(run_dir / "baseline.json")
        for rel in ("design/jlc/catalog.json", "design/jlc/responses/C25804.json", "design/jlc/responses/C17024.json"):
            self.assertIn(rel, baseline["files"])
        desc = describe_check(scope["checks"][0], check_specs=scope["checks"], files=manifest["files"],
                              scope=scope, inputs=run_dir / "inputs")
        self.assertEqual(desc["status"], "supported")
        res = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((res["execution"], res["outcome"]), ("ok", "pass"), res["summary"])
        self.assertEqual(res["details"]["catalog"]["capture"]["requested"], ["C17024", "C25804"])
        self.assertEqual(res['details']['policy']['max_age_hours'], 12)

        # A retained review start takes precedence over a caller's backdated as_of.
        started = self.shifted(doc['as_of'], 25)
        write_json(run_dir / 'result.json', {'started_utc': started})
        stale = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((stale['execution'], stale['outcome']), ('failed', 'unknown'))
        self.assertIn('stale', stale['summary'])
        self.assertEqual(stale['details']['stock_evaluation']['as_of'], started)
        (run_dir / 'result.json').unlink()

        (run_dir / "inputs/design/jlc/responses/C17024.json").unlink()
        missing = run_checks(run_dir, time.monotonic() + 300.0)[0]
        self.assertEqual((missing["execution"], missing["outcome"]), ("failed", "unknown"), missing["summary"])
        self.assertIn("C17024", missing["summary"])

    def test_response_hash_mismatch_unknown(self):
        self.jlc_capture(self.root, {"C25804": (200, search(row("C25804", 500)))}, ["C25804"])
        (self.root / "top.kicad_sch").write_text(schematic("top", [part("R1")]))
        scope = {"schema": "agent-ee.scope/1", "inputs": [{"path": str(self.root / "top.kicad_sch"),
                 "at": "top.kicad_sch"}, {"path": str(self.root / "jlc"), "at": "jlc"}],
                 "schematic": "top.kicad_sch", "checks": [{"id": "asm", "kind": "assembly_readiness",
                 "params": {"catalog": "jlc/catalog.json"}}]}
        write_json(self.root / "scope.json", scope)
        run = self.root / "run"
        run.mkdir()
        capture(self.root / "scope.json", run)
        (run / "inputs/jlc/responses/C25804.json").write_bytes(search(row("C25804", 501)))
        result = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertEqual(result['details']['response_verdicts']['C25804']['verdict'], 'mismatch')
        self.assertIn('work/asm/assembly-readiness.json', result['evidence'])

        (run / 'inputs/jlc/responses/C25804.json').unlink()
        missing = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((missing['execution'], missing['outcome']), ('failed', 'unknown'))
        self.assertEqual(missing['details']['response_verdicts']['C25804']['verdict'], 'missing')

        # A parent symlink must not substitute bytes from outside captured inputs.
        (run / 'inputs/jlc/responses').rmdir()
        (run / 'inputs/jlc/responses').symlink_to(self.root / 'jlc/responses', target_is_directory=True)
        linked = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((linked['execution'], linked['outcome']), ('failed', 'unknown'))
        self.assertIn('symlink', linked['summary'])

    def test_invalid_check_params_are_unsupported(self):
        (self.root / 'top.kicad_sch').write_text(schematic('top', [part('R1')]))
        scope = {'schema': 'agent-ee.scope/1', 'inputs': [
                 {'path': str(self.root / 'top.kicad_sch'), 'at': 'top.kicad_sch'}],
                 'schematic': 'top.kicad_sch', 'checks': [
                 {'id': 'asm', 'kind': 'assembly_readiness', 'params': {}}]}
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        run.mkdir()
        capture(self.root / 'scope.json', run)
        result = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((result['execution'], result['outcome']), ('unsupported', 'unknown'))
        self.assertIn("'catalog' path", result['summary'])

    def test_unknown_key_refused(self):
        doc = self.jlc_capture(self.root, {"C25804": (200, search(row("C25804", 500)))}, ["C25804"])
        original = read_json(self.root / "jlc/catalog.json")
        for key in ('source_url', 'response_sha256', 'stockNumber'):
            with self.subTest(key=key):
                doc = read_json(self.root / "jlc/catalog.json")
                doc['parts']['C25804'][key] = 'unverified'
                write_json(self.root / "jlc/catalog.json", doc)
                with self.assertRaises(AssemblyReadinessExecutionError):
                    load_catalog_snapshot(self.root, "jlc/catalog.json")
                write_json(self.root / "jlc/catalog.json", original)
        # G3: removing requests must never select the legacy snapshot loader.
        doc = dict(original)
        del doc['requests']
        write_json(self.root / "jlc/catalog.json", doc)
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'requires exactly'):
            load_catalog_snapshot(self.root, "jlc/catalog.json")
        legacy = self.catalog([{'lcsc': 'C25804', 'stock': 500, 'source_url': 'ignored?'}])
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'unknown keys'):
            load_catalog_snapshot(self.root, legacy)

    # ------------------------------------------------------------------ AGENTEE-254 operating temperature
    def temperature_case(self, temperature='-40°C ~ 85°C (TA)', *, mpn='PN-GENERIC', items=None,
                         manufacturer='Example Mfr', package='0603 (1608 Metric)'):
        from tests.test_digikey_capture import product
        from tests.test_digikey_evidence import parameters, write_capture
        found = product(mpn, manufacturer=manufacturer, quantity=5000,
                        parameters=parameters(temperature=temperature, package=package))
        path = write_capture(self.root / 'dk', items if items is not None else {mpn: found})
        cat = self.catalog([{"lcsc": "C25804", "stock": 1000, "is_basic": True, "last_seen": "2026-10-15"}])
        return {"catalog": cat, "operating_temperature": {
            "min_c": -20, "max_c": 70, "capture": "dk/digikey-capture.json", "capture_sha256": digest(path),
            "parts": {"R1": mpn}}}

    def test_digikey_operating_temperature_pass_and_defect(self):
        # R2 is DNP: since AGENTEE-274 a populated unlisted part makes the predicate unknown (tested below).
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1"), part("R2", dnp=True)]))
        params = self.temperature_case()
        report = self.evaluate(netlist, params)
        self.assertEqual(report["outcome"], "pass", report["findings"])
        entry = report["operating_temperature"]["components"][0]
        self.assertEqual((entry["reference"], entry["status"]), ("R1", "pass"))
        self.assertEqual(entry["digikey"]["provenance"]["response"]["sha256"],
                         digest(self.root / "dk/responses/PN-GENERIC.json"))
        self.assertNotIn("quantity_available", entry["digikey"])
        for low, high in ((-41, 70), (-20, 86), (-20, 85.5)):
            with self.subTest(low=low, high=high):
                hot = {**params, "operating_temperature": {**params["operating_temperature"],
                                                           "min_c": low, "max_c": high}}
                report = self.evaluate(netlist, hot)
                self.assertEqual(report["outcome"], "defect")
                self.assertEqual([f["code"] for f in report["findings"] if f["severity"] == "defect"],
                                 ["operating_temperature_out_of_range"])
        # Inclusive endpoints pass.
        edge = {**params, "operating_temperature": {**params["operating_temperature"], "min_c": -40, "max_c": 85}}
        self.assertEqual(self.evaluate(netlist, edge)["outcome"], "pass")

    def test_digikey_operating_temperature_unbound_absent_or_unparseable_is_unknown(self):
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1"), part("R2", dnp=True)]))
        cases = {"junction or case": dict(temperature='-40°C ~ 125°C (TJ)'),
                 "unparsed": dict(temperature='-40°C to 85°C'), "lists no": dict(temperature=None),
                 "is not DigiKey MPN": dict(mpn='PN-GENERIC-T'),
                 "manufacturer": dict(manufacturer='Other Maker'),
                 "not_found": dict(items={'PN-GENERIC': 'not_found'}),
                 "failed": dict(items={'PN-GENERIC': 'failed'})}
        for reason, kwargs in cases.items():
            with self.subTest(reason=reason):
                shutil.rmtree(self.root / "dk", ignore_errors=True)
                report = self.evaluate(netlist, self.temperature_case(**kwargs))
                self.assertEqual(report["outcome"], "unknown", report["findings"])
                finding = next(f for f in report["findings"] if f["code"] == "operating_temperature_unknown")
                self.assertEqual(finding["reference"], "R1")
                self.assertIn(reason, finding["message"])
        params = self.temperature_case()
        for ref in ("R2", "R9"):
            with self.subTest(ref=ref):
                unpopulated = {**params, "operating_temperature": {**params["operating_temperature"],
                                                                   "parts": {ref: "PN-GENERIC"}}}
                report = self.evaluate(netlist, unpopulated)
                self.assertEqual(report["outcome"], "unknown")
                self.assertIn("not exactly one populated", report["operating_temperature"]["components"][0]["reason"])
        pinned = {**params, "operating_temperature": {**params["operating_temperature"], "capture_sha256": "0" * 64}}
        report = self.evaluate(netlist, pinned)
        self.assertEqual(report["outcome"], "unknown")
        self.assertEqual([f["code"] for f in report["findings"] if f["severity"] == "unknown"],
                         ["operating_temperature_unavailable"])
        # A response missing from the candidate's captured files is never replayed.
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob("*") if p.is_file()}
        files.pop("dk/responses/PN-GENERIC.json")
        report = evaluate_assembly_readiness(self.root, netlist, params, captured_files=files)
        self.assertEqual(report["outcome"], "unknown")
        self.assertIn("not captured inputs", report["operating_temperature"]["error"])

    def test_operating_temperature_unlisted_populated_part_is_unknown(self):
        # AGENTEE-274: judging only the listed references can never pass the board-wide predicate.
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1"), part("R2"), part("R3", dnp=True)]))
        params = self.temperature_case()
        report = self.evaluate(netlist, params)
        self.assertEqual(report["outcome"], "unknown", report["findings"])
        self.assertEqual([(f["code"], f["references"]) for f in report["findings"]],
                         [("operating_temperature_unlisted", ["R2"])])
        self.assertEqual(report["operating_temperature"]["unlisted"], ["R2"])
        self.assertEqual(report["operating_temperature"]["components"][0]["status"], "pass")
        # A listed part out of range stays a defect alongside the unlisted unknown.
        hot = {**params, "operating_temperature": {**params["operating_temperature"], "max_c": 90}}
        report = self.evaluate(netlist, hot)
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual(sorted(f["code"] for f in report["findings"]),
                         ["operating_temperature_out_of_range", "operating_temperature_unlisted"])
        # Every populated part judged: the predicate can pass; the DNP R3 needs no verdict.
        full = {**params, "operating_temperature": {**params["operating_temperature"],
                                                    "parts": {"R1": "PN-GENERIC", "R2": "PN-GENERIC"}}}
        report = self.evaluate(netlist, full)
        self.assertEqual(report["outcome"], "pass", report["findings"])
        self.assertEqual([c["status"] for c in report["operating_temperature"]["components"]], ["pass", "pass"])
        self.assertEqual(report["operating_temperature"]["unlisted"], [])

    def exemption(self, ref="R2", line="R2 is a board-edge bleeder resistor; its operating temperature is accepted "
                                       "by the owner without DigiKey evidence.", number=2):
        decision = self.root / "decisions/temperature.md"
        decision.parent.mkdir(exist_ok=True)
        decision.write_text("# Temperature exemptions\n" + line + "\n", encoding="utf-8")
        return {ref: {"source": "decisions/temperature.md", "sha256": digest(decision), "line": number,
                      "quote": line.split(";")[0]}}

    def test_operating_temperature_provenance_bound_exemption(self):
        from agent_ee.assembly_readiness import capability, validate_params
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1"), part("R2")]))
        params = self.temperature_case()
        spec = params["operating_temperature"]
        exempt = {**params, "operating_temperature": {**spec, "exemptions": self.exemption()}}
        report = self.evaluate(netlist, exempt)
        self.assertEqual(report["outcome"], "pass", report["findings"])
        entry = report["operating_temperature"]["exempt"][0]
        self.assertEqual((entry["reference"], entry["status"]), ("R2", "exempt"))
        self.assertEqual(entry["citation"]["sha256"], digest(self.root / "decisions/temperature.md"))
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob("*") if p.is_file()}
        self.assertIn("decisions/temperature.md", capability(exempt, files, self.root)["immutable_inputs"])
        self.assertEqual(evaluate_assembly_readiness(self.root, netlist, exempt, captured_files=files)["outcome"],
                         "pass")
        # An uncaptured, changed, misquoted or unpopulated exemption is unknown, never a pass.
        files.pop("decisions/temperature.md")
        with self.assertRaisesRegex(ValueError, "must be a captured input"):
            capability(exempt, files, self.root)
        report = evaluate_assembly_readiness(self.root, netlist, exempt, captured_files=files)
        self.assertEqual(report["outcome"], "unknown")
        self.assertIn("not a captured input", report["operating_temperature"]["exempt"][0]["reason"])
        cases = {"differs from its declared sha256": {"sha256": "0" * 64},
                 "quote is absent": {"line": 1}, "line 9": {"line": 9}}
        for reason, change in cases.items():
            with self.subTest(reason=reason):
                citation = {**exempt["operating_temperature"]["exemptions"]["R2"], **change}
                bad = {**params, "operating_temperature": {**spec, "exemptions": {"R2": citation}}}
                report = self.evaluate(netlist, bad)
                self.assertEqual(report["outcome"], "unknown")
                finding = next(f for f in report["findings"] if f["code"] == "operating_temperature_exemption_invalid")
                self.assertIn(reason, finding["message"])
        for ref in ("R9",):
            unpopulated = {**params, "operating_temperature": {**spec, "exemptions": self.exemption(
                ref, line=f"{ref} is accepted by the owner")}}
            report = self.evaluate(netlist, unpopulated)
            self.assertEqual(report["outcome"], "unknown")
            self.assertEqual(sorted(f["code"] for f in report["findings"]),
                             ["operating_temperature_exemption_invalid", "operating_temperature_unlisted"])
        # Declarations: the quote must name the exact reference, and a part is judged or exempt, not both.
        for bad in (self.exemption(line="R20 is accepted by the owner"), self.exemption(ref="R1"),
                    {"R2": {**self.exemption()["R2"], "extra": 1}}, {"R2": {**self.exemption()["R2"], "line": 0}},
                    {"R2": {**self.exemption()["R2"], "source": "../x.md"}}, {}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_params({**params, "operating_temperature": {**spec, "exemptions": bad}})
        with self.assertRaisesRegex(ValueError, "mutable deviations"):
            validate_params({**params, "deviations": "decisions/temperature.md",
                             "operating_temperature": {**spec, "exemptions": self.exemption()}})

    def test_operating_temperature_exemption_requires_affirmative_unconditional_statement(self):
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1"), part("R2")]))
        params = self.temperature_case()
        decision = self.root / "decisions/temperature.md"
        decision.parent.mkdir()
        cases = [
            ("R2 is NOT exempt: it must carry DigiKey operating-temperature evidence.", 1, "R2", "unknown"),
            ("R2 is exempt only if it is replaced by a 125C part.", 1, "R2 is exempt", "unknown"),
            ("R2 10k 0603", 1, "R2", "unknown"),
            ("R2 requires evidence. R3 is exempt.", 1, "R2", "unknown"),
            ("R2 is exempt from clearance checks.", 1, "R2", "unknown"),
            ("R2 is exempt from operating-temperature evidence.", 1, "R2", "pass"),
            ("R2 is exempted from operating-temperature evidence.", 1, "R2", "pass"),
            ("R2 is exempt\nonly if it is replaced by a 125C part.", 1, "R2 is exempt", "unknown"),
            ("R2 is exempt\nfrom neither temperature evidence nor review.", 1, "R2", "unknown"),
            ("# Only if replaced\nR2 is exempt.", 2, "R2", "unknown"),
            ("# Not exempt\nR2 is exempt.", 2, "R2", "unknown"),
            ("If replaced:\n\n- R2 is exempt.", 3, "R2", "unknown"),
            ("- If replaced:\n  - R2 is exempt.", 2, "R2", "unknown"),
            ("```\nR2 is exempt.\n```", 2, "R2", "unknown"),
            ("R2 is not considered exempt.", 1, "R2", "unknown"),
            ("R2 is exempt; that permission is revoked.", 1, "R2 is exempt", "unknown"),
            ("R2 might be exempt.", 1, "R2", "unknown"),
            ("R2 is exempt?", 1, "R2", "unknown"),
            ("R2 is exempt. R2 must carry evidence.", 1, "R2 is exempt", "unknown"),
            ("# Temperature exemptions\n- R2 is exempt from operating-temperature evidence.", 2, "R2", "pass"),
        ]
        for text, number, quote, expected in cases:
            with self.subTest(text=text):
                decision.write_text(text + "\n", encoding="utf-8")
                citation = {"source": "decisions/temperature.md", "sha256": digest(decision),
                            "line": number, "quote": quote}
                configured = {**params, "operating_temperature": {**params["operating_temperature"],
                                                                 "exemptions": {"R2": citation}}}
                files = {p.relative_to(self.root).as_posix(): digest(p)
                         for p in self.root.rglob("*") if p.is_file()}
                report = evaluate_assembly_readiness(self.root, netlist, configured, captured_files=files)
                self.assertEqual(report["outcome"], expected, report["findings"])
                entry = report["operating_temperature"]["exempt"][0]
                self.assertEqual(entry["status"], "exempt" if expected == "pass" else "unknown")
                if expected == "unknown":
                    self.assertIn("operating_temperature_exemption_invalid", [f["code"] for f in report["findings"]])
                    hot = {**configured, "operating_temperature": {**configured["operating_temperature"],
                                                                   "max_c": 125}}
                    defect = self.evaluate(netlist, hot)
                    self.assertEqual(defect["outcome"], "defect", defect["findings"])
                    self.assertIn("operating_temperature_out_of_range", [f["code"] for f in defect["findings"]])

    def test_operating_temperature_exemption_refuses_design_sources_and_longer_tokens(self):
        from agent_ee.assembly_readiness import capability, validate_params
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1"), part("R2")]))
        params = self.temperature_case()
        citation = self.exemption()["R2"]
        lines = (self.root / "temp.kicad_sch").read_text().splitlines()
        index = next(i for i, line in enumerate(lines) if '"Reference" "R2"' in line)
        schematic_citation = {"source": "temp.kicad_sch", "sha256": digest(self.root / "temp.kicad_sch"),
                              "line": index + 1, "quote": '"Reference" "R2"'}
        bad_citations = [schematic_citation]
        bad_citations.extend({**citation, "source": "design" + suffix} for suffix in
                             (".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_sym", ".KICAD_SCH"))
        bad_citations.extend(self.exemption(line=line)["R2"] for line in
                             ("R2-1 accepted", "R2-1 is exempt", "R1/R2 is exempt", "R2/R3 is exempt"))
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob("*") if p.is_file()}
        for bad in bad_citations:
            with self.subTest(citation=bad):
                configured = {**params, "operating_temperature": {**params["operating_temperature"],
                                                                 "exemptions": {"R2": bad}}}
                with self.assertRaises(ValueError):
                    validate_params(configured)
                with self.assertRaises(ValueError):
                    capability(configured, files, self.root)
                with self.assertRaises(AssemblyReadinessExecutionError) as cm:
                    self.evaluate(netlist, configured)
                self.assertEqual(cm.exception.execution, "unsupported")

    def test_digikey_package_must_bind_to_the_footprint(self):
        # AGENTEE-274: with a declared package map the DigiKey package must equal the footprint (ee29:R_0603).
        from tests.test_digikey_evidence import write_package_map
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1")]))

        def run(package="0603 (1608 Metric)", footprints=None, packages=None, pin=None):
            shutil.rmtree(self.root / "dk", ignore_errors=True)
            params = self.temperature_case(package=package)
            package_map = write_package_map(self.root, "maps/packages.json",
                                            footprints or {"ee29:R_0603": ("0603", ["resistor"])},
                                            packages or {"0603 (1608 Metric)": "0603", "0402 (1005 Metric)": "0402"})
            if pin:
                package_map["sha256"] = pin
            params["operating_temperature"]["package_map"] = package_map
            return self.evaluate(netlist, params)

        report = run()
        self.assertEqual(report["outcome"], "pass", report["findings"])
        entry = report["operating_temperature"]["components"][0]
        self.assertEqual(entry["package"]["status"], "pass")
        self.assertEqual(entry["package"]["package_source"]["rule"], "package_case")
        report = run("0402 (1005 Metric)")
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual([f["code"] for f in report["findings"] if f["severity"] == "defect"],
                         ["digikey_package_mismatch"])
        self.assertEqual(report["operating_temperature"]["components"][0]["status"], "unknown")
        for reason, kwargs in {"has no package": dict(package=None),
                               "footprint 'ee29:R_0603' is outside": dict(footprints={"ee29:R_0402": ("0402", ["resistor"])}),
                               "DigiKey package '0805 (2012 Metric)' is outside": dict(package="0805 (2012 Metric)"),
                               "package_map sha256": dict(pin="0" * 64)}.items():
            with self.subTest(reason=reason):
                report = run(**kwargs)
                self.assertEqual(report["outcome"], "unknown", report["findings"])
                self.assertIn(reason, " ".join(f["message"] for f in report["findings"]))

    def test_digikey_stock_never_counts_as_jlc_stock(self):
        netlist = self.export("temp.kicad_sch", schematic("temp", [part("R1")]))
        params = self.temperature_case()
        self.catalog([{"lcsc": "C25804", "stock": 0, "is_basic": True, "last_seen": "2026-10-15"}])
        report = self.evaluate(netlist, params)
        self.assertEqual(report["operating_temperature"]["components"][0]["status"], "pass")
        self.assertEqual(report["outcome"], "defect")
        self.assertEqual([f["code"] for f in report["findings"] if f["severity"] == "defect"], ["out_of_stock"])

    def test_operating_temperature_params_capability_and_run_checks(self):
        from agent_ee.assembly_readiness import capability, validate_params
        params = self.temperature_case()
        spec = params["operating_temperature"]
        for bad in ({**spec, "min_c": True}, {**spec, "max_c": float("nan")}, {**spec, "min_c": 80},
                    {k: v for k, v in spec.items() if k != "parts"}, {**spec, "extra": 1},
                    {**spec, "capture": "dk/other.json"}, {**spec, "parts": {}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_params({**params, "operating_temperature": bad})
        files = {p.relative_to(self.root).as_posix(): digest(p) for p in self.root.rglob("*") if p.is_file()}
        described = capability(params, files, self.root)
        self.assertIn("assembly:operating-temperature", described["predicates"])
        self.assertIn("dk/responses/PN-GENERIC.json", described["immutable_inputs"])
        self.assertNotIn("assembly:operating-temperature", capability({"catalog": params["catalog"]}, files)["predicates"])
        files.pop("dk/responses/PN-GENERIC.json")
        with self.assertRaisesRegex(ValueError, "must be a captured input"):
            capability(params, files, self.root)

        (self.root / "top.kicad_sch").write_text(schematic("top", [part("R1")]), encoding="utf-8")
        scope = {"schema": "agent-ee.scope/1", "schematic": "top.kicad_sch",
                 "inputs": [{"path": str(p), "at": p.relative_to(self.root).as_posix()}
                            for p in self.root.rglob("*") if p.is_file()],
                 "checks": [{"id": "asm", "kind": "assembly_readiness", "params": params}]}
        write_json(self.root / "scope.json", scope)
        run = self.root.parent / f"{self.root.name}-run"
        run.mkdir()
        self.addCleanup(shutil.rmtree, run, True)
        capture(self.root / "scope.json", run)
        result = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual((result["execution"], result["outcome"]), ("ok", "pass"), result["summary"])
        self.assertEqual(result["details"]["operating_temperature"]["components"][0]["status"], "pass")
        (run / "inputs/dk/responses/PN-GENERIC.json").write_bytes(b"{}")
        result = run_checks(run, time.monotonic() + 120)[0]
        self.assertEqual(result["outcome"], "unknown", result["summary"])

    def test_lcsc_stocknumber_rejected(self):
        doc = self.jlc_capture(self.root, {"C25804": (200, search(row("C25804", 500)))}, ["C25804"])
        # Even a response shaped like JLCPCB cannot promote LCSC figures.
        doc['source']['endpoint'] = 'https://wmsc.lcsc.com/wmsc/stockNumber'
        write_json(self.root / "jlc/catalog.json", doc)
        with self.assertRaisesRegex(AssemblyReadinessExecutionError, 'LCSC and other sources are refused'):
            load_catalog_snapshot(self.root, "jlc/catalog.json")
        doc['source']['endpoint'] = DEFAULT_ENDPOINT
        raw = search({**row('C25804', 500), 'stockNumber': 999999})
        (self.root / 'jlc/responses/C25804.json').write_bytes(raw)
        doc['requests'][0]['response'].update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        write_json(self.root / "jlc/catalog.json", doc)
        self.assertEqual(load_catalog_snapshot(self.root, "jlc/catalog.json")['parts']['C25804']['stock'], 500)
        del_row = row('C25804', 500)
        del del_row['stockCount']
        del_row['stockNumber'] = 999999
        from agent_ee.jlc_stock_capture import parse_search_response
        with self.assertRaisesRegex(ValueError, 'stockCount'):
            parse_search_response(search(del_row), 'C25804')

    def test_as_of_from_receipt(self):
        from agent_ee.cli import run_review, verify
        from agent_ee.stage_receipts import run_step
        source = self.root / 'source'
        source.mkdir()
        doc = self.jlc_capture(source, {"C25804": (200, search(row("C25804", 500)))}, ["C25804"])
        (source / 'top.kicad_sch').write_text(schematic('top', [part('R1')]))
        (source / 'top.kicad_pcb').write_text('(kicad_pcb (version 20221018))')
        spec = {'schema': 'agent-ee.stage-step/1', 'stage': 'schematic', 'step': 'generate',
                'argv': ['/usr/bin/cp', '/candidate/top.kicad_sch', '/work/top.kicad_sch'],
                'writes': {'top.kicad_sch': 'top.kicad_sch'}}
        receipt_dir = self.root / 'generation'
        receipt = run_step(receipt_dir, source, spec, {'pre_receipt': 1})
        self.assertEqual(receipt['status'], 'completed', receipt)
        link = {'path': 'generation/receipt.json', 'sha256': digest(receipt_dir / 'receipt.json'),
                'binding': receipt['binding']}
        scope = {'schema': 'agent-ee.scope/1', 'name': 'stock-receipt', 'scope': 'JLC stock receipt binding',
                 'inputs': [{'path': str(p), 'at': p.name} for p in sorted(source.iterdir())],
                 'schematic': 'top.kicad_sch', 'board': 'top.kicad_pcb',
                 'checks': [{'id': 'asm', 'kind': 'assembly_readiness',
                 'params': {'catalog': 'jlc/catalog.json', 'as_of': doc['as_of'], 'max_age_hours': 1000}}]}
        context = campaign_stock_context(self.root, {'history': []}, {'pre_receipts': [link]}, scope)
        self.assertEqual(context['receipt']['sha256'], link['sha256'])
        write_json(self.root / 'scope.json', scope)
        run = self.root / 'run'
        with contextlib.redirect_stdout(io.StringIO()):
            run_review(self.root / 'scope.json', run, 120, checks_only=True, stock_context=context)
        verify(run)
        result = read_json(run / 'checks/asm.json')
        self.assertEqual((result['execution'], result['outcome']), ('ok', 'pass'), result['summary'])
        stamp = datetime.fromtimestamp(receipt['started_unix'] + receipt['duration_s'], timezone.utc).isoformat()
        self.assertEqual(result['details']['catalog']['as_of'], stamp)
        self.assertEqual(result['details']['policy']['max_age_hours'], 24)
        verify_stock_context(run, {'checks': [result]}, context)

        # Campaign history selects its anchored generation output, ignoring later placement.
        work = self.root / 'implementations/1'
        work.mkdir(parents=True)
        shutil.copytree(receipt_dir, work / 'generation')
        placement = run_step(work / 'placement', source, {**spec, 'stage': 'placement'}, {'iteration': 1})
        self.assertEqual(placement['status'], 'completed', placement)
        index = work / 'stage-receipts.json'
        write_json(index, {'receipts': [
            {'path': 'generation/receipt.json', 'sha256': digest(work / 'generation/receipt.json')},
            {'path': 'placement/receipt.json', 'sha256': digest(work / 'placement/receipt.json')}]})
        history_state = {'history': [{}], 'stage_receipts': {'1': digest(index)}}
        history_context = campaign_stock_context(self.root, history_state, {}, scope)
        self.assertEqual(history_context['receipt']['sha256'], context['receipt']['sha256'])
        self.assertEqual(history_context['receipt']['path'], str(work / 'generation/receipt.json'))
        write_json(index, {'receipts': []})
        with self.assertRaisesRegex(ValueError, 'index changed'):
            campaign_stock_context(self.root, history_state, {}, scope)

        omitted = copy.deepcopy(result)
        del omitted['details']['stock_evaluation']
        with self.assertRaisesRegex(ValueError, 'lacks its evaluation witness'):
            verify_stock_context(run, {'checks': [omitted]}, context)
        # Forged gate fields cannot excuse an evaluated check whose witness was removed.
        gated = copy.deepcopy(omitted)
        gated['details'] = {'reason': 'stage_not_reached', 'stage': 'schematic',
                            'gate_findings': [{'check_id': 'asm', 'entity': 'R1', 'kind': 'stage_not_reached'}]}
        with self.assertRaisesRegex(ValueError, 'requires an immutable baseline'):
            verify_stock_context(run, {'checks': [gated]}, context)
        witness_path = run / 'work/asm/stock-evaluation.json'
        witness = read_json(witness_path)
        forged = copy.deepcopy(result)
        forged['details']['stock_evaluation']['as_of'] = doc['as_of']
        write_json(witness_path, forged['details']['stock_evaluation'])
        with self.assertRaisesRegex(ValueError, 'time differs'):
            verify_stock_context(run, {'checks': [forged]}, context)
        write_json(witness_path, witness)
        with self.assertRaisesRegex(ValueError, 'context differs'):
            verify_stock_context(run, {'checks': [result]}, {'receipt': None})
        # Caller backdates as_of to the capture; the actual generation time still makes it stale.
        stale = read_json(run / 'inputs/jlc/catalog.json')
        stale['as_of'] = self.shifted(stamp, -25)
        stale['completed_at'] = self.shifted(stamp, -24.5)
        stale['requests'][0]['fetched_at'] = stale['as_of']
        stale['parts']['C25804']['last_seen'] = stale['as_of']
        write_json(run / 'inputs/jlc/catalog.json', stale)
        result = run_checks(run, time.monotonic() + 120, stock_context=context)[0]
        self.assertEqual((result['execution'], result['outcome']), ('failed', 'unknown'))
        self.assertIn('stale', result['summary'])
        self.assertEqual(result['details']['response_verdicts']['C25804']['verdict'], 'verified')
        missing = run_checks(run, time.monotonic() + 120, stock_context={'receipt': None})[0]
        self.assertEqual((missing['execution'], missing['outcome']), ('failed', 'unknown'))
        self.assertIn('requires a generation stage receipt', missing['summary'])
        verify_stock_context(run, {'checks': [missing]}, {'receipt': None})
        promoted = copy.deepcopy(missing)
        promoted.update(execution='ok', outcome='pass')
        with self.assertRaisesRegex(ValueError, 'must remain failed/unknown'):
            verify_stock_context(run, {'checks': [promoted]}, {'receipt': None})
        # A changed anchor is never accepted, even when the stock itself is fresh.
        (receipt_dir / 'receipt.json').write_text('{}')
        changed = run_checks(run, time.monotonic() + 120, stock_context=context)[0]
        self.assertEqual((changed['execution'], changed['outcome']), ('failed', 'unknown'))
        self.assertIn('receipt changed', changed['summary'])



POD_GATE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/stock-stage-gate-pod-v5/run"


class StockStageGateReplayTests(unittest.TestCase):
    """AGENTEE-251: a pod v5 pre-stage gate result replays without a stock witness."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name) / "run"
        shutil.copytree(POD_GATE_FIXTURE, self.run)
        self.check = read_json(self.run / "checks/assembly.json")
        self.baseline = read_json(self.run / "baseline-id.json")

    def verify(self, check, baseline=None):
        verify_stock_context(self.run, {"checks": [check]}, None, baseline=baseline or self.baseline)

    def test_pod_v5_stage_not_reached_replays_without_stock_witness(self):
        self.assertEqual((self.check["execution"], self.check["outcome"], self.check["details"]["reason"]),
                         ("ok", "defect", "stage_not_reached"))
        self.assertEqual(len(self.check["details"]["gate_findings"]), 104)
        self.assertNotIn("stock_evaluation", self.check["details"])
        self.verify(self.check)

    def test_forged_stage_fields_raise(self):
        for key, value in (("stage", "routing"), ("reason", "stage_blocked")):
            forged = copy.deepcopy(self.check)
            forged["details"][key] = value
            # A forged stage_blocked has no earlier sealed check results to reconstruct from.
            with self.subTest(key=key), self.assertRaises((ValueError, OSError)):
                self.verify(forged)
        forged = copy.deepcopy(self.check)
        forged["details"]["extra"] = "x"
        with self.assertRaisesRegex(ValueError, "recomputed stage gate"):
            self.verify(forged)
        forged = copy.deepcopy(self.check)
        forged["summary"] = "verification: passed"
        with self.assertRaisesRegex(ValueError, "recomputed stage gate"):
            self.verify(forged)

    def test_tampered_gate_findings_raise(self):
        dropped = copy.deepcopy(self.check)
        dropped["details"]["gate_findings"].pop()
        renamed = copy.deepcopy(self.check)
        renamed["details"]["gate_findings"][0]["entity"] = "C999"
        duplicated = copy.deepcopy(self.check)
        duplicated["details"]["gate_findings"].append(duplicated["details"]["gate_findings"][0])
        for name, forged in (("dropped", dropped), ("renamed", renamed), ("duplicated", duplicated)):
            with self.subTest(name), self.assertRaises(ValueError):
                self.verify(forged)

    def test_tampered_presence_witness_or_reached_stage_raises(self):
        path = self.run / "work/assembly/stage-subject.json"
        witness = read_json(path)
        witness["missing"] = witness["missing"][1:]
        write_json(path, witness)
        with self.assertRaisesRegex(ValueError, "expected-content witness"):
            self.verify(self.check)
        shutil.copyfile(POD_GATE_FIXTURE / "work/assembly/stage-subject.json", path)
        self.verify(self.check)
        # Expected content now present on the board: the stage was reached, so absence is false.
        board = self.run / "inputs/project/telemetry-pod.kicad_pcb"
        text = board.read_text().rstrip().removesuffix(")")
        text += "".join(f' (footprint "Owned:Placeholder" (layer "F.Cu") (at 0 0)\n'
                        f'  (property "Reference" "{r}" (at 0 0 0) (layer "F.SilkS")))\n'
                        for r in read_json(path)["missing"])
        board.write_text(text + ")\n")
        with self.assertRaisesRegex(ValueError, "lacks successful native observation"):
            self.verify(self.check)

    def test_gate_fields_on_an_evaluated_check_raise(self):
        witness = {"schema": "agent-ee.stock-evaluation/1", "context": None}
        carried = copy.deepcopy(self.check)
        carried["details"]["stock_evaluation"] = witness
        with self.assertRaisesRegex(ValueError, "cannot carry a stock evaluation witness"):
            self.verify(carried)
        # Witness removed from the result after an actual evaluation: its sealed artifact remains.
        for name in ("stock-evaluation.json", "assembly-readiness.json", "design.xml"):
            artifact = self.run / "work/assembly" / name
            artifact.write_text("{}")
            with self.subTest(name), self.assertRaisesRegex(ValueError, "retains stock evaluation artifacts"):
                self.verify(self.check)
            artifact.unlink()
        cited = copy.deepcopy(self.check)
        cited["evidence"].append("work/assembly/assembly-readiness.json")
        with self.assertRaisesRegex(ValueError, "evidence beyond its stage gate"):
            self.verify(cited)

    def test_baseline_identity_and_ungated_absence_still_raise(self):
        with self.assertRaisesRegex(ValueError, "expected-content witness"):
            self.verify(self.check, {"baseline_id": "0" * 64})
        ungated = copy.deepcopy(self.check)
        del ungated["details"]["reason"]
        with self.assertRaisesRegex(ValueError, "lacks its evaluation witness"):
            self.verify(ungated)


if __name__ == "__main__":
    unittest.main()
