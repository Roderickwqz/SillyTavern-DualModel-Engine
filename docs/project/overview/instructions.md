## Engineering and Coding Rule

MUST LOAD `<HARNESS_ROOT>/principles/engineer_p1.md`
MUST LOAD `<HARNESS_ROOT>/principles/engineer_p2.md`

## Git Rule

Load `<HARNESS_ROOT>/principles/git.md`

## Commands

- `python -m pip install -e .` installs package editable.
- `python -m pytest -q` runs full test suite.

## Quality Guard

Run before final handoff:

```bash
python <HARNESS_ROOT>/scripts/guard.py --changed
```

For larger changes:

```bash
python <HARNESS_ROOT>/scripts/guard.py src tests
```

Guard = shallow safety only. Not replacement for tests/lint/typecheck/build.

## Testing

Load `<HARNESS_ROOT>/principles/test.md`

## Env Variables

READ `.env`
