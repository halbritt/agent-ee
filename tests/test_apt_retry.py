"""Outer apt retry contract; subprocess doubles only replace waits and apt IO."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AptRetryTests(unittest.TestCase):
    def test_empty_command_is_refused_before_attempting_or_waiting(self):
        result = subprocess.run(['/bin/sh', str(ROOT / 'docker/apt-retry.sh')],
                                env=dict(os.environ, PATH=''),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'apt-retry: a command is required\n')

    def test_final_stage_removes_helper_after_last_apt_call(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        final_stage = dockerfile.rsplit('FROM native AS toolchain\n', 1)[1]
        removal = '    rm -f /usr/local/bin/apt-retry; \\\n'
        self.assertEqual(final_stage.count(removal), 1)
        before, after = final_stage.split(removal)
        self.assertIn('apt-retry apt-get ', before)
        self.assertNotIn('apt-retry', after)
        self.assertIn('apt-mark hold zstd; \\\n' + removal, final_stage)

    def test_hosted_build_persists_intermediate_layers_and_loads_local_image(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assert_hosted_cache_workflow(workflow)

    def assert_hosted_cache_workflow(self, workflow):
        setup = workflow.split('- name: Set up Buildx for the hosted layer cache', 1)[1]
        setup = setup.split('\n      - name:', 1)[0]
        self.assertRegex(setup, r'(?m)^          version: v[0-9]+\.[0-9]+\.[0-9]+$')
        self.assertRegex(setup, r'(?m)^          driver-opts: image=moby/buildkit@sha256:[0-9a-f]{64}$')
        build = workflow.split('- name: Build the image from the checkout', 1)[1]
        build = build.split('\n      - name:', 1)[0]
        self.assertIn('uses: docker/setup-buildx-action@', workflow)
        self.assertIn('driver: docker-container', workflow)
        self.assertIn('uses: docker/build-push-action@', build)
        for setting in ('context: .', 'load: true', 'push: false',
                        'tags: ${{ env.IMAGE }}', "DOCKER_BUILD_RECORD_UPLOAD: 'false'"):
            self.assertIn(setting, build)
        toolchain = workflow.split('- name: Cache the pinned toolchain stages', 1)[1]
        toolchain = toolchain.split('\n      - name:', 1)[0]
        self.assertIn('target: toolchain', toolchain)
        self.assertIn("DOCKER_BUILD_RECORD_UPLOAD: 'false'", toolchain)
        scope = 'type=gha,version=2,scope=agent-ee-native-amd64-v1'
        self.assertIn('cache-from: ' + scope + '\n', build)
        self.assertIn('cache-to: ' + scope + ',mode=max,ignore-error=true\n', toolchain)
        self.assertNotIn('cache-to:', build)
        dockerfile = (ROOT / 'Dockerfile').read_text()
        cached, source = dockerfile.split('FROM toolchain\n', 1)
        self.assertNotIn('COPY .', cached)
        self.assertIn('COPY . /opt/agent-ee', source)
        self.assertNotIn('continue-on-error', toolchain)
        for bypass in ('no-cache:', 'build-args:', 'continue-on-error', 'ignore-error=true'):
            self.assertNotIn(bypass, build)

    def test_hosted_cache_contract_rejects_mutated_safeguards(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        export = 'cache-to: type=gha,version=2,scope=agent-ee-native-amd64-v1,mode=max,ignore-error=true'
        version = next(line for line in workflow.splitlines() if 'version: v' in line)
        image = next(line for line in workflow.splitlines() if 'image=moby/buildkit@' in line)
        mutations = {
            'missing export tolerance': workflow.replace(',ignore-error=true', ''),
            'disabled export tolerance': workflow.replace('ignore-error=true', 'ignore-error=false'),
            'tolerance on import': workflow.replace(export, export.removesuffix(',ignore-error=true')).replace(
                'cache-from: type=gha,version=2,scope=agent-ee-native-amd64-v1',
                'cache-from: type=gha,version=2,scope=agent-ee-native-amd64-v1,ignore-error=true'),
            'missing buildx pin': workflow.replace(version + '\n', ''),
            'floating buildx': workflow.replace(version, '          version: latest'),
            'missing daemon pin': workflow.replace(image + '\n', ''),
            'floating daemon': workflow.replace(image, '          driver-opts: image=moby/buildkit:buildx-stable-1'),
            'truncated daemon digest': workflow.replace(image, image[:-1]),
        }
        for step in ('toolchain', 'build'):
            mutations[step + ' continue-on-error'] = workflow.replace(
                '        id: ' + step + '\n',
                '        id: ' + step + '\n        continue-on-error: true\n')
        for name, mutated in mutations.items():
            with self.subTest(mutation=name):
                self.assertNotEqual(mutated, workflow)
                with self.assertRaises(AssertionError):
                    self.assert_hosted_cache_workflow(mutated)

    def run_retry(self, failures, status=100):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, body in {
                'sleep': 'echo "sleep $*" >> "$LOG"',
                'rm': 'echo "rm $*" >> "$LOG"',
                'apt-get': '''n=$(cat "$COUNT" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$COUNT"
echo "apt $*" >> "$LOG"
[ "$n" -gt "$FAILURES" ] || exit "$STATUS"
''',
            }.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + ':' + os.environ['PATH'],
                       LOG=str(root / 'log'), COUNT=str(root / 'count'),
                       FAILURES=str(failures), STATUS=str(status))
            result = subprocess.run(['sh', str(ROOT / 'docker/apt-retry.sh'),
                                     'apt-get', 'install', 'package=1.2'],
                                    env=env, capture_output=True, text=True, timeout=10)
            return result, (root / 'log').read_text().splitlines()

    def test_success_needs_no_wait_or_cleanup(self):
        result, log = self.run_retry(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log, ['apt install package=1.2'])
        self.assertIn('attempt 1/4', result.stderr)

    def test_recovers_after_each_possible_failure_count(self):
        for failures in (1, 2, 3):
            with self.subTest(failures=failures):
                result, log = self.run_retry(failures)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([s for s in log if s.startswith('sleep ')],
                                 ['sleep ' + s for s in ('60', '120', '240')[:failures]])
                self.assertEqual(log.count('apt install package=1.2'), failures + 1)
                self.assertEqual(sum(s.startswith('rm ') for s in log), failures)
                self.assertNotIn('exhausted', result.stderr)

    def test_exhaustion_preserves_exit_status_and_complete_indexes(self):
        for status in (100, 42):
            with self.subTest(status=status):
                result, log = self.run_retry(99, status)
                self.assertEqual(result.returncode, status)
                self.assertEqual(log.count('apt install package=1.2'), 4)
                self.assertEqual([s for s in log if s.startswith('sleep ')],
                                 ['sleep 60', 'sleep 120', 'sleep 240'])
                cleanups = [s for s in log if s.startswith('rm ')]
                self.assertEqual(cleanups, ['rm -rf /var/lib/apt/lists/partial/* '
                                          '/var/cache/apt/archives/partial/*'] * 3)
                self.assertIn('exhausted 4 attempts', result.stderr)

    def test_every_docker_apt_call_uses_shared_wrapper_and_strict_update(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        calls = [s.strip() for s in dockerfile.splitlines() if 'Acquire::Retries=' in s]
        self.assertEqual(len(calls), 8)
        for call in calls:
            self.assertTrue(call.startswith('apt-retry apt-get '), call)
            self.assertIn('-o Acquire::Retries=5 -o Acquire::Retries::Delay=true '
                          '-o Acquire::Retries::Delay::Maximum=8', call)
            if 'update;' in call:
                self.assertIn('-o APT::Update::Error-Mode=any update;', call)
        self.assertIn('COPY --chmod=755 docker/apt-retry.sh /usr/local/bin/apt-retry', dockerfile)
        self.assertEqual(dockerfile.count('apt-get '), len(calls) + 1)  # pin comment
