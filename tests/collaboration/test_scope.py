import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('scope', ROOT / 'scripts/check_scope.py')
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)
POLICY = json.loads((ROOT / '.collaboration/ownership.json').read_text(encoding='utf-8'))


class PolicyTests(unittest.TestCase):
    def test_master_governance_access_preserves_business_owners(self):
        self.assertIn('MASTER', POLICY['roles'])
        for path in ['AGENTS.md', 'README.md', '.collaboration/DECISIONS.md',
                     'scripts/check_scope.py', 'tests/collaboration/test_scope.py',
                     'backend/app/modules/training/AGENTS.md']:
            self.assertEqual(scope.owner_of(path, POLICY), 'A', path)
            self.assertEqual(scope.check_paths([path], 'MASTER', POLICY), [], path)
        for path in ['docs/updates/MASTER/new.md', 'docs/requests/MASTER/new.md',
                     'docs/modules/MASTER/role.md', 'tasks/MASTER.md']:
            self.assertEqual(scope.owner_of(path, POLICY), 'MASTER', path)

    def test_master_cannot_edit_business_or_another_roles_records(self):
        for path in ['backend/app/main.py', 'frontend/src/components/AppShell.tsx',
                     'backend/app/modules/talents/service.py',
                     'backend/app/modules/training/service.py',
                     'backend/app/tasks/runner.py', 'docs/updates/A/old.md',
                     'tasks/C.md', 'docs/modules/C/一期需求细化.md']:
            self.assertTrue(scope.check_paths([path], 'MASTER', POLICY), path)

    def test_master_still_rejects_private_readonly_and_unknown_paths(self):
        for path in ['.env', '.collaboration/.env', '.git/config',
                     'docs/imports/AGENTS.md', 'docs/requirements/sources/AGENTS.md',
                     'docs/AGENTS.override.md', 'unassigned.md']:
            self.assertTrue(scope.check_paths([path], 'MASTER', POLICY), path)

    def test_imported_next_routes_have_one_business_owner(self):
        examples = {
            'frontend/src/app/(main)/talents/page.tsx': 'B',
            'frontend/src/app/(main)/screen/UploadPanel.tsx': 'B',
            'frontend/src/app/(main)/screen/ScreenBatchPanel.tsx': 'A',
            'frontend/src/app/(main)/screen/page.tsx': 'A',
            'frontend/src/lib/usePolling.ts': 'D',
        }
        for path, expected in examples.items():
            self.assertEqual(scope.owner_of(path, POLICY), expected, path)
            for other in set('ABCD') - {expected}:
                self.assertTrue(scope.check_paths([path], other, POLICY), (path, other))

    def test_only_named_environment_templates_are_allowed(self):
        for path in ['backend/.env.example', 'frontend/.env.local.example']:
            self.assertEqual(scope.owner_of(path, POLICY), 'A', path)
        for path in ['backend/.env', 'frontend/.env.local', 'frontend/.env.production',
                     'frontend/.env.production.example', 'backend/backups/db.sql',
                     'frontend/.next/server.js', 'backend/data/eval/report.json']:
            self.assertIsNone(scope.owner_of(path, POLICY), path)

    def test_imported_mixed_backend_and_reference_boundaries(self):
        examples = {
            'backend/app/tasks/screen_worker.py': 'A',
            'backend/app/tasks/parse_worker.py': 'B',
            'backend/app/tasks/runner.py': 'D',
            'backend/app/core/prompts/match.txt': 'A',
            'backend/app/core/prompts/parse.txt': 'B',
            'backend/app/core/prompts/loader.py': 'D',
        }
        for path, owner in examples.items():
            self.assertEqual(scope.owner_of(path, POLICY), owner, path)
        self.assertIsNone(scope.owner_of('backend/app/service/unassigned.py', POLICY))
        self.assertIsNone(scope.owner_of('docs/imports/hhy_gycs_master/README.md', POLICY))

    def test_each_role_owns_its_module_and_records(self):
        examples = {'A': 'matching', 'B': 'talents', 'C': 'assessments', 'D': 'analytics'}
        for actor, module in examples.items():
            for path in [f'backend/app/modules/{module}/service.py',
                         f'frontend/src/features/{module}/Page.tsx',
                         f'docs/updates/{actor}/20261106-note.md', f'tasks/{actor}.md',
                         f'backend/migrations/versions/{actor}_001_example.py']:
                self.assertEqual(scope.check_paths([path], actor, POLICY), [])
                for other in set('ABCD') - {actor}:
                    self.assertTrue(scope.check_paths([path], other, POLICY), (other, path))

    def test_private_and_unknown_paths_rejected_for_everyone(self):
        for path in ['.env', 'backend/.env.local', 'data/report.pdf', '.idea/misc.xml',
                     'docs/requirements/sources/需求文档.md', 'docs/test/seed_data.sql',
                     'frontend/src/features/talents2/Page.tsx', 'new_unknown.py',
                     'backend/app/core/authentication/x.py']:
            for actor in 'ABCD':
                self.assertTrue(scope.check_paths([path], actor, POLICY), (actor, path))

    def test_governance_cannot_be_rewritten_by_module_owner(self):
        self.assertEqual(scope.owner_of('backend/app/modules/talents/AGENTS.md', POLICY), 'A')
        self.assertTrue(scope.check_paths(['backend/app/modules/talents/AGENTS.md'], 'B', POLICY))
        self.assertIsNone(scope.owner_of('backend/app/modules/talents/AGENTS.override.md', POLICY))

    def test_unsafe_paths_and_migration_prefixes(self):
        for path in ['../x', '/x', 'C:/x', 'backend/../x', 'a\\b', 'a//b']:
            with self.assertRaises(scope.ScopeError):
                scope.owner_of(path, POLICY)
        self.assertIsNone(scope.owner_of('backend/migrations/versions/C_extra/A_001.py', POLICY))

    def test_rename_parser_checks_both_paths(self):
        raw = b'R100\x00backend/app/modules/talents/old.py\x00backend/app/modules/training/new.py\x00'
        paths = scope.parse_diff(raw)
        self.assertEqual(len(paths), 2)
        self.assertTrue(scope.check_paths(paths, 'B', POLICY))
        self.assertTrue(scope.check_paths(paths, 'C', POLICY))


