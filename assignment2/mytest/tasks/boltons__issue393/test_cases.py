import unittest
from boltons.iterutils import research

class Reproduction(unittest.TestCase):
    def test_shared_tuple_is_visited_on_each_path(self):
        shared = ("hello",)
        root = {"first": shared, "second": shared}
        self.assertEqual(research(root), [((None,), root), (("first",), shared), (("first", 0), "hello"), (("second",), shared), (("second", 0), "hello")])

class Regression(unittest.TestCase):
    def test_distinct_branches_still_traversed(self):
        paths = [path for path, value in research({"first": [1], "second": [2]})]
        self.assertIn(("first", 0), paths)
        self.assertIn(("second", 0), paths)

if __name__ == "__main__":
    unittest.main()
