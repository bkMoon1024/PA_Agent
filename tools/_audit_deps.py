# -*- coding: utf-8 -*-
import ast, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

STDLIB = set(sys.stdlib_module_names)
LOCAL = {"pa_agent", "tests", "tools", "scripts", "config"}
third = {}

for base in ("pa_agent",):
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            try:
                tree = ast.parse(open(path, encoding="utf-8").read())
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
                    third.setdefault(top, set()).add(path)

# import name -> pypi package name
IMPORT_TO_PKG = {
    "PyQt6": "PyQt6", "MetaTrader5": "MetaTrader5", "cv2": "opencv-python",
    "tvDatafeed": "tvdatafeed", "websocket": "websocket-client",
    "yfinance": "yfinance", "sklearn": "scikit-learn", "yaml": "PyYAML",
    "PIL": "pillow", "bs4": "beautifulsoup4", "dateutil": "python-dateutil",
}

print("代码里 import 的第三方模块 -> 依赖库中是否有对应包")
print("=" * 60)
lib_dir = r"D:\cl\依赖库"
lib_files = " ".join(f.lower() for f in os.listdir(lib_dir)) if os.path.isdir(lib_dir) else ""

for mod in sorted(third):
    pkg = IMPORT_TO_PKG.get(mod, mod)
    key = pkg.lower().replace("-", "_").replace(".", "_")
    # 宽松匹配：包名前缀出现在文件名里
    found = any(key.split("_")[0] in f for f in lib_files.split())
    mark = "OK " if found else "MISSING"
    print("[%s] %-16s (pkg=%s)" % (mark, mod, pkg))
