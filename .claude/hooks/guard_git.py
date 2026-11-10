#!/usr/bin/env python3
"""PreToolUse hook for Bash: enforce the repo's git rules instead of relying on instructions.

  1. No commits on main: work on a claude/* branch and open a PR.
  2. No pushes that would update main: changes reach main only by squash-merging a PR.
  3. No commit while scripts/verify.sh fails.

Reads the hook JSON on stdin. Exit 2 blocks the tool call and shows stderr to Claude.
`decide()` is pure, so tests/test_guard_git.py can check it without git or a shell.

This guards against mistakes, not against someone deliberately evading it: a git commit or push
that it can't read with confidence (command substitution, bash -c, sudo, eval, ...) is blocked,
and the message asks for a plain command instead.
"""
import json
import os
import re
import shlex
import subprocess
import sys

WRAPPERS = {"env", "command", "nohup", "time"}
SHELL_KEYWORDS = {"if", "then", "else", "elif", "fi", "while", "until", "for", "case", "do", "done", "{", "}"}
UNKNOWN = object()  # an upstream we couldn't determine
# git global options that take a separate value
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
MAIN = "main"
# Commands that run other commands. A git commit/push behind one is blocked rather than guessed at.
SCRIPT_RUNNERS = {"bash", "sh", "zsh", "dash", "eval", "su", "ssh", "source", ".", "watch", "parallel"}  # take code as text
EXEC_RUNNERS = {"xargs", "sudo", "doas", "runuser", "exec", "find", "timeout", "nice"}  # take a command as words
GIT_WRITE = re.compile(r"\b(commit|push)\b")
# Heredocs with a quoted delimiter (<<'EOF') are literal text. Unquoted ones expand $(...), so they stay visible.
QUOTED_HEREDOC = re.compile(r"<<-?[ \t]*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n.*?^[ \t]*\2[ \t]*$", re.S | re.M)
UNQUOTED_HEREDOC = re.compile(r"<<-?[ \t]*[A-Za-z_\\]")
CONTINUATION = re.compile(r"(&&|\|\||\|)[ \t]*(?:#[^\n]*)?\n|\\\n")  # bash continues these across a comment too
SPLIT_STRING = re.compile(r"^(-S|--split-string)(=|$)")
REDIRECT_AT = re.compile(r"\d*(&>>?|>>?&?|<(?![<(]))")  # an output/input redirection operator (not <<, not <()
INTERPRETERS = {"python", "python3", "node", "perl", "ruby", "php", "deno", "bun"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PREVIOUS_BRANCH = re.compile(r"^(-|@\{-\d+\})$")  # git switch -, git checkout @{-1}
# git subcommands that change the working tree or index to other content. verify.sh ran before the call,
# so a commit after one of them in the same call would commit files verify.sh never checked.
TREE_CHANGERS = {"pull", "merge", "rebase", "reset", "restore", "stash", "cherry-pick", "revert", "am", "apply"}
UNREADABLE = ("write the git commit or push as a plain command (no $(...), backticks, bash -c, eval, sudo or xargs) "
              "so the guard can check it")


def strip_redirects(cmd):
    """Remove redirections and their targets (2>&1, >/dev/null, &>> log, < in) outside quotes."""
    out, i, quote = [], 0, None
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            quote = None if ch == quote else quote
            out.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            i += 1
            continue
        m = REDIRECT_AT.match(cmd, i)
        if m and (i == 0 or (not cmd[i - 1].isalnum() and cmd[i - 1] not in "<>") or cmd[i].isdigit()):
            j = m.end()
            while j < len(cmd) and cmd[j] in " \t":
                j += 1
            if j < len(cmd) and cmd[j] in "'\"":  # quoted target
                end = cmd.find(cmd[j], j + 1)
                j = len(cmd) if end < 0 else end + 1
            else:
                while j < len(cmd) and cmd[j] not in " \t\n;&|()<>":
                    j += 1
            out.append(" ")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def heredoc_runs_code(cmd):
    """Is a quoted heredoc whose body mentions a git commit/push fed to a shell or interpreter?"""
    for m in QUOTED_HEREDOC.finditer(cmd):
        body = m.group(0).split("\n", 1)[1] if "\n" in m.group(0) else ""
        if not (re.search(r"\bgit\b", body) and GIT_WRITE.search(body)):
            continue
        line = cmd[cmd.rfind("\n", 0, m.start()) + 1:m.start()]
        words = line.replace("|", " ; ").replace("&&", " ; ").split(";")[-1].split()
        words = words[skip_wrappers(words):]
        if words and os.path.basename(words[0]) in SCRIPT_RUNNERS | EXEC_RUNNERS | INTERPRETERS:
            return True
    return False


def substitutions(text):
    """Yield the bodies of $(...), <(...), >(...) and `...` in text (nesting-aware for parentheses)."""
    i = 0
    while i < len(text):
        if text.startswith(("$(", "<(", ">("), i):
            depth, j = 1, i + 2
            while j < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[j], 0)
                j += 1
            yield text[i + 2:j - 1]
            i += 2  # keep scanning inside, so nested substitutions are seen too
        elif text[i] == "`":
            j = text.find("`", i + 1)
            j = len(text) if j < 0 else j
            yield text[i + 1:j]
            i = j + 1
        else:
            i += 1


