# Branches and pull requests

## Branch first

Before writing code for a new agent, a small feature or a bug fix, create a branch from an up-to-date `main` and switch to it (`git switch -c <name>`; uncommitted changes come along). Never commit to or edit code on `main` unless the user says so in that conversation.

## Branch names: `<version>/<slug>`

`<version>` is the release the work will ship in, numbered by `.claude/rules/changelog.md`:

- New agent: the next minor, e.g. `0.4.0/<slug>`.
- Bug fix or small feature: the open patch. Start from the newest version heading in `CHANGELOG.md` plus one patch (`0.3.2` -> `0.3.3`). Every branch for that patch uses the same version until it holds 5 items; then the next item starts the next patch.

Count the items of the open patch from the existing branches (`git branch -a --list "<version>/*"`). One branch holds one fix or one feature; several items share a version, never a branch.

`<slug>` is a short kebab-case name for the item, e.g. `0.3.3/usage-tracking`.

## Finish

Keep working and committing on the branch. When the user approves the changes, push the branch and open a PR to `main` with `gh pr create`. Do not merge it unless the user asks.
