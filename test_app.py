
import unittest
from app import app

class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_dynamic_location(self):
        # Test the original bug: hardcoded "Balıkesir"
        response = self.app.get('/')
        self.assertNotIn(b'Paris', response.data)

        # Test the fix: dynamic location
        response = self.app.post('/', data=dict(
            check_in='2025-12-01',
            check_out='2025-12-05',
            location='Paris',
            hotel1='Hotel A'
        ), follow_redirects=True)
        self.assertIn(b'Paris Comparison', response.data)
        self.assertIn(b'Paris Comparison', response.data)

if __name__ == '__main__':
    unittest.main()
