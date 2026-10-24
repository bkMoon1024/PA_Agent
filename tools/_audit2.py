# -*- coding: utf-8 -*-
import ast, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
STDLIB = set(sys.stdlib_module_names)
LOCAL = {"pa_agent", "tests", "tools", "scripts", "config"}
third = {}
for base in ("tests", "scripts"):
    if not os.path.isdir(base):
        continue
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(root, f), encoding="utf-8").read())
            except Exception:
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level > 0 or not node.module:
                        continue
                    names = [node.module]
                for n in names:
                    top = n.split(".")[0]
                    if top in STDLIB or top in LOCAL or not top:
                        continue
                    third.setdefault(top, set()).add(os.path.join(root, f))
for k in sorted(third):
    print("%-20s %s" % (k, sorted(third[k])[:2]))
