import unittest
from boltons.listutils import BarrelList
from boltons.setutils import IndexedSet


def multisegment():
    values = BarrelList(range(30000))
    values.pop(0)
    values.insert(0, 0)
    return values


class Reproduction(unittest.TestCase):
    def test_bounds_and_insertions(self):
        values = multisegment()
        self.assertGreater(len(values.lists), 1)
        with self.assertRaises(IndexError):
            _ = values[len(values)]
        values.insert(len(values) + 100, 'end')
        values.insert(-len(values) - 100, 'start')
        self.assertEqual(values[0], 'start')
        self.assertEqual(values[-1], 'end')

    def test_pop_after_tail_drained(self):
        values = multisegment()
        for _ in range(len(values.lists[-1])):
            values.pop(len(values) - 1)
        expected = values[-1]
        self.assertEqual(values.pop(), expected)

    def test_bulk_removal_avoids_repeated_compaction(self):
        class CountingSet(IndexedSet):
            calls = 0

            def _compact(self):
                self.calls += 1
                return super()._compact()

        for method in ('difference_update', 'intersection_update'):
            with self.subTest(method=method):
                values = CountingSet(range(10000))
                other = set(range(0, 10000, 2))
                getattr(values, method)(other)
                expected = list(range(1 if method == 'difference_update' else 0, 10000, 2))
                self.assertEqual(list(values), expected)
                self.assertEqual(values[100], expected[100])
                self.assertLessEqual(values.calls, 1)


class Regression(unittest.TestCase):
    def test_small_operations(self):
        values = BarrelList([1, 2, 3])
        values.insert(1, 4)
        self.assertEqual(list(values), [1, 4, 2, 3])
        indexed = IndexedSet(range(10))
        indexed.difference_update({1, 3})
        self.assertEqual(list(indexed), [0, 2, 4, 5, 6, 7, 8, 9])


if __name__ == '__main__':
    unittest.main()
