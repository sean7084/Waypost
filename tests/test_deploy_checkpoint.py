"""Behavioural tests for the two deploy guards that block a release.

The checkpoint helper's mode resolution and the workflow's runner-drift gate are
the two places where a wrong answer stops a deploy — or, worse, lets one through
against the wrong database. `DeployScriptHygieneTests` asserts the scripts *contain*
the right strings; these tests exercise what they actually do.

The mode tests run the real bash helper with a `docker` shell function standing in
for the CLI, so they need no Docker, no database and no network. They are skipped
where bash is unavailable rather than silently passing.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / 'docker' / 'bin' / '_pg_checkpoint.sh'
WORKFLOW = REPO_ROOT / '.github' / 'workflows' / 'deploy-ecs.yml'
DEV_INSTALLER = REPO_ROOT / 'scripts' / 'deploy-dev-to-ecs.ps1'

BASH = shutil.which('bash')


def posix(path):
    """A path bash understands, including on Windows (C:/… -> /c/…)."""
    text = Path(path).as_posix()
    if os.name == 'nt' and re.match(r'^[A-Za-z]:/', text):
        return '/' + text[0].lower() + text[2:]
    return text


@unittest.skipUnless(BASH, 'bash is not available on this platform')
class CheckpointModeResolutionTests(SimpleTestCase):
    """`db_checkpoint_mode` maps the operator's intent to a branch.

    `missing` is auto with no running db service — today's "nothing to dump here".
    `invalid` must be distinguishable from every valid answer, because ci-deploy.sh
    dies on it and prints the value it rejected.
    """

    def resolve(self, *, env_mode=None, env_file=None, db_running=False):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / 'app'
            backup_dir = Path(tmp) / 'backups'
            app_dir.mkdir()
            backup_dir.mkdir()
            if env_file is not None:
                (app_dir / '.env').write_text(env_file, encoding='utf-8')

            # A shell function outranks anything on PATH, so no stub executable and
            # no executable bit are needed — which is what makes this work on Windows.
            script = f'''
set -euo pipefail
docker() {{
    if [ "$1" = "compose" ] && [ "$2" = "ps" ]; then
        if [ "${{STUB_DB_RUNNING:-0}}" = "1" ]; then
            printf 'db\\nredis\\n'
        fi
        return 0
    fi
    return 0
}}
log() {{ :; }}
APP_DIR="{posix(app_dir)}"
BACKUP_DIR="{posix(backup_dir)}"
source "{posix(HELPER)}"
db_checkpoint_mode
'''
            env = dict(os.environ)
            env.pop('DB_CHECKPOINT_MODE', None)
            env['STUB_DB_RUNNING'] = '1' if db_running else '0'
            if env_mode is not None:
                env['DB_CHECKPOINT_MODE'] = env_mode

            result = subprocess.run(
                [BASH, '-c', script], env=env, capture_output=True, text=True, timeout=60)

        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_explicit_modes_are_returned_verbatim(self):
        for mode in ('compose', 'external', 'none'):
            with self.subTest(mode=mode):
                self.assertEqual(self.resolve(env_mode=mode), mode)

    def test_explicit_mode_does_not_consult_docker(self):
        # external must not be reinterpreted as compose just because db is up:
        # that is the cutover deploy, the one that most needs the right database.
        self.assertEqual(self.resolve(env_mode='external', db_running=True), 'external')

    def test_unknown_mode_is_invalid_not_silently_auto(self):
        self.assertEqual(self.resolve(env_mode='rds'), 'invalid')

    def test_auto_follows_the_running_db_service(self):
        self.assertEqual(self.resolve(env_mode='auto', db_running=True), 'compose')
        self.assertEqual(self.resolve(env_mode='auto', db_running=False), 'missing')

    def test_unset_mode_defaults_to_auto(self):
        self.assertEqual(self.resolve(db_running=True), 'compose')
        self.assertEqual(self.resolve(db_running=False), 'missing')

    def test_env_file_supplies_the_mode_when_the_environment_is_silent(self):
        self.assertEqual(
            self.resolve(env_file='DB_CHECKPOINT_MODE=external\n'), 'external')

    def test_environment_wins_over_the_env_file(self):
        self.assertEqual(
            self.resolve(env_mode='none', env_file='DB_CHECKPOINT_MODE=external\n'), 'none')

    def test_env_file_value_is_stripped_of_quotes_spaces_and_cr(self):
        self.assertEqual(
            self.resolve(env_file='DB_CHECKPOINT_MODE="external" \r\n'), 'external')

    def test_commented_out_env_file_line_is_not_a_mode(self):
        self.assertEqual(
            self.resolve(env_file='# DB_CHECKPOINT_MODE=external\n', db_running=False),
            'missing')


class RunnerDriftGateTests(SimpleTestCase):
    """The gate that turns "the host ran the previous script" into a refusal.

    These are structural assertions on the workflow: Actions cannot be executed
    locally, so what is checkable is that the gate compares every installed script,
    runs before the deploy, and keeps both escape hatches explicit.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.workflow = WORKFLOW.read_text(encoding='utf-8')
        cls.installer = DEV_INSTALLER.read_text(encoding='utf-8')

    def test_gate_runs_before_the_deploy_step(self):
        gate = self.workflow.index('- name: Check the host-side runner matches this tag')
        deploy = self.workflow.index('- name: Run the deploy script on the ECS')
        self.assertLess(gate, deploy)

    def test_gate_compares_every_script_the_installers_put_on_the_host(self):
        loop = re.search(r'for NAME in ([^;]+); do', self.workflow)
        self.assertIsNotNone(loop, 'the drift gate must iterate the installed scripts')
        self.assertEqual(
            sorted(loop.group(1).split()),
            sorted(['ci-deploy.sh', '_pg_checkpoint.sh', 'waypost-backup.sh']))

    def test_both_sides_are_hashed_with_sha256(self):
        self.assertIn('LOCAL=$(sha256sum "docker/bin/${NAME}"', self.workflow)
        self.assertIn('REMOTE=$(ssh_run "sha256sum /srv/waypost/bin/${NAME}', self.workflow)

    def test_reinstall_is_an_explicit_dispatch_input(self):
        self.assertRegex(self.workflow, r'workflow_dispatch:\s*\n\s*inputs:')
        self.assertIn('reinstall_runner:', self.workflow)
        self.assertIn('REINSTALL: ${{ inputs.reinstall_runner }}', self.workflow)

    def test_drift_escape_hatch_is_a_named_variable_not_a_comment(self):
        self.assertIn('ALLOW_DRIFT: ${{ vars.ALLOW_RUNNER_DRIFT }}', self.workflow)
        self.assertIn('ALLOW_RUNNER_DRIFT is set', self.workflow)

    def test_a_tag_predating_a_script_is_not_refused_for_it(self):
        # Re-dispatching an old tag is the documented way to redeploy a release.
        self.assertIn('if [ ! -f "docker/bin/${NAME}" ]; then', self.workflow)
        self.assertIn('not part of this tag, skipped', self.workflow)

    def test_both_installers_agree_on_file_modes(self):
        # ci-deploy.sh runs over SSH (0755), waypost-backup.sh runs from cron and
        # reads .env (0750), the helper is only ever sourced (0640). Two installers
        # disagreeing here is how a script ends up unreadable by its own caller.
        for name, mode in (('ci-deploy.sh', '0755'), ('waypost-backup.sh', '0750')):
            with self.subTest(script=name):
                self.assertRegex(self.workflow, rf'{re.escape(name)}\) printf \'{mode}\'')
                self.assertRegex(self.installer, rf'{re.escape(name)}\)\s+install -m {mode}')
        self.assertRegex(self.workflow, r"\*\) printf '0640'")
        self.assertRegex(self.installer, r'\*\)\s+install -m 0640')