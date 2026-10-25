# Tests

> Never create a test, or adjust a test so that it can be passed.

## Writing a test

- Write the test from the behaviour the spec or the user asked for, before the code or independent of it.
- Never shape a test around what the code happens to do.

## A failing test

- Fix the code, not the test.
- Never weaken, loosen, skip, mark xfail or delete a failing test in order to make it pass.
- Never change its assertions, fixtures, inputs or expected values in order to make it pass.
- When the test contradicts the spec, stop and report it with the spec line and the test. Do not change the test yourself.

## Removing tests

- Remove tests of behaviour that a spec or plan deliberately removed.
- Name the removed behaviour and the reason in the commit message or the report.
