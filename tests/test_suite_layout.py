"""The test files themselves: every test in a file runs however the file is run.

`python tests/test_x.py` stops at `unittest.main()`, so a class defined below the
`if __name__ == "__main__":` guard is never defined and its tests silently do not run, while
`kit.py test` (unittest discovery) imports the whole file and runs them. Both must see the same suite.
"""
import ast
import unittest

from tests.helpers import ROOT


def _is_main_guard(node: ast.stmt) -> bool:
    return isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)


class TheMainGuardComesLast(unittest.TestCase):
    def test_nothing_is_defined_below_the_main_guard(self):
        files = sorted((ROOT / "tests").glob("test_*.py"))
        self.assertTrue(files, "no test files found")
        for path in files:
            body = ast.parse(path.read_text(encoding="utf-8")).body
            guards = [i for i, node in enumerate(body) if _is_main_guard(node)]
            if not guards:
                continue
            with self.subTest(file=path.name):
                after = [getattr(node, "name", type(node).__name__) for node in body[guards[0] + 1:]]
                self.assertEqual(after, [], f"{path.name}: defined below `if __name__ == \"__main__\"`, "
                                            "so running the file directly skips it; move the guard to the end")


if __name__ == "__main__":
    unittest.main()
