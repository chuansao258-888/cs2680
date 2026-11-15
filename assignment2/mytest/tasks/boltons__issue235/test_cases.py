import unittest
from boltons.ioutils import SpooledStringIO, SpooledBytesIO

class Reproduction(unittest.TestCase):
    def test_capability_methods(self):
        for cls in (SpooledStringIO, SpooledBytesIO):
            with self.subTest(cls=cls):
                stream = cls()
                self.assertTrue(stream.seekable())
                self.assertTrue(stream.readable())
                self.assertTrue(stream.writable())

class Regression(unittest.TestCase):
    def test_read_write_seek(self):
        for cls, value in ((SpooledStringIO, "hello"), (SpooledBytesIO, b"hello")):
            with self.subTest(cls=cls):
                stream = cls()
                stream.write(value)
                stream.seek(0)
                self.assertEqual(stream.read(), value)

if __name__ == "__main__":
    unittest.main()
