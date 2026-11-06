"""Dependency proof: every import in the repository is an allowed module.

Run from anywhere:  python3 tools/check_imports.py
Prints OK and exits 0, or prints every problem (file:line: reason) and exits 1.

How it works: it PARSES every .py file with the ast module. It never runs them.
For each import it finds the top-level module name and checks it against:
- allowed: Python's own standard library (sys.stdlib_module_names, Python 3.10+)
  and our own packages and modules (OWN);
- always forbidden, even though they ship with Python: FORBIDDEN;
- the tester (torture.py and tester/) must never import the engine (src) or the
  CLI (kvdb, which imports the engine).
"""
import ast
import os
import sys

OWN = {"src", "tester", "tools", "tests", "kvdb", "torture"}
# sqlite3, dbm and shelve are banned by the brief (Section 3). The underscore
# names are the C modules behind them, banned for the same reason.
FORBIDDEN = {"sqlite3", "_sqlite3", "dbm", "_dbm", "_gdbm", "shelve"}
TESTER_MUST_NOT_IMPORT = {"src", "kvdb"}
SKIP_DIRS = {".git", "__pycache__", ".torture-work", ".claude", ".venv", "venv"}


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def module_parts(rel_path):
    """'tester/oracle.py' -> ['tester', 'oracle']; 'tester/__init__.py' -> ['tester']."""
    parts = rel_path.replace(os.sep, "/")[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def resolve_relative(rel_path, level, module):
    """Turn 'from ..x import y' inside rel_path into an absolute dotted name.

    Returns None if the relative import climbs above the repository root.
    """
    parts = module_parts(rel_path)
    is_package = rel_path.replace(os.sep, "/").endswith("/__init__.py")
    package = parts if is_package else parts[:-1]
    if level - 1 > len(package):
        return None
    base = package[:len(package) - (level - 1)]
    full = base + (module.split(".") if module else [])
    return ".".join(full)


def imported_names(tree, rel_path):
    """Yield (line, dotted_name, problem_or_None) for every import in the tree."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name, None
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = resolve_relative(rel_path, node.level, node.module)
                if base is None:
                    yield node.lineno, None, "relative import climbs above the repository"
                    continue
            else:
                base = node.module or ""
            # 'from src import kv' really imports src.kv, so report each name too.
            for alias in node.names:
                if alias.name == "*" or not base:
                    yield node.lineno, base or alias.name, None
                else:
                    yield node.lineno, base + "." + alias.name, None
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (isinstance(func, ast.Name) and func.id == "__import__") or (
                isinstance(func, ast.Attribute) and func.attr == "import_module")
            if dynamic:
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield node.lineno, arg.value, None
                else:
                    yield node.lineno, None, "dynamic import with a non-constant name cannot be checked"


def is_tester_file(rel_path):
    rel = rel_path.replace(os.sep, "/")
    return rel == "torture.py" or rel.startswith("tester/")


def check(root):
    """Return a sorted list of problem strings. An empty list means OK."""
    allowed = set(sys.stdlib_module_names) | OWN
    problems = []
    for path in python_files(root):
        rel = os.path.relpath(path, root)
        try:
            with open(path, "rb") as f:
                tree = ast.parse(f.read(), filename=rel)
        except (SyntaxError, ValueError) as exc:
            problems.append("{}: cannot parse: {}".format(rel, exc))
            continue
        for line, name, problem in imported_names(tree, rel):
            where = "{}:{}".format(rel, line)
            if problem:
                problems.append("{}: {}".format(where, problem))
                continue
            top = name.split(".")[0]
            if top in FORBIDDEN:
                problems.append("{}: forbidden module '{}' (banned by the brief)".format(where, name))
            elif top not in allowed:
                problems.append("{}: '{}' is not a standard-library module or one of ours".format(where, name))
            if is_tester_file(rel) and top in TESTER_MUST_NOT_IMPORT:
                problems.append("{}: the tester must not import the engine ('{}')".format(where, name))
    return sorted(set(problems))


def main():
    if sys.version_info < (3, 10):
        print("check_imports needs Python 3.10+ for sys.stdlib_module_names", file=sys.stderr)
        return 1
    problems = check(repo_root())
    if problems:
        for p in problems:
            print(p)
        print("{} problem(s)".format(len(problems)))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
