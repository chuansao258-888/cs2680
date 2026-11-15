import unittest
from datetime import date, timedelta
from itertools import islice
from boltons.timeutils import daterange

class Reproduction(unittest.TestCase):
    def test_equal_bounds_inclusive_terminates(self):
        day = date(2022, 1, 10)
        self.assertEqual(list(islice(daterange(day, day, inclusive=True), 3)), [day])

class Regression(unittest.TestCase):
    def test_increasing_and_exclusive_equal(self):
        day = date(2022, 1, 10)
        self.assertEqual(list(daterange(day, day)), [])
        self.assertEqual(list(daterange(day, day + timedelta(days=2))), [day, day + timedelta(days=1)])

if __name__ == "__main__":
    unittest.main()
