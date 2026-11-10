"""Unit tests for the git guard hook's decision logic (.claude/hooks/guard_git.py)."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks", "guard_git.py")
spec = importlib.util.spec_from_file_location("guard_git", HOOK)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
BRANCHES = {"main", "claude/x", "claude/y"}


def blocked(cmd, branch="claude/x", upstream="claude/x"):
    return guard.decide(cmd, branch, upstream, BRANCHES)[0] is not None


class TestCommit(unittest.TestCase):
    def test_commit_on_main_is_blocked_in_every_spelling(self):
        for cmd in ["git commit -m x", "git commit -am x", "git -c user.name=a commit -m x", "git --no-pager commit",
                    "GIT_DIR=.git git commit", "env git commit", "/usr/bin/git commit", "git -C /repo commit -m y",
                    "cd x && git commit -m y", "echo ok\ngit commit -m x", "true\n  git -C . commit",
                    "if git commit -m x; then echo ok; fi", "! git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd, "main", "main"))

    def test_commands_the_guard_cannot_read_are_blocked(self):
        for cmd in ['echo "$(git commit -m x)"', "echo `git commit -m x`", "bash -c 'git commit -m x'",
                    "sudo -u alice git commit -m x", "eval git commit -m x", "xargs git push origin < refs",
                    "sh -c 'git push origin main'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "claude/x", "claude/x", BRANCHES)[0], guard.UNREADABLE)

    def test_branch_change_must_be_chained_with_and(self):
        self.assertFalse(blocked("git switch -c claude/x && git add -A && git commit -m x", "main", "main"))
        self.assertFalse(blocked("git checkout -b claude/y && git commit -m x", "main", "main"))
        for cmd in ["git switch -c claude/x; git commit -m x", "git switch -c claude/x || git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd, "main", "main"))

    def test_checkout_of_main_is_tracked(self):
        self.assertTrue(blocked("git checkout main && git commit -m x"))
        self.assertTrue(blocked("git switch main && git commit -m x"))
        self.assertEqual(guard.decide("git checkout -- file.txt && git commit -m x", "claude/x", "claude/x", BRANCHES), (None, True))

    def test_commit_on_a_branch_runs_verify(self):
        for cmd in ["git commit -m 'merge to main later'", 'git commit -m "fix the bash script"', 'git commit -m "$(cat msg)"']:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "claude/x", "claude/x", BRANCHES), (None, True))


class TestPush(unittest.TestCase):
    def test_pushes_that_update_main_are_blocked(self):
        for cmd in ["git push origin main", "git push -u origin main", "git push origin HEAD:main", "git push origin +main",
                    "git push origin 'main'", "git push origin refs/heads/main", "git -c x=y push origin main",
                    "git --git-dir=.git push origin main", "git push --all", "git push --mirror origin",
                    "git push origin :", "git push origin 'refs/heads/*:refs/heads/*'"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd))

    def test_implicit_push_follows_branch_and_upstream(self):
        for cmd in ["git push", "git push origin", "git push -u origin HEAD"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd, "main", "main"))
                self.assertFalse(blocked(cmd, "claude/x", "claude/x"))
        # With no refspec, git pushes to the upstream; an explicit HEAD pushes to the same-named branch.
        self.assertTrue(blocked("git push", "claude/x", "main"))
        self.assertTrue(blocked("git push origin", "claude/x", "main"))
        self.assertFalse(blocked("git push -u origin HEAD", "claude/x", "main"))

    def test_allowed_pushes(self):
        for cmd in ["git push -u origin claude/workflow-v2", "git push origin main-docs",
                    "git push origin claude/x && git log main", "git push origin claude/x  # then merge to main"]:
            with self.subTest(cmd=cmd):
                self.assertFalse(blocked(cmd))


class TestRealisticCommands(unittest.TestCase):
    """Commands agents write every day must not be blocked."""

    def test_commits_with_tricky_messages_are_allowed(self):
        for cmd in ["git commit -F - <<'EOF'\nIt's done\n\ngit push origin main is refused\nEOF",
                    "git commit -m \"$(cat <<'EOF'\nSay \"git push\" plainly\nEOF\n)\"",
                    "git commit -m 'Block `git commit` in backticks'",
                    "git commit -m 'avoid $(git push) in scripts'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "claude/x", "claude/x", BRANCHES), (None, True))

    def test_redirections_keep_the_and_chain(self):
        for cmd in ["git switch -c claude/t 2>&1 && git commit -m x", "git switch -c claude/t >/dev/null 2>&1 && git commit -m x",
                    "git switch -c claude/t &>/dev/null && git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "main", "main", BRANCHES), (None, True))
        self.assertFalse(blocked("git switch -c claude/t 2>&1 && git push -u origin HEAD", "main", "main"))

    def test_searches_that_mention_git_are_allowed(self):
        for cmd in ["find . -name '*.py' | xargs grep -n 'git push'", "find . -exec grep -l 'git commit' {} +",
                    "grep -n push README.md", "echo \"$(date)\" && git status"]:
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard.decide(cmd, "main", "main", BRANCHES)[0])

    def test_wrapped_runners_are_still_caught(self):
        for cmd in ["env sudo git commit -m x", "nohup sudo git push origin main", "timeout 60 git push origin main"]:
            with self.subTest(cmd=cmd):
                self.assertIsNotNone(guard.decide(cmd, "claude/x", "claude/x", BRANCHES)[0])

    def test_detached_head_says_so(self):
        self.assertIn("detached HEAD", guard.decide("git commit -m x", None)[0])


class TestCopilotSecondReview(unittest.TestCase):
    """Cases from Copilot's review of PR #6."""

    def test_negated_or_broken_chains_after_a_switch_block_the_commit(self):
        for cmd in ["! git switch -c claude/x && git commit -m x",
                    "git switch -c claude/x && git add -A; git commit -m x",
                    "git switch -c claude/x && git add -A || git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd, "main", "main"))

    def test_wrappers_with_options_are_seen_through(self):
        self.assertTrue(blocked("env -i git commit -m x", "main", "main"))
        self.assertTrue(blocked("env -u HOME git commit -m x", "main", "main"))
        self.assertEqual(guard.decide("env -i git commit -m x", "claude/x", "claude/x", BRANCHES), (None, True))
        for cmd in ["command bash -c 'git commit -m x'", "env bash -c 'git commit -m x'", "env -i bash -c 'git push'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "claude/x", "claude/x", BRANCHES)[0], guard.UNREADABLE)

    def test_switch_double_dash_is_a_branch_not_a_path(self):
        self.assertTrue(blocked("git switch -- main && git commit --allow-empty -m x"))

    def test_switching_to_a_branch_uses_its_upstream(self):
        upstreams = {"claude/x": "main", "claude/y": "claude/y"}
        self.assertIsNotNone(guard.decide("git switch claude/x && git push", "claude/y", "claude/y", BRANCHES, upstreams)[0])
        self.assertIsNone(guard.decide("git switch claude/y && git push", "claude/x", "main", BRANCHES, upstreams)[0])
        # Unknown upstreams block implicit pushes after a switch.
        self.assertIsNotNone(guard.decide("git switch claude/y && git push", "claude/x", "claude/x", BRANCHES)[0])


class TestCopilotThirdReview(unittest.TestCase):
    """Cases from Copilot's review of 4433453."""

    def test_unquoted_heredoc_with_a_git_write_is_unreadable(self):
        self.assertEqual(guard.decide("cat <<EOF\n$(git commit -m x)\nEOF", "main", "main", BRANCHES)[0], guard.UNREADABLE)
        self.assertEqual(guard.decide("git commit -F - <<EOF\nmsg\nEOF", "claude/x", "claude/x", BRANCHES)[0], guard.UNREADABLE)

    def test_runner_scripts_behind_wrappers_are_unreadable(self):
        for cmd in ["env -S 'git commit -m x'", "env --split-string='git push origin main'", "sudo sh -c 'git commit -m x'",
                    "exec bash -c 'git push'", "timeout 60 bash -c 'git commit -m x'"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "claude/x", "claude/x", BRANCHES)[0], guard.UNREADABLE)

    def test_tracking_or_reset_switches_make_the_upstream_unknown(self):
        for cmd in ["git switch -c claude/new --track origin/main && git push", "git switch -c claude/new -t origin/main && git push",
                    "git switch -C claude/x && git push", "git checkout -B claude/x && git push",
                    "git switch -c claude/new origin/main && git push"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd, "claude/y", "claude/y"))
        self.assertFalse(blocked("git switch -c claude/new && git push -u origin claude/new", "main", "main"))

    def test_switch_after_or_or_pipe_is_uncertain(self):
        self.assertTrue(blocked("git show-ref --verify --quiet refs/heads/claude/x || git switch -c claude/x && git commit -m x",
                                "main", "main"))

    def test_multiline_and_chains_are_one_chain(self):
        self.assertEqual(guard.decide("git switch -c claude/new &&\ngit commit -m x", "main", "main", BRANCHES), (None, True))
        self.assertEqual(guard.decide("git switch -c claude/new && \\\n  git commit -m x", "main", "main", BRANCHES), (None, True))