@unittest.skipUnless(shutil.which('git'), 'Git required')
class GitIntegrationTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True)

    def put(self, path, text='example\n'):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf-8')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='talent-scope-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Scope Test')
        self.git('config', 'user.email', 'scope-test@example.invalid')
        self.put('.collaboration/ownership.json', json.dumps(POLICY))
        for name in ['scripts/check_scope.py', 'scripts/setup_collaboration.py', '.githooks/pre-commit']:
            self.put(name, (ROOT / name).read_text(encoding='utf-8'))
        self.put('backend/app/modules/talents/old.py')
        self.put('docs/updates/B/old.md', '# Old recorded result\n')
        self.git('add', '--all')  # Disposable test repo only.
        self.git('update-index', '--chmod=+x', '.githooks/pre-commit')
        self.git('commit', '-m', 'test baseline')
        self.git('switch', '-c', 'feature/B/parser')

    def run_checker(self, *args):
        return subprocess.run([sys.executable, str(self.repo / 'scripts/check_scope.py'),
                               '--actor', 'B', *args], cwd=self.repo, capture_output=True,
                              text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})

    def new_update(self):
        self.put('docs/updates/B/20261106-120000-result.md', '# Real result\n')

    def stage(self):
        self.git('add', '--all')  # Disposable test repo only.

    def personal_branch(self):
        self.git('switch', '-c', 'member-b')
        self.git('config', '--local', 'collab.actor', 'B')
        self.git('config', '--local', 'collab.branch', 'member-b')

    def test_own_change_and_new_update_pass(self):
        self.put('backend/app/modules/talents/中文 文件.py')
        self.new_update()
        self.stage()
        result = self.run_checker('--staged')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_other_module_is_blocked(self):
        self.put('backend/app/modules/training/service.py')
        self.new_update()
        self.stage()
        self.assertNotEqual(self.run_checker('--staged').returncode, 0)

    def test_missing_new_update_blocked(self):
        self.put('backend/app/modules/talents/service.py')
        self.stage()
        self.assertNotEqual(self.run_checker('--staged').returncode, 0)

    def test_rewriting_previous_update_is_blocked(self):
        self.put('docs/updates/B/old.md', '# silently rewritten\n')
        self.new_update()
        self.stage()
        self.assertNotEqual(self.run_checker('--staged').returncode, 0)

    def test_cross_module_move_blocked(self):
        (self.repo / 'backend/app/modules/training').mkdir(parents=True)
        self.git('mv', 'backend/app/modules/talents/old.py', 'backend/app/modules/training/moved.py')
        self.new_update()
        self.stage()
        self.assertNotEqual(self.run_checker('--staged').returncode, 0)

    def test_own_delete_with_update_passes(self):
        self.git('rm', 'backend/app/modules/talents/old.py')
        self.new_update()
        self.stage()
        result = self.run_checker('--staged')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_role_branch_mismatch_blocked(self):
        self.git('switch', '-c', 'feature/C/another')
        self.assertNotEqual(self.run_checker('--staged').returncode, 0)

    def test_master_blocked_for_every_role(self):
        self.git('switch', '-c', 'master')
        self.git('config', '--local', 'collab.branch', 'master')
        for actor in [*'ABCD', 'MASTER']:
            self.git('config', '--local', 'collab.actor', actor)
            result = self.run_checker('--actor', actor, '--staged')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Development on master is forbidden', result.stderr)

    def test_master_setup_governance_commit_and_business_rejection(self):
        self.git('switch', '-c', 'reviewer-personal')
        setup = subprocess.run([sys.executable, str(self.repo / 'scripts/setup_collaboration.py'),
                                '--actor', 'MASTER', '--branch', 'reviewer-personal'],
                               cwd=self.repo, capture_output=True)
        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(self.git('config', '--local', '--get', 'collab.actor').stdout.strip(), b'MASTER')
        self.put('scripts/check_scope.py', (ROOT / 'scripts/check_scope.py').read_text(encoding='utf-8') + '\n# reviewed rule\n')
        self.put('docs/updates/MASTER/new.md', '# Actual verification\n')
        self.stage()
        result = self.run_checker('--actor', 'MASTER', '--staged')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.git('commit', '-m', 'reviewed governance')
        self.assertEqual(self.run_checker('--actor', 'MASTER', '--base', 'main').returncode, 0)
        self.put('backend/app/modules/training/forbidden.py')
        self.stage()
        rejected = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'reject business edit'], capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(b'owner=C, actor=MASTER', rejected.stderr)

    def test_master_tool_changes_require_own_new_record(self):
        self.git('switch', '-c', 'docs/MASTER/review')
        self.put('scripts/check_scope.py', (ROOT / 'scripts/check_scope.py').read_text(encoding='utf-8') + '\n# rule change\n')
        self.stage()
        result = self.run_checker('--actor', 'MASTER', '--staged')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('NEW docs/updates/MASTER/', result.stderr)

    def test_registered_personal_branch_own_change_and_update_pass(self):
        self.personal_branch()
        self.put('backend/app/modules/talents/service.py')
        self.new_update()
        self.stage()
        result = self.run_checker('--staged')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_registered_personal_branch_cross_scope_blocked(self):
        self.personal_branch()
        self.put('backend/app/modules/training/other.py')
        self.new_update()
        self.stage()
        result = self.run_checker('--staged')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('owner=C, actor=B', result.stderr)

    def test_registered_personal_branch_missing_update_blocked(self):
        self.personal_branch()
        self.put('backend/app/modules/talents/service.py')
        self.stage()
        result = self.run_checker('--staged')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Code changes require a NEW', result.stderr)

    def test_master_yb_is_not_globally_allowed(self):
        self.git('switch', '-c', 'master_YB')
        self.assertNotEqual(self.run_checker('--staged').returncode, 0)

    def test_personal_branch_does_not_accept_another_actor(self):
        self.personal_branch()
        self.assertNotEqual(self.run_checker('--actor', 'C', '--staged').returncode, 0)

    def test_setup_registers_personal_branch_locally(self):
        self.git('switch', '-c', 'member-b')
        setup = subprocess.run([sys.executable, str(self.repo / 'scripts/setup_collaboration.py'),
                                '--actor', 'B', '--branch', 'member-b'], cwd=self.repo, capture_output=True)
        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.assertEqual(self.git('config', '--local', '--get', 'collab.branch').stdout.strip(), b'member-b')
        self.assertEqual(self.run_checker('--staged').returncode, 0)

    def test_setup_rejects_master_without_config_changes(self):
        self.git('branch', 'master')
        setup = subprocess.run([sys.executable, str(self.repo / 'scripts/setup_collaboration.py'),
                                '--actor', 'B', '--branch', 'master'], cwd=self.repo, capture_output=True)
        self.assertNotEqual(setup.returncode, 0)
        for key in ['collab.actor', 'collab.branch', 'core.hooksPath']:
            result = subprocess.run(['git', '-C', str(self.repo), 'config', '--local', '--get', key], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_committed_branch_range_checked(self):
        self.put('backend/app/modules/talents/service.py')
        self.new_update()
        self.stage()
        self.git('commit', '-m', 'own module')
        result = self.run_checker('--base', 'main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.put('backend/app/modules/training/other.py')
        self.stage()
        self.git('commit', '-m', 'cross boundary')  # Hook deliberately not installed in this repo.
        self.assertNotEqual(self.run_checker('--base', 'main').returncode, 0)

    def test_setup_and_hook_reject_cross_scope(self):
        setup = subprocess.run([sys.executable, str(self.repo / 'scripts/setup_collaboration.py'),
                                '--actor', 'B'], cwd=self.repo, capture_output=True)
        self.assertEqual(setup.returncode, 0, setup.stderr)
        self.put('backend/app/modules/training/other.py')
        self.new_update()
        self.stage()
        result = subprocess.run(['git', '-C', str(self.repo), 'commit', '-m', 'should reject'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Scope check failed', result.stderr)

    def test_setup_preserves_existing_hooks_path(self):
        self.git('config', 'core.hooksPath', 'existing-hooks')
        setup = subprocess.run([sys.executable, str(self.repo / 'scripts/setup_collaboration.py'),
                                '--actor', 'B'], cwd=self.repo, capture_output=True)
        self.assertNotEqual(setup.returncode, 0)
        self.assertEqual(self.git('config', '--get', 'core.hooksPath').stdout.strip(), b'existing-hooks')

    def test_missing_actor_fails_closed(self):
        result = subprocess.run([sys.executable, str(self.repo / 'scripts/check_scope.py'), '--staged'],
                                cwd=self.repo, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