def nested_git_write(cmd):
    """Does an executable substitution (outside single quotes) contain a git commit or push?"""
    return any(re.search(r"\bgit\b", b) and GIT_WRITE.search(b) for b in substitutions(unquoted(cmd)))


def drop_literals(cmd):
    """Drop quoted heredoc bodies (literal text) and join lines continued after &&, || or |."""
    cmd = QUOTED_HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], cmd)
    return CONTINUATION.sub(lambda m: (m.group(1) or "") + " ", cmd)


def unquoted(cmd):
    """Blank out single-quoted text, which the shell never expands, keeping the quote marks."""
    out, quote = [], None
    for ch in cmd:
        if quote == "'":
            if ch == "'":
                quote = None
                out.append(ch)
            continue
        if ch == "'" and quote is None:
            quote = "'"
        elif ch == '"':
            quote = None if quote == '"' else '"'
        out.append(ch)
    return "".join(out)


def segments(cmd):
    """Split a shell command into (operator_before, words, negated) triples; comments dropped, keywords stripped."""
    # Each newline separates commands. Keep the newline itself so a # comment ends there instead of
    # swallowing the lines after it, and add a ; on its own line as the separator token.
    lexer = shlex.shlex(cmd.replace("\n", "\n;\n"), posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    out, seg, op, seg_negated = [], [], ";", False
    for tok in lexer:  # raises ValueError on unbalanced quotes
        if tok and set(tok) <= set(";&|()"):
            if seg:
                out.append((op, seg, seg_negated))
            seg, op, seg_negated = [], tok, False
        elif not seg and (tok in SHELL_KEYWORDS or tok == "!"):
            if tok == "!":
                seg_negated = True
            else:
                out.append((op, [tok], False))  # keep keywords visible as their own step
            continue
        else:
            seg.append(tok)
    if seg:
        out.append((op, seg, seg_negated))
    return out


WRAPPER_OPTS_WITH_VALUE = {"-u", "--unset", "-C", "--chdir", "-S", "--split-string", "-f", "-o", "--output"}


def skip_wrappers(words):
    """Index of the first word after env assignments and wrappers like `env -i` or `command -p`."""
    i = 0
    while i < len(words):
        if ASSIGNMENT.match(words[i]):
            i += 1
        elif words[i] in WRAPPERS:
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in WRAPPER_OPTS_WITH_VALUE else 1
        else:
            break
    return i


def git_call(words):
    """If the simple command runs git, return (subcommand, args); otherwise None."""
    i = skip_wrappers(words)
    if i >= len(words) or os.path.basename(words[i]) != "git":
        return None
    i += 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GIT_OPTS_WITH_VALUE else 1
    if i >= len(words):
        return None
    return words[i], words[i + 1:]


def hides_git_write(words):
    """Does this simple command run a git commit/push through another program the guard can't follow?"""
    text = " ".join(words)
    writes = "git" in text and bool(GIT_WRITE.search(text))
    if writes and any(SPLIT_STRING.match(w) for w in words):  # env -S '<command line>'
        return True
    rest = words[skip_wrappers(words):]
    if not rest:
        return False
    runner = os.path.basename(rest[0])
    if runner in SCRIPT_RUNNERS or (runner in EXEC_RUNNERS and any(os.path.basename(w) in SCRIPT_RUNNERS - {".", "source"} for w in rest[1:])):
        return writes  # the git command is inside a script string, e.g. sudo sh -c '...'
    if runner in EXEC_RUNNERS:
        git_at = [i for i, w in enumerate(rest[1:], 1) if os.path.basename(w) == "git"]
        return any(w in ("commit", "push") for i in git_at for w in rest[i + 1:])
    return False


def push_updates_main(args, branch, upstream):
    """Could `git push <args>`, run on `branch` whose upstream is `upstream`, update main on the remote?"""
    positional, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a in ("--all", "--mirror", "--branches"):
            return True
        if a in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
            skip = True
            continue
        if not a.startswith("-"):
            positional.append(a)
    refspecs = positional[1:]  # the first positional is the remote
    if not refspecs:  # git pushes the current branch to its upstream (or a same-named branch)
        return MAIN in (branch, upstream) or branch is None or upstream is UNKNOWN
    for spec in refspecs:
        spec = spec.lstrip("+")
        if spec == ":" or "*" in spec:  # matching or wildcard refspecs can include main
            return True
        src, _, dst = spec.partition(":")
        target = dst or src
        if target in ("HEAD", "@") and not dst:  # pushes to the same-named branch, whatever the upstream
            if branch in (MAIN, None):
                return True
            continue
        if target in (MAIN, f"refs/heads/{MAIN}"):
            return True
    return False


def decide(cmd, branch, upstream=None, branches=(), upstreams=None):
    """Return (block_reason or None, needs_verify) for a Bash command run while on `branch`.

    `branch` is None on a detached HEAD. `upstream` is the branch's configured upstream branch name;
    `branches` are the local branch names and `upstreams` maps them to their upstream branch names
    (None: unknown). Only `git commit` and `git push` are inspected.
    """
    if not GIT_WRITE.search(cmd) or "git" not in cmd:
        return None, False
    if heredoc_runs_code(cmd):
        return UNREADABLE, False
    cmd = drop_literals(cmd)
    # Checked before redirections are stripped: the shell runs a substitution in a redirection target too.
    if nested_git_write(cmd):
        return UNREADABLE, False
    cmd = strip_redirects(cmd)
    if UNQUOTED_HEREDOC.search(unquoted(cmd)) and re.search(r"\bgit\b[\s\S]*\b(commit|push)\b", cmd):
        return UNREADABLE, False
    try:
        segs = segments(cmd)
    except ValueError:
        return UNREADABLE, False
    if any(hides_git_write(w) for _, w, _ in segs):
        return UNREADABLE, False
    # if/while/for/case can skip or repeat steps, so the guard won't guess at them around a git write.
    if any(len(w) == 1 and w[0] in SHELL_KEYWORDS - {"{", "}"} for _, w, _ in segs) \
            and any((c := git_call(w)) and c[0] in ("commit", "push") for _, w, _ in segs):
        return UNREADABLE, False
    # After a branch switch, every later step must be joined by && (and the switch not negated);
    # otherwise the switch might have failed while later steps still run on the old branch.
    # A switch after || or | might not run at all, so it also leaves the branch unknown.
    # `moved` means a switch in this call may have changed the working tree, so verify.sh (which runs
    # once, before the whole call) would have checked other files than the ones being committed.
    needs_verify, switched, unsure, conditional, moved, previous = False, False, False, False, False, False
    for op, words, negated in segs:
        if switched and op != "&&":
            unsure = True
        if op in ("||", "|"):
            conditional = True
        call = git_call(words)
        if not call:
            continue
        sub, args = call
        if sub in TREE_CHANGERS:
            moved = True
            continue
        if sub == "checkout" and "--" in args and any(not a.startswith("-") for a in args[:args.index("--")]):
            moved = True  # `checkout <tree-ish> -- <paths>` copies another commit's files in
            continue
        if sub in ("switch", "checkout") and not (sub == "checkout" and "--" in args) and any(PREVIOUS_BRANCH.match(a) for a in args):
            # "the previous branch" isn't known from the command alone.
            branch, upstream, switched, previous, moved = None, UNKNOWN, True, True, True
            continue
        if sub in ("switch", "checkout"):
            new = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-c", "-C", "--create", "-b", "-B")), None)
            if new is not None:
                start = [a for a in args if not a.startswith("-") and a != new]
                tracks = any(a in ("-t", "--track", "--set-upstream") or a.startswith("--track=") for a in args)
                resets = any(a in ("-C", "-B") for a in args)
                # A new branch has no upstream unless it tracks one, starts from a remote branch, or reuses a name.
                upstream = UNKNOWN if (tracks or resets or start) else None
                moved = moved or any(a not in ("HEAD", "@") for a in start)
            elif sub == "switch" or "--" not in args:  # `checkout -- <paths>` restores files instead
                names = [a for a in args if a != "--" and not a.startswith("-")]
                if len(names) == 1 and (sub == "switch" or names[0] in branches or names[0] == MAIN):
                    new = names[0]
                    upstream = UNKNOWN if upstreams is None else upstreams.get(new)
                    moved = moved or new != branch
            if new is not None:
                branch, switched = new, True
                unsure = unsure or negated or conditional
        elif sub == "commit":
            if unsure:
                return "chain the branch switch and the commit with && so the commit can't land on the wrong branch", False
            if previous:
                return "switch to the branch by name (not - or @{-1}) so the guard can check it", False
            if moved:
                return ("verify.sh ran before this command, so it didn't check the files this commit would include. "
                        "Run the switch, checkout, reset, pull or similar first, then commit in a separate command"), False
            if branch is None:
                return "you're on a detached HEAD. Switch to a claude/* branch first: git switch -c claude/<topic>", False
            if branch == MAIN:
                return "you're on main. Create a branch first: git switch -c claude/<topic>", False
            needs_verify = True
        elif sub == "push":
            if push_updates_main(args, None if unsure else branch, upstream):
                return "this push could update main. Push your claude/* branch by name and open a PR; main changes only by squash-merging it.", False
    return None, needs_verify


def main():
    # Malformed input fails open on purpose: Claude Code always sends a JSON object with a string command.
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    cmd = ((data.get("tool_input") or {}).get("command") or "") if isinstance(data, dict) else ""
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root = subprocess.run(["git", "-C", project, "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    if not root:
        return 0
    git = lambda *a: subprocess.run(["git", "-C", root, *a], capture_output=True, text=True).stdout.strip()
    branch = git("branch", "--show-current") or None  # None: detached HEAD, treated as unknown
    # Read the configured upstream directly: session clones have no remote-tracking refs, so @{u} fails there.
    upstream = (git("config", f"branch.{branch}.merge").removeprefix("refs/heads/") or None) if branch else None
    branches = set(git("for-each-ref", "--format=%(refname:short)", "refs/heads").split())
    upstreams = {}
    for line in git("config", "--get-regexp", r"^branch\..*\.merge$").splitlines():
        key, _, ref = line.partition(" ")
        upstreams[key[len("branch."):-len(".merge")]] = ref.removeprefix("refs/heads/")
    reason, needs_verify = decide(cmd, branch, upstream, branches, upstreams)
    if reason:
        print(f"Blocked: {reason}", file=sys.stderr)
        return 2
    if needs_verify:
        r = subprocess.run([os.path.join(root, "scripts", "verify.sh")], capture_output=True, text=True)
        if r.returncode != 0:
            tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-15:])
            print(f"Blocked: scripts/verify.sh failed, so this commit would break the build. Fix it first. Last lines:\n{tail}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