class TestCopilotFourthReview(unittest.TestCase):
    """Cases from Copilot's review of ec2c970."""

    def test_heredoc_fed_to_a_shell_is_unreadable(self):
        for cmd in ["bash <<'EOF'\ngit commit --allow-empty -m x\nEOF", "sh -s <<'EOF'\ngit push origin main\nEOF",
                    "python3 - <<'EOF'\nimport os; os.system('git push origin main')\nEOF"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "main", "main", BRANCHES)[0], guard.UNREADABLE)
        # Writing docs that mention git through cat is fine.
        self.assertEqual(guard.decide("cat > notes.md <<'EOF'\nRun git push origin claude/x\nEOF", "main", "main", BRANCHES),
                         (None, False))

    def test_redirections_do_not_hide_a_switch(self):
        for cmd in ["git switch main &>/dev/null && git commit -m x", "git switch main &>> log && git commit -m x",
                    "git switch main >/dev/null 2>&1 && git commit -m x", "git switch main > 'out file' && git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd))
        self.assertEqual(guard.decide('git commit -m "a -> b >= c"', "claude/x", "claude/x", BRANCHES), (None, True))

    def test_control_flow_around_a_git_write_is_unreadable(self):
        for cmd in ["if false; then git switch -c claude/new; fi && git commit -m x",
                    "for b in a; do git push origin $b; done", "while true; do git commit -m x; done"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "main", "main", BRANCHES)[0], guard.UNREADABLE)


