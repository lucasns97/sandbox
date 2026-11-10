#!/usr/bin/env bash
# Every local check for this repo, in one command. The commit hook (.claude/hooks/guard_git.py)
# runs it before each commit and CI runs it on every PR, so keep it under about a minute.
# Exits non-zero on any failure.
set -u
export PYTHONDONTWRITEBYTECODE=1
cd "$(dirname "$0")/.."
status=0

echo "== Unit tests (includes the git guard's) =="
python3 -m unittest discover -s tests -p 'test_*.py' || status=1

echo "== Experiment scripts compile =="
# Byte-compile only; running the benchmarks takes too long for a per-commit check.
python3 - <<'PY' || status=1
import glob, sys
ok = True
for f in sorted(glob.glob('*.py')):
    try:
        compile(open(f, encoding='utf-8').read(), f, 'exec')
        print('ok', f)
    except SyntaxError as e:
        print(f'{f}:{e.lineno}: {e.msg}', file=sys.stderr)
        ok = False
sys.exit(0 if ok else 1)
PY

exit $status
