import unittest

from sim import measure


class Spread(unittest.TestCase):
    def test_no_times_have_no_spread(self):
        self.assertIsNone(measure.spread([]))

    def test_an_odd_count_has_its_middle_time_as_median(self):
        self.assertEqual(
            measure.spread([0.5, 0.1, 0.3]), {"count": 3, "median": 0.3, "worst": 0.5}
        )

    def test_an_even_count_has_the_mean_of_its_middle_two(self):
        self.assertEqual(
            measure.spread([4.0, 1.0, 2.0, 3.0]), {"count": 4, "median": 2.5, "worst": 4.0}
        )


if __name__ == "__main__":
    unittest.main()
