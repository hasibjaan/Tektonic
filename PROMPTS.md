# PROMPTS.md — AI prompt log

Every prompt sent to any AI tool during the hackathon, unedited (brief, Section 9).

- Raw logs live in `prompts/`, one file per person and tool. Claude Code prompts are appended automatically by `tools/promptlog.py` (a UserPromptSubmit hook) to `prompts/claude-code-<name>.md`. Prompts to any other AI tool are added by hand to `prompts/<tool>-<name>.md`.
- In Phase 9 every file in `prompts/` is merged into this file in chronological order, each entry copied byte for byte. The original files are kept.
- Nothing below this header is ever edited, reordered, shortened or deleted.

---

PHASE 0 — REPOSITORY, PROMPT LOGGER, IMPORT CHECKER, DOCUMENT SKELETONS
Official start: [11:20]. Time now: [02:10]. Driver: [Hasib].

Read CLAUDE.md fully before doing anything.

FIRST, reply with these four things only, and run commands to get the facts — do not guess:
1. The invariant from CLAUDE.md section 2, in plain English.
2. The platform: find which of python / python3 / py works here, its exact version, the OS and the shell. Fill CLAUDE.md section 5. If the version is below 3.10, tell us before doing anything else.
3. Owners: Person A [name] owns the engine. Person B [name] owns the torture tool and its oracle, on a separate laptop. Person C [name] owns the documents and the prompt log. Confirm you understand that torture.py and tester/ must never import src.kv.
4. The highest-risk unknown on this laptop.

THEN, one step at a time, showing real command output:
1. Create the folder layout from CLAUDE.md section 6, with empty __init__.py files. Give kvdb.py and torture.py a placeholder that prints "not implemented yet" to stderr and exits with status 2.
2. Write tools/promptlog.py, the prompt logger:
   - reads the hook's JSON from stdin as bytes, decodes it as UTF-8, and takes the "prompt" field;
   - appends to prompts/claude-code-<name>.md, where <name> is its first argument, opening the file with encoding="utf-8";
   - each entry: a heading with local date and time, "Claude Code", and the name; then the prompt EXACTLY as received, inside a fence of five tildes;
   - finds the repository through the CLAUDE_PROJECT_DIR environment variable;
   - prints NOTHING to stdout (hook stdout becomes part of your context);
   - always exits 0 — exit code 2 would block our prompt — and reports errors only to stderr.
3. In .claude/settings.local.json add a UserPromptSubmit hook in exec form: "command" is the Python command that works on this laptop (use its full path if needed), "args" is ["${CLAUDE_PROJECT_DIR}/tools/promptlog.py", "[driver name]"]. Gitignore settings.local.json.
4. Write tools/check_imports.py, the dependency proof:
   - parse every .py file in the repository with the ast module (do not run them);
   - collect every imported top-level module name;
   - allowed: names in sys.stdlib_module_names, and our own packages (src, tester, tools, tests) and modules (kvdb, torture);
   - always forbidden: sqlite3, dbm, shelve — report the file and line;
   - additionally report any import of src.kv from torture.py or anything in tester/;
   - print OK and exit 0, or list every problem and exit 1.
5. Create these skeletons. HEADINGS ONLY. No claims, no invented content:
   - README.md: Build · Requirements · Commands · serve protocol · On-disk format · Recovery algorithm · Limits · Errors · Platform notes · Measured results. Every body: "To be written from evidence." Add one note under Build: the torture tool lives in tester/ and torture.py, not torture/, so no folder clashes with a ./torture launcher.
   - ARCHITECTURE.md: Part 1 Diagram (link to diagrams/architecture.mmd) · Part 2 Proof table with ALL 32 rows, A1 to F4, each with the scenario wording from brief Section 5, and every other column "not verified" · Part 3 Three guarantees: Durability, Atomicity, Recoverability — empty.
   - BREAKING.md: exactly the five required headings — 1 Crash points table (header row only) · 2 The ordering argument · 3 Known weaknesses · 4 What we tried that failed · 5 If we had 24 more hours. Every body: "To be written from evidence."
   - PROMPTS.md: keep what we pasted; add a short header explaining that raw logs live in prompts/ and Phase 9 merges them.
   - diagrams/architecture.mmd: an empty Mermaid stub.
   - evidence/README.md: what goes in evidence/ (hex dumps, benchmarks, torture runs, failing seeds).
   - docs/clarifications.md: empty heading — we add the judges' answers word for word.
6. .gitignore: __pycache__/, *.pyc, *.kv, *.tmp, .torture-work/, .claude/settings.local.json, rehearsal/
7. Run: python -m compileall -q .   then   python tools/check_imports.py   then   python -m unittest discover -s tests -v. Show the output.

EXIT — show me: the folder tree, `git status`, the three command outputs.
Then tell me to restart Claude Code and send the test prompt "logger test", and tell me how to confirm it appeared in prompts/.
Propose a commit message. STOP. Do not commit until I type "commit".