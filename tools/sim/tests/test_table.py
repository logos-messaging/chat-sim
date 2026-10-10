import unittest

from sim import table


def report(**changed) -> dict:
    whole = {
        "peers": 3,
        "ok": True,
        "stopped": None,
        "timers": "fast",
        "batch": 10,
        "max_message_kib": 512,
        "nodes": [
            {
                "name": name,
                "dials": dials,
                "ready_s": ready,
                "start_s": start,
                "wired": {"peers": wired, "mesh_peers": wired_mesh},
                "end": {
                    "peers": end,
                    "mesh_peers": end_mesh,
                    "publish_refused": refused,
                    "inbound_dropped": dropped,
                },
            }
            for name, dials, ready, start, wired, wired_mesh, end, end_mesh, refused, dropped in (
                ("saro", [], 3.125, 3.125, 12, 11, 10, 9, 8, 7),
                ("raya", ["saro"], 6.25, 3.0, 22, 21, 20, 19, 18, 17),
                ("pax", ["saro", "raya"], 9.375, 2.875, 32, 31, 30, 29, 28, 27),
            )
        ],
        "batches": [
            {
                "joiners": 2,
                "members": 3,
                "joined_s": 0.75,
                "seated_s": 0.875,
                "welcome_bytes": [7121, 7121],
            }
        ],
        "post": {
            "poster": "pax",
            "readers": 2,
            "unread": [],
            "read": {"count": 2, "median": 0.0625, "worst": 0.125},
            "delivered": {"count": 2, "median": 0.03125, "worst": 0.0625},
            "lookup_mean": 0.046875,
            "lookups": 2,
        },
    }
    whole.update(changed)
    return whole


class Render(unittest.TestCase):
    def test_a_whole_run(self):
        self.assertEqual(
            table.render(report()),
            "3 peers, fast group timers, batches of 10, largest message 512 KiB\n"
            "every member read the post\n"
            "\n"
            "node   ready   start  dials  wired  mesh  end  mesh  refused  dropped\n"
            "saro  3.125s  3.125s      0     12    11   10     9        8        7\n"
            "raya  6.250s  3.000s      1     22    21   20    19       18       17\n"
            "pax   9.375s  2.875s      2     32    31   30    29       28       27\n"
            "\n"
            "batch  joiners  members  joined  seated  Welcome bytes\n"
            "1            2        3  0.750s  0.875s       2 x 7121\n"
            "\n"
            "post from pax: read by 2 of 2, median 0.062s, worst 0.125s\n"
            "delivered, by when the lookup reached the registry, median 0.031s, worst 0.062s\n"
            "registry lookup: mean 0.047s\n",
        )

    def test_a_run_that_stopped_while_its_peers_started(self):
        nodes = [
            {"name": "saro", "dials": [], "ready_s": 3.125, "start_s": 3.125},
            {"name": "raya", "dials": ["saro"]},
        ]
        stopped = report(
            ok=False,
            stopped="raya: no ready line within the budget",
            nodes=nodes,
            batches=[],
            post=None,
        )

        self.assertEqual(
            table.render(stopped),
            "3 peers, fast group timers, batches of 10, largest message 512 KiB\n"
            "stopped: raya: no ready line within the budget\n"
            "\n"
            "node   ready   start  dials  wired  mesh  end  mesh  refused  dropped\n"
            "saro  3.125s  3.125s      0      -     -    -     -        -        -\n"
            "raya       -       -      1      -     -    -     -        -        -\n",
        )

    def test_a_post_some_did_not_read_names_how_many_did(self):
        post = dict(report()["post"], unread=["saro"], delivered=None, lookup_mean=None, lookups=1)
        post["read"] = {"count": 1, "median": 0.0625, "worst": 0.0625}
        text = table.render(report(ok=False, stopped="the post was not read by: saro", post=post))

        self.assertIn("stopped: the post was not read by: saro\n", text)
        self.assertIn("post from pax: read by 1 of 2, median 0.062s, worst 0.062s\n", text)
        self.assertIn("registry: 1 lookups of the poster, not one a reader", text)

    def test_welcomes_of_several_sizes_show_the_smallest_and_the_largest(self):
        batch = dict(report()["batches"][0], welcome_bytes=[7200, 7121])

        self.assertIn("2 x 7121..7200\n", table.render(report(batches=[batch])))


if __name__ == "__main__":
    unittest.main()