class TestCopilotFifthReview(unittest.TestCase):
    def test_git_writes_inside_redirection_targets_are_unreadable(self):
        for cmd in ['printf ok >"$(git push origin main)"', "cat < $(git commit -m x)", "echo x > `git push`"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "claude/x", "claude/x", BRANCHES)[0], guard.UNREADABLE)
        self.assertEqual(guard.decide("git commit -m 'x' > '$(git push)'", "claude/x", "claude/x", BRANCHES), (None, True))


class TestCopilotSandboxReview(unittest.TestCase):
    """Found by Copilot on lucasns97/sandbox#1, where the kit was installed."""

    def test_a_comment_does_not_hide_the_next_line(self):
        for cmd in ["git status # note\ngit commit -m x", "echo hi # x\ngit push origin main", "# only a comment\ngit commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd, "main", "main"))
        self.assertFalse(blocked("git add -A # stage\ngit commit -m x"))

    def test_switching_to_the_previous_branch_is_unknown(self):
        for cmd in ["git switch - && git commit -m x", "git checkout - && git commit -m x",
                    "git checkout @{-1} && git commit -m x", "git switch - && git push"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd))
        self.assertFalse(blocked("git switch - && git push -u origin claude/x"))

    def test_commit_after_switching_to_other_files_is_blocked(self):
        # verify.sh runs once, before the call, so it would have checked the old branch's files.
        for cmd in ["git switch claude/y && git commit -m x", "git checkout claude/y && git commit -m x",
                    "git switch -c claude/z origin/claude/y && git commit -m x"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(blocked(cmd))
        # A new branch from the current commit has the same files, so verify still applies.
        self.assertFalse(blocked("git switch -c claude/z && git commit -m x", "main", "main"))
        self.assertFalse(blocked("git switch claude/x && git commit -m x"))


class TestHookProcess(unittest.TestCase):
    """Run the hook as Claude Code does, against a real repo with no remote-tracking refs."""

    def test_push_from_a_branch_tracking_main_is_blocked(self):
        with tempfile.TemporaryDirectory() as repo:
            run = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True)
            run("init", "-q", "-b", "main")
            run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
            run("switch", "-q", "-c", "claude/x")
            run("config", "branch.claude/x.remote", "origin")
            run("config", "branch.claude/x.merge", "refs/heads/main")
            result = lambda cmd: subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_input": {"command": cmd}}),
                                                capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": repo})
            self.assertEqual(result("git push").returncode, 2)
            self.assertEqual(result("git push -u origin claude/x").returncode, 0)
            self.assertEqual(result("git status").returncode, 0)

    def test_malformed_input_fails_open(self):
        for stdin in ["not json", '{"tool_input": {"command": null}}', "[]"]:
            with self.subTest(stdin=stdin):
                r = subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)


class TestOtherCommands(unittest.TestCase):
    def test_unrelated_commands_pass_without_verify(self):
        for cmd in ["ls -la", "git status", "git log main", "git pull origin main", "git commit-tree abc",
                    "echo git commit is a phrase", "git diff main...HEAD", "bash run_tests.sh"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.decide(cmd, "main", "main", BRANCHES), (None, False))


if __name__ == "__main__":
    unittest.main()
