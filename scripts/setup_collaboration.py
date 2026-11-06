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
    run('-C', str(root), 'config', '--local', 'collab.actor', args.actor)
    run('-C', str(root), 'config', '--local', 'core.hooksPath', '.githooks')
    print(f'Configured this clone: actor={args.actor}, hooksPath=.githooks')
    print('Use your own clone; change to your feature/<actor>/<task> branch before committing.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
