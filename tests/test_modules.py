"""The tetherlib modules must hold together: every module imports on its own (so no import-order accident), no function uses a name
that nothing defines, and every `module.name` one module uses from another exists. Python only finds these at run time, on the
path that happens to use them; this finds them all at once."""
import ast
import builtins
import importlib
import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LIB = os.path.join(ROOT, "lib")
PKG = os.path.join(LIB, "tetherlib")
MODULES = sorted(f[:-3] for f in os.listdir(PKG) if f.endswith(".py") and f != "__init__.py")
sys.path.insert(0, LIB)


def bound_names(body, args=None):
    """Names a function binds itself (parameters, assignments, loops, imports, nested definitions)."""
    names = set()
    if args:
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            names.add(a.arg)
        for a in (args.vararg, args.kwarg):
            if a:
                names.add(a.arg)
    for b in body:
        for x in ast.walk(b):
            if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
                names.add(x.id)
            elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(x.name)
            elif isinstance(x, ast.ExceptHandler) and x.name:
                names.add(x.name)
            elif isinstance(x, (ast.Import, ast.ImportFrom)):
                for a in x.names:
                    names.add((a.asname or a.name).split(".")[0])
            elif isinstance(x, ast.arg):
                names.add(x.arg)
    return names


class Walker(ast.NodeVisitor):
    def __init__(self, top, loaded, module, aliases=()):
        self.top, self.loaded, self.module, self.scopes, self.problems = top, loaded, module, [], []
        self.aliases = set(aliases)  # the tetherlib modules this file imports by name

    def local(self, name):
        return any(name in s for s in self.scopes)

    def visit_FunctionDef(self, n):
        bound = bound_names(n.body, n.args)
        for clash in sorted(bound & self.aliases):  # a local called `store` would hide the module and fail only when that path runs
            self.problems.append(f"{self.module}.py:{n.lineno}: {n.name} uses {clash} as a local name, which hides the module {clash}")
        self.scopes.append(bound)
        for x in n.body:
            self.visit(x)
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, n):
        self.scopes.append({a.arg for a in n.args.args})
        self.visit(n.body)
        self.scopes.pop()

    def visit_ListComp(self, n):
        self.scopes.append({t.id for g in n.generators for t in ast.walk(g.target) if isinstance(t, ast.Name)})
        self.generic_visit(n)
        self.scopes.pop()

    visit_SetComp = visit_DictComp = visit_GeneratorExp = visit_ListComp

    def visit_Name(self, n):
        if isinstance(n.ctx, ast.Load) and n.id not in self.top and not self.local(n.id):
            self.problems.append(f"{self.module}.py:{n.lineno}: {n.id} is not defined")

    def visit_Attribute(self, n):
        v = n.value
        if isinstance(v, ast.Name) and v.id in self.loaded and v.id != self.module and not self.local(v.id) and not hasattr(self.loaded[v.id], n.attr):
            self.problems.append(f"{self.module}.py:{n.lineno}: {v.id} has no {n.attr}")
        self.generic_visit(n)


class Modules(unittest.TestCase):
    def test_each_module_imports_on_its_own(self):
        for m in MODULES:
            r = subprocess.run([sys.executable, "-c", f"import tetherlib.{m}"], capture_output=True, text=True, cwd=LIB)
            self.assertEqual(r.returncode, 0, f"import tetherlib.{m} failed:\n{r.stderr}")

    def test_no_module_uses_a_name_that_nothing_defines(self):
        loaded = {m: importlib.import_module(f"tetherlib.{m}") for m in MODULES}
        problems = []
        for m in MODULES:
            with open(os.path.join(PKG, m + ".py"), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            top = set(dir(builtins))
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    top.add(n.name)
                elif isinstance(n, (ast.Import, ast.ImportFrom)):
                    top.update((a.asname or a.name).split(".")[0] for a in n.names)
                elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    top.update(t.id for t in ast.walk(n) if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store))
            aliases = {a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "tetherlib" for a in n.names}
            w = Walker(top, loaded, m, aliases)
            w.visit(tree)
            problems += w.problems
        self.assertEqual(problems, [])

    def test_the_checker_itself_catches_a_missing_name_and_a_shadowed_module(self):
        w = Walker(set(dir(builtins)) | {"f"}, {}, "demo")
        w.visit(ast.parse("def f():\n    return nothing_defines_this\n"))
        self.assertEqual(len(w.problems), 1)
        w = Walker(set(dir(builtins)) | {"f"}, {}, "demo", {"store"})
        w.visit(ast.parse("def f():\n    store = 1\n    return store\n"))
        self.assertEqual(len(w.problems), 1)


if __name__ == "__main__":
    unittest.main()
