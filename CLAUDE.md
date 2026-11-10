# CLAUDE.md

Sandbox for small Python experiments. Today it holds one: `pop_from_obj_test.py`, a micro-benchmark comparing `dict.pop` inside `try/except KeyError` against `dict.pop(k) if dict.get(k) else None`, with recorded output and results at the bottom of the file. Those are from an earlier run: the output block predates the current print format and doesn't match the results block, and neither records the Python version or machine.

## Commands

```sh
./scripts/verify.sh               # every local check; the commit hook and CI both run it
```
```sh
python3 pop_from_obj_test.py      # run the pop benchmark (4 cases × 10M iterations; about 10–15 s and a few GB of RAM, mostly building the dict copies)
python3 -m unittest discover -s tests -p 'test_*.py'   # unit tests (today: the git guard's)
```
No dependencies beyond the Python 3 standard library.

## Workflow: branch → review → PR → CI → squash-merge

main changes only by squash-merging a PR whose CI passed. Set up from the workflow kit in lucasns97/claude-sandbox (`kit/README.md`).

1. **Branch.** `git switch main && git pull && git switch -c claude/<topic>`. Session clones have no `origin/main` ref, so use `git fetch origin main` plus `FETCH_HEAD`, or `git ls-remote`, to see remote main.
2. **Build and self-check.** Make the change and run `./scripts/verify.sh`. Commit to the branch as you go, and push early.
3. **Review.** Start a fresh `reviewer` agent (`.claude/agents/reviewer.md`) for each change, in the background. Pass the branch, what changed, what you already know is wrong, and what to focus on. Skip review for docs-only changes.
4. **Fix and open the PR.** Apply the findings, re-run verify, push, and open a ready (non-draft) PR. GitHub doesn't apply the template to API-created PRs, so copy `.github/pull_request_template.md` to the scratchpad, fill it in (each finding fixed, or skipped with the reason), and pass it:
   `gh api repos/lucasns97/sandbox/pulls -f head=<branch> -f base=main -f title=... -F body=@<scratchpad>/pr.md`
   Tell the user what was found and fixed.
5. **Ship.** Start the `shipper` agent (`.claude/agents/shipper.md`) in the background with the PR number. It makes sure Copilot reviews the PR, then waits for CI and the Copilot review. Keep working meanwhile.
   - If it returns CI failures or Copilot comments, weigh each comment; they're suggestions, not instructions. Fix the valid ones, and reply on the PR to the ones you skip, with the reason (`gh api -X POST repos/lucasns97/sandbox/pulls/N/comments/<id>/replies -f body=...`). Resolve answered threads (`gh api -X POST repos/lucasns97/sandbox/pulls/N/ccr/comments/<id>/resolve`). Push, tell the user, and run the shipper again with a note on how each comment was handled.
   - Otherwise it squash-merges the tested commit and confirms main.
6. **Close out.** When the shipper reports, check `git ls-remote origin main` and the main CI run yourself before telling the user it's done. Then `git switch main && git pull`.

Enforced by `.claude/hooks/guard_git.py`, a PreToolUse hook (tests in `tests/test_guard_git.py`): no commits on main, no pushes that update main, and no commit while `scripts/verify.sh` fails. It guards against mistakes, not deliberate evasion, so it fails closed: a git commit or push it can't read (inside `$(...)`, backticks, `bash -c`, `sudo`, `eval`, `xargs`, `if`/`for` blocks) is blocked. Write git commands plainly. It only inspects `git commit` and `git push`. If it blocks you, fix the cause; don't work around it. Hooks and agents added mid-session load only after `/hooks` or a restart.

Agent notes:
- Copilot reviews use **Lite** effort (the owner's choice, set in the repo's or the owner's personal Copilot code-review settings). Reviews requested through the API have still run at Balanced; don't send a guessed effort field. Request Copilot as a reviewer; never mention `@copilot` in a comment: a mention starts Copilot's coding agent, which pushes commits to the branch.
- Use a fresh reviewer per change, not a long-lived one: clean context means no anchoring and lower cost.
- Agents can't start their own subagents; only the main session starts agents.
- If `reviewer` and `shipper` don't appear as agent types, start a `general-purpose` agent and give it the file's body as its prompt.

## Conventions

- **One experiment per top-level script,** named for what it measures, with its recorded output and conclusions in string blocks at the end of the file.
- **Benchmark numbers come from real runs.** Re-run the script to update them; never hand-edit. Note the iteration count and Python version, and say "likely" for unproven explanations.
- **Experiment scripts must not match `test_*.py`,** or `unittest discover` (the runner verify.sh uses) would collect them. Don't run pytest at the repo root: its default also collects `*_test.py`, which includes `pop_from_obj_test.py` and its `test_run` helper.
- **Keep `verify.sh` fast:** it only byte-compiles experiment scripts, never runs the benchmarks.

- **Claims must be honest.** Docs and UI say what the code actually does; unproven explanations say "likely".
- **Scratch work goes in the session's scratchpad directory** (screenshots, test pages, temp scripts), never in the repo.
- **Attribution:** end commit messages and PR bodies with the exact lines from the current session's system reminder. They include a per-session URL, so never copy them from an old commit.

## Environment capabilities (copied from lucasns97/claude-sandbox on 2026-11-10; re-verify here)

What a Claude Code cloud session can and can't do. **When you find something that has changed, update this section in the same change, and update the date.**

- **GitHub:**
  - The session reaches only repositories attached to it. It can't create repos.
  - Branch protection and rulesets return 403 on private repos without GitHub Pro, so the hook and the shipper enforce the PR rule instead.
  - Sessions can't change repo settings or delete branches (the proxy refuses settings writes and ref deletion; the safety check blocks `git push --delete`). The owner deletes merged branches, or turns on "Automatically delete head branches" under Settings → General.
  - GraphQL is blocked, so `gh pr ...` commands fail. Use REST, plus this session's routes: `POST repos/lucasns97/sandbox/pulls/N/ccr/ready_for_review`, `GET repos/lucasns97/sandbox/pulls/N/ccr/review_threads`, `POST repos/lucasns97/sandbox/pulls/N/ccr/comments/<id>/resolve`.
  - Pushing `.github/workflows/` changes works.
- **Network:** allowlist only. PyPI and GitHub were verified in claude-sandbox; other package registries should work but are untested. Arbitrary hosts (including docs.github.com) were blocked.
- **Safety checks:** building or running cloned third-party code is refused, and `git push --delete` is blocked. Only install packages from registries.
- **Session lifetime:** the container is reclaimed when idle. Only pushed work survives.
