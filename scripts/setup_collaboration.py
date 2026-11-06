"""Explicit per-clone opt-in; doesn't create branches, commit or push."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def run(*args, check=True):
    return subprocess.run(['git', *args], capture_output=True, text=True, check=check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor', required=True, choices=list('ABCD'))
    parser.add_argument('--branch', help='Your existing personal branch; registered in this clone only')
    args = parser.parse_args()
    root = Path(run('rev-parse', '--show-toplevel').stdout.strip())
    previous = run('-C', str(root), 'config', '--get', 'core.hooksPath', check=False).stdout.strip()
    if previous and previous != '.githooks':
        print(f'Existing core.hooksPath={previous}; stopped without overwriting it.', file=sys.stderr)
        return 1
    for name in ['.githooks/pre-commit', 'scripts/check_scope.py', '.collaboration/ownership.json']:
        if run('-C', str(root), 'ls-files', '--error-unmatch', name, check=False).returncode:
            print(f'Commit/review the collaboration baseline before setup: {name}', file=sys.stderr)
            return 1
    mode = run('-C', str(root), 'ls-files', '--stage', '.githooks/pre-commit').stdout
    if os.name != 'nt' and not mode.startswith('100755 '):
        print('Maintainer must commit executable mode for .githooks/pre-commit before Unix setup.', file=sys.stderr)
        return 1
    if args.branch is not None:
        if (args.branch == 'master'
                or run('-C', str(root), 'check-ref-format', '--branch', args.branch, check=False).returncode
                or run('-C', str(root), 'show-ref', '--verify', f'refs/heads/{args.branch}', check=False).returncode):
            print('Register only your existing personal development branch; master is forbidden.', file=sys.stderr)
            return 1
    run('-C', str(root), 'config', '--local', 'collab.actor', args.actor)
    if args.branch is not None:
        run('-C', str(root), 'config', '--local', 'collab.branch', args.branch)
    run('-C', str(root), 'config', '--local', 'core.hooksPath', '.githooks')
    print(f'Configured this clone: actor={args.actor}, hooksPath=.githooks')
    print('Use your own personal branch (register with --branch) or feature/<actor>/<task>. Never develop on master.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
