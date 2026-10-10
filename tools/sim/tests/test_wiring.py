import unittest

from sim.wiring import LINKS, dials, links, strides

# Every size a group is meant to reach, and the two below the ring.
SIZES = range(2, 301)

# Relay peers a node keeps of those that dialed it, at the library's defaults: 150 connections,
# half of them for relay, two thirds of those inbound. One more is evicted.
ACCEPTS = 50


class Wiring(unittest.TestCase):
    def test_every_dial_is_to_a_node_started_earlier(self):
        for n in SIZES:
            for node, dialed in enumerate(dials(n)):
                self.assertTrue(all(0 <= other < node for other in dialed), (n, node, dialed))

    def test_no_node_dials_itself_or_one_node_twice(self):
        for n in SIZES:
            for node, dialed in enumerate(dials(n)):
                self.assertNotIn(node, dialed, n)
                self.assertEqual(len(dialed), len(set(dialed)), (n, node, dialed))

    def test_no_link_is_dialed_from_both_ends(self):
        for n in SIZES:
            dialing = dials(n)
            pairs = [(node, other) for node, dialed in enumerate(dialing) for other in dialed]
            self.assertEqual(len(pairs), len({frozenset(pair) for pair in pairs}), n)

    def test_from_seven_nodes_up_each_has_six_links(self):
        for n in SIZES:
            if n > LINKS:
                self.assertEqual(links(dials(n)), [LINKS] * n, n)

    def test_fewer_than_seven_nodes_link_to_every_other(self):
        for n in range(2, LINKS + 1):
            self.assertEqual(links(dials(n)), [n - 1] * n, n)

    def test_no_node_is_dialed_by_more_than_its_links_which_is_fewer_than_it_accepts(self):
        self.assertLess(LINKS, ACCEPTS)
        for n in SIZES:
            dialed_by = [0] * n
            for dialed in dials(n):
                for other in dialed:
                    dialed_by[other] += 1
            self.assertLessEqual(max(dialed_by), LINKS, n)

    def test_every_node_is_reached_from_the_first(self):
        for n in SIZES:
            dialing = dials(n)
            linked = [set(dialed) for dialed in dialing]
            for node, dialed in enumerate(dialing):
                for other in dialed:
                    linked[other].add(node)
            reached, frontier = {0}, [0]
            while frontier:
                for other in linked[frontier.pop()] - reached:
                    reached.add(other)
                    frontier.append(other)
            self.assertEqual(len(reached), n, n)

    def test_the_strides_differ_and_none_meets_itself_round_the_ring(self):
        for n in range(LINKS + 1, 301):
            ring, chord, longest = strides(n)
            self.assertEqual(ring, 1, n)
            self.assertTrue(ring < chord < longest, (n, chord, longest))
            self.assertLess(2 * longest, n, n)

    def test_the_first_node_dials_nobody(self):
        for n in SIZES:
            self.assertEqual(dials(n)[0], [], n)

    def test_a_ring_of_a_hundred(self):
        dialing = dials(100)
        self.assertEqual(strides(100), (1, 5, 25))
        self.assertEqual(dialing[1], [0])
        self.assertEqual(dialing[30], [5, 25, 29])
        self.assertEqual(dialing[99], [0, 4, 24, 74, 94, 98])


if __name__ == "__main__":
    unittest.main()
