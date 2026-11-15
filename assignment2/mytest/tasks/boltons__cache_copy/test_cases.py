import unittest
from boltons.cacheutils import LRI, LRU


class Reproduction(unittest.TestCase):
    def test_copy_preserves_state_eviction_and_callback(self):
        for cls in (LRI, LRU):
            with self.subTest(cache=cls.__name__):
                handler = lambda key: key.upper()
                cache = cls(max_size=2, values=[('a', 1), ('b', 2)], on_miss=handler)
                cache['a'] = 3
                hits = cache.hit_count
                copied = cache.copy()
                self.assertEqual(cache.hit_count, hits)
                self.assertIs(copied.on_miss, handler)
                self.assertEqual(copied.hit_count, 0)
                cache['c'] = copied['c'] = 4
                self.assertEqual(set(cache), set(copied))
                self.assertEqual(copied['missing'], 'MISSING')
                self.assertNotIn('missing', cache)


class Regression(unittest.TestCase):
    def test_basic_cache_behavior(self):
        for cls in (LRI, LRU):
            with self.subTest(cache=cls.__name__):
                cache = cls(max_size=2)
                cache['a'] = 1
                cache['b'] = 2
                self.assertEqual(cache['a'], 1)
                self.assertEqual(len(cache), 2)


if __name__ == '__main__':
    unittest.main()
