"""The kicad-happy output and install-record contract that agent-ee reads.

agent-ee's checks and the image installer (docker/kicad-happy/install.py) both
use these checks, so a release whose output changes shape under the same
envelope number fails the image build instead of reaching a run. Standard
library only: the image's kicad-happy build stage loads this file beside the
installer, before the agent-ee package is in the image.

The checks cover the fields that acceptance reads. A report must name its
analyzer and a listed envelope format and carry a findings list. A finding needs
a rule id and a known severity. When present, finding_id, source_path, status
and type are strings, suppressed is a boolean, assumptions is a list of strings,
and components, nets and pins are lists. The severity gate's summary counts must
match the findings. Other native fields stay informative.
"""
import re

ANALYZERS = {
    'schematic': 'skills/kicad/scripts/analyze_schematic.py',
    'pcb': 'skills/kicad/scripts/analyze_pcb.py',
    'thermal': 'skills/kicad/scripts/analyze_thermal.py',
    'emc': 'skills/emc/scripts/analyze_emc.py',
}
SEVERITY_ANALYZERS = {
    'schematic': ANALYZERS['schematic'],
    'pcb': ANALYZERS['pcb'],
    'cross_analysis': 'skills/kicad/scripts/cross_analysis.py',
    'emc': ANALYZERS['emc'],
}
SEVERITIES = ('error', 'warning', 'info')
INSTALL_RECORD_SCHEMA = 'agent-ee.kicad-happy-install/1'
# A release (2.3.2) or a prerelease (2.4.0-rc.1); nothing may follow.
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?')
SHA256 = re.compile('[0-9a-f]{64}')
COMMIT = re.compile('[0-9a-f]{40}')


class FormatError(ValueError):
    """Analyzer output or install metadata outside the contract agent-ee reads."""


def _text(value):
    return isinstance(value, str) and bool(value)


# Finding fields whose value decides acceptance, when present: severity-gate
# identity, eligibility (assumptions, source_path) and exclusion (suppressed, status),
# the RC calculation type, and the locators matched against declared components.
FINDING_FIELDS = {
    'finding_id': _text,
    'source_path': lambda v: isinstance(v, str),
    'assumptions': lambda v: isinstance(v, list) and all(_text(item) for item in v),
    'suppressed': lambda v: isinstance(v, bool),
    'status': lambda v: isinstance(v, str),
    'type': lambda v: isinstance(v, str),
    'components': lambda v: isinstance(v, list),
    'nets': lambda v: isinstance(v, list),
    'pins': lambda v: isinstance(v, list),
}


def check_report(analyzer, report, formats):
    """Return the report's envelope format; FormatError unless its read fields have their shapes."""
    if (not isinstance(report, dict) or report.get('schema_version') not in formats
            or report.get('analyzer_type') != analyzer
            or not isinstance(report.get('findings'), list)):
        raise FormatError(f'unrecognized {analyzer} analyzer schema')
    for index, finding in enumerate(report['findings']):
        if (not isinstance(finding, dict) or not _text(finding.get('rule_id'))
                or finding.get('severity') not in SEVERITIES):
            raise FormatError(f'unrecognized {analyzer} finding schema at /findings/{index}')
        for key, valid in FINDING_FIELDS.items():
            if key in finding and not valid(finding[key]):
                raise FormatError(f'unrecognized {analyzer} finding field {key} at /findings/{index}')
    return report['schema_version']


def check_summary(analyzer, report):
    """FormatError unless the summary's total and per-severity counts match the findings."""
    summary = report.get('summary')
    counts = {severity: 0 for severity in SEVERITIES}
    for finding in report['findings']:
        counts[finding['severity']] += 1
    by_severity = summary.get('by_severity') if isinstance(summary, dict) else None
    if (not isinstance(by_severity, dict) or type(summary.get('total_findings')) is not int
            or summary['total_findings'] != len(report['findings'])
            or set(by_severity) != set(SEVERITIES)
            or any(type(value) is not int or value != counts[severity]
                   for severity, value in by_severity.items())
            or type(summary.get('suppressed', 0)) is not int):
        raise FormatError(f'incomplete or contradictory {analyzer} severity summary')


def release_major(version):
    """The major number of a release or prerelease version string, else None."""
    match = VERSION.fullmatch(version) if isinstance(version, str) else None
    return int(match[1]) if match else None


def check_install_record(record, formats):
    """Return the record when its fields are exactly the installer's, each well formed."""
    fields = {'schema', 'source', 'tag', 'commit', 'version', 'output_format',
              'release_sha256', 'corrections', 'installation_sha256'}
    if not isinstance(record, dict) or set(record) != fields:
        raise FormatError('install record fields differ from ' + ', '.join(sorted(fields)))
    corrections = record['corrections']
    if (record['schema'] != INSTALL_RECORD_SCHEMA or not _text(record['source'])
            or release_major(record['version']) is None or record['tag'] != 'v' + record['version']
            or not isinstance(record['commit'], str) or not COMMIT.fullmatch(record['commit'])
            or record['output_format'] not in formats
            or any(not isinstance(record[key], str) or not SHA256.fullmatch(record[key])
                   for key in ('release_sha256', 'installation_sha256'))
            or not isinstance(corrections, list)
            or any(not isinstance(item, dict) or set(item) != {'origin', 'sha256'}
                   or not _text(item['origin']) or not isinstance(item['sha256'], str)
                   or not SHA256.fullmatch(item['sha256']) for item in corrections)):
        raise FormatError('install record has a malformed or unlisted field value')
    return record


def analyzer_args(name, schematic, pcb, work, ambient):
    """Arguments after the script path, as agent-ee runs analyzer `name` with outputs in `work`."""
    if name == 'schematic':
        args = [schematic]
    elif name == 'pcb':
        args = [pcb, '--full']
    else:
        args = ['--schematic', f'{work}/schematic.json', '--pcb', f'{work}/pcb.json']
    if name == 'thermal':
        args += ['--ambient', str(ambient)]
    args += ['--output', f'{work}/{name}.json']
    if name != 'cross_analysis':
        args += ['--config', f'{work}/config.json']
    return args
