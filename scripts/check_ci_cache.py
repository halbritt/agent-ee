#!/usr/bin/env python3
"""Probe signed apt layer reuse offline; usage: python3 scripts/check_ci_cache.py LOG_DIR.

Uses the default Docker builder and a small bootstrap stage, not the full image
or GitHub's remote cache service. Leaves agent-ee:ee319-cache-probe for inspection.
"""
import argparse
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log_directory', type=Path)
    root = parser.parse_args().log_directory.resolve() / 'cache-probe'
    root.mkdir(parents=True, exist_ok=True)
    # Real signed apt metadata and the same TLS bootstrap pin as the production stage.
    # Only the source payload changes between warm and hit, like successive CI runs.
    (root / 'Dockerfile').write_text('''ARG UBUNTU_DIGEST
FROM ubuntu:24.04@${UBUNTU_DIGEST} AS native
ARG CA_CERTIFICATES_VERSION=20240203
RUN rm -f /etc/apt/sources.list /etc/apt/sources.list.d/*; \\
    printf '%s\\n' 'Types: deb' 'URIs: http://archive.ubuntu.com/ubuntu' \\
    'Suites: noble' 'Components: main' \\
    'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' \\
    > /etc/apt/sources.list.d/bootstrap.sources; \\
    apt-get -o Acquire::http::Timeout=5 -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update && \\
    apt-get install -y --no-install-recommends "ca-certificates=${CA_CERTIFICATES_VERSION}"
FROM native
COPY source /source
RUN test -s /source
''')
    repo = Path(__file__).resolve().parents[1]
    base = re.search(r'^ARG UBUNTU_DIGEST=(.+)$', (repo / 'Dockerfile').read_text(), re.M)[1]
    package = re.search(r'^ARG CA_CERTIFICATES_VERSION=(.+)$', (repo / 'Dockerfile').read_text(), re.M)[1]
    fixture = root / 'Dockerfile'
    fixture.write_text(fixture.read_text().replace('ARG UBUNTU_DIGEST\n', 'ARG UBUNTU_DIGEST=' + base + '\n'))
    common = ['docker', 'buildx', 'build', '--builder', 'default', '--progress=plain',
              '--build-arg', 'UBUNTU_DIGEST=' + base, '--build-arg', 'CA_CERTIFICATES_VERSION=' + package,
              '--build-arg', 'BUILDKIT_INLINE_CACHE=1',
              '--load', '-t', 'agent-ee:ee319-cache-probe']
    for label, flags, source, expected in (
        ('warm', ['--network=default', '--no-cache'], 'first checkout', 0),
        ('hit', ['--network=default', '--build-arg', 'http_proxy=http://127.0.0.1:9',
                 '--cache-from', 'agent-ee:ee319-cache-probe'], 'second checkout', 0),
        ('miss', ['--network=default', '--build-arg', 'http_proxy=http://127.0.0.1:9',
                  '--build-arg', 'CA_CERTIFICATES_VERSION=0-agent-ee-cache-miss',
                  '--cache-from', 'agent-ee:ee319-cache-probe'], 'second checkout', 1),
    ):
        (root / 'source').write_text(source)
        result = subprocess.run(common + flags + [str(root)], capture_output=True, text=True, timeout=600)
        log = result.stdout + result.stderr
        (root.parent / (label + '.log')).write_text(log)
        print(label, 'exit', result.returncode, flush=True)
        if expected == 0:
            if result.returncode != 0:
                raise RuntimeError(log)
        else:
            if (result.returncode == 0 or 'Failed to fetch' not in log
                    or '127.0.0.1:9' not in log or 'Connection refused' not in log
                    or 'exit code: 100' not in log):
                raise RuntimeError('cache miss did not fail at apt transport: ' + log)
        if label == 'hit':
            step = re.search(r'#(\d+) \[native 2/2\] RUN', log)
            if step is None or '#' + step[1] + ' CACHED' not in log:
                raise RuntimeError('apt step was not cached: ' + log)
        print(log[-1800:], flush=True)


if __name__ == '__main__':
    main()
