import unittest
from boltons.strutils import singularize

class Reproduction(unittest.TestCase):
    def test_senses(self):
        self.assertEqual(singularize("senses"), "sense")

class Regression(unittest.TestCase):
    def test_other_words(self):
        self.assertEqual(singularize("lenses"), "lens")
        self.assertEqual(singularize("cats"), "cat")

if __name__ == "__main__":
    unittest.main()
