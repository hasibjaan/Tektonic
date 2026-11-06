"""Prompt logger: a Claude Code UserPromptSubmit hook.

Claude Code runs this once per prompt and sends a JSON object on stdin.
We take its "prompt" field and append it, unchanged, to
prompts/claude-code-<name>.md, where <name> is our first argument.

Rules this file keeps:
- It prints NOTHING to stdout. A hook's stdout becomes part of Claude's context.
- It always exits 0. Exit code 2 would block the prompt.
- Errors go to stderr only.
- It only ever appends. It never rewrites or removes an earlier entry.
"""
import datetime
import json
import os
import re
import sys

MIN_FENCE = 5  # five tildes, as the team's format asks


def fence_for(text):
    """Return a tilde fence longer than any tilde run at the start of a line in text.

    Normally this is five tildes. If the prompt itself contains a line that starts
    with five or more tildes, a five-tilde fence would end early, so we use a
    longer one. The prompt text itself is never changed.
    """
    longest = 0
    for match in re.finditer(r"^[ ]{0,3}(~+)", text, flags=re.MULTILINE):
        longest = max(longest, len(match.group(1)))
    return "~" * max(MIN_FENCE, longest + 1)


def safe_name(raw):
    """Keep the file name to letters, digits, dot, dash and underscore."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", raw).strip("._")
    return name or "unknown"


def format_entry(prompt, name, now):
    """Build one log entry: heading, fence, the prompt exactly, fence."""
    fence = fence_for(prompt)
    heading = "### {} · Claude Code · {}".format(now.strftime("%Y-%m-%d %H:%M:%S"), name)
    return "{}\n{}\n{}\n{}\n\n".format(heading, fence, prompt, fence)


def project_dir():
    """The repository root: CLAUDE_PROJECT_DIR, or the folder above tools/."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return env
    print("promptlog: CLAUDE_PROJECT_DIR not set, using the folder above tools/",
          file=sys.stderr)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(argv):
    name = safe_name(argv[1] if len(argv) > 1 else "unknown")
    raw = sys.stdin.buffer.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Keep going so the prompt is not lost; say clearly that it is not exact.
        print("promptlog: stdin is not valid UTF-8 ({}); bad bytes replaced".format(exc),
              file=sys.stderr)
        text = raw.decode("utf-8", errors="replace")
    data = json.loads(text)
    prompt = data.get("prompt")
    if not isinstance(prompt, str):
        print("promptlog: no string 'prompt' field in hook input; nothing logged",
              file=sys.stderr)
        return
    folder = os.path.join(project_dir(), "prompts")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "claude-code-{}.md".format(name))
    entry = format_entry(prompt, name, datetime.datetime.now())
    # newline="" writes "\n" as-is on every OS, so the prompt bytes are not changed.
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(entry)
        f.flush()
        os.fsync(f.fileno())


def main():
    try:
        run(sys.argv)
    except BaseException as exc:  # never block the prompt, whatever happened
        try:
            print("promptlog: error: {!r}".format(exc), file=sys.stderr)
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
