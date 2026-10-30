"""Dependency-free test runner:  python run_tests.py [name-filter]
The tests are also plain pytest tests (pytest tests/)."""
import importlib
import pkgutil
import sys
import time
import traceback

import tests


def main(flt: str = "") -> int:
    passed = failed = 0
    t0 = time.time()
    for mod in sorted(m.name for m in pkgutil.iter_modules(tests.__path__) if m.name.startswith("test_")):
        module = importlib.import_module(f"tests.{mod}")
        for name in sorted(dir(module)):
            if not name.startswith("test_") or flt not in f"{mod}.{name}":
                continue
            try:
                getattr(module, name)()
                passed += 1
                print(f"  ok    {mod}.{name}")
            except Exception:
                failed += 1
                print(f"  FAIL  {mod}.{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed in {time.time() - t0:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
