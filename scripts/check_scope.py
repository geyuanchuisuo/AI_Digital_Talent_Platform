"""Check changed paths against role ownership; not an identity/security boundary."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys


class ScopeError(Exception):
    pass


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        raise ScopeError(result.stderr.decode('utf-8', errors='replace').strip())
    return result.stdout


def normalize(path):
    if not path or '\\' in path or path.startswith('/') or ':' in path:
        raise ScopeError(f'Unsafe path: {path!r}')
    parts = path.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ScopeError(f'Unsafe path: {path!r}')
    return path


def matches(path, rule):
    if rule.endswith('/**'):
        return path.startswith(rule[:-2])
    if '*' not in rule:
        return path == rule
    # Single-segment wildcard, used for migration filenames only.
    parent, pattern = rule.rsplit('/', 1)
    return str(PurePosixPath(path).parent) == parent and PurePosixPath(path).match(pattern)


def owner_of(path, policy):
    normalize(path)
    folded = path.casefold()
    parts = folded.split('/')
    if any(p in policy['blocked_segments'] for p in parts):
        return None
    name = parts[-1]
    if name == '.env' or (name.startswith('.env.') and name != '.env.example'):
        return None
    if name.endswith(('.pem', '.key', '.p12')):
        return None
    if any(matches(folded, p.casefold()) for p in policy['readonly']):
        return None
    # Governance is always A-maintained, including rules inside B/C/D modules.
    if name in ('agents.md', 'claude.md') or name.startswith('agents.override.'):
        return 'A' if name in ('agents.md', 'claude.md') else None
    for actor, rules in policy['owners'].items():
        if any(matches(path, rule) for rule in rules):
            return actor
    return None


def parse_diff(data):
    fields = data.decode('utf-8', errors='strict').split('\0')
    if fields and not fields[-1]:
        fields.pop()
    paths = []
    i = 0
    while i < len(fields):
        status = fields[i]
        count = 2 if status.startswith(('R', 'C')) else 1
        if i + count >= len(fields):
            raise ScopeError('Malformed git name-status output')
        paths.extend(fields[i + 1:i + count + 1])
        i += count + 1
    return paths


def check_paths(paths, actor, policy):
    errors = []
    for path in sorted(set(paths)):
        owner = owner_of(path, policy)
        if owner != actor:
            errors.append(f'{path}: owner={owner or "READONLY/UNASSIGNED"}, actor={actor}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actor', choices=list('ABCD'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--staged', action='store_true')
    mode.add_argument('--base', help='Compare base...HEAD (committed branch changes)')
    args = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
        actor = args.actor
        if not actor:
            value = subprocess.run(['git', '-C', str(root), 'config', '--get', 'collab.actor'], capture_output=True)
            actor = value.stdout.decode().strip()
        if actor not in 'ABCD' or len(actor) != 1:
            raise ScopeError('Set collab.actor to A/B/C/D or pass --actor; never guess your role.')
        policy = json.loads((root / '.collaboration/ownership.json').read_text(encoding='utf-8'))
        branch = git(root, 'branch', '--show-current').decode().strip()
        if branch == 'master':
            raise ScopeError('Development on master is forbidden for everyone; switch to your development branch.')
        personal_branch = subprocess.run(['git', '-C', str(root), 'config', '--local', '--get', 'collab.branch'], capture_output=True).stdout.decode().strip()
        local_actor = subprocess.run(['git', '-C', str(root), 'config', '--local', '--get', 'collab.actor'], capture_output=True).stdout.decode().strip()
        if not (branch and branch == personal_branch and actor == local_actor) and not any(branch.startswith(f'{kind}/{actor}/') and len(branch.split('/')) >= 3
                   and branch.split('/')[-1] for kind in ('feature', 'fix', 'docs', 'integration')):
            raise ScopeError(f'Use your locally registered personal branch or feature/{actor}/<task> (or fix/docs/integration), got {branch!r}')
        if args.base:
            git(root, 'rev-parse', '--verify', f'{args.base}^{{commit}}')
            paths = parse_diff(git(root, 'diff', '--no-ext-diff', '--no-renames', '--name-status', '-z', f'{args.base}...HEAD'))
            tree = git(root, 'ls-tree', '-r', '-z', 'HEAD')
            symlinks = [r.split(b'\t', 1)[1].decode('utf-8') for r in tree.split(b'\0') if r.startswith(b'120000 ')]
            new_paths = git(root, 'diff', '--no-ext-diff', '--no-renames', '--diff-filter=A', '--name-only', '-z', f'{args.base}...HEAD')
        else:
            paths = parse_diff(git(root, 'diff', '--cached', '--no-ext-diff', '--no-renames', '--name-status', '-z'))
            entries = git(root, 'ls-files', '--stage', '-z')
            symlinks = [r.split(b'\t', 1)[1].decode('utf-8') for r in entries.split(b'\0') if r.startswith(b'120000 ')]
            new_paths = git(root, 'diff', '--cached', '--no-ext-diff', '--no-renames', '--diff-filter=A', '--name-only', '-z')
        errors = check_paths(paths, actor, policy)
        added = set(new_paths.decode('utf-8').split('\0'))
        records = [p for p in paths if p.startswith(f'docs/updates/{actor}/')
                   and p.endswith('.md') and PurePosixPath(p).name != 'AGENTS.md']
        for record in records:
            if record not in added:
                errors.append(f'Update history is append-only; add a correction record: {record}')
        needs_record = any(p.startswith(('backend/', 'frontend/', 'scripts/', 'tests/'))
                           and PurePosixPath(p).name != 'AGENTS.md' for p in paths)
        if needs_record and not any(p in added for p in records):
            errors.append(f'Code changes require a NEW docs/updates/{actor}/<timestamp-topic>.md record')
        if symlinks:
            errors.extend(f'Symlink not permitted: {p}' for p in symlinks)
        if errors:
            raise ScopeError('\n'.join(errors))
        print(f'Scope OK: actor={actor}, changed_paths={len(set(paths))}. No runtime tests implied.')
        return 0
    except (ScopeError, OSError, ValueError, UnicodeError) as exc:
        print(f'Scope check failed:\n{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
