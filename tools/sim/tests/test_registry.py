import http.client
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

REGISTRY = Path(__file__).resolve().parents[3] / ".github" / "scripts" / "mock-registry.py"
ACCOUNT = "/v1/account/a1"
LOG = b"the account log of a1"
# The members that get a post of a group of 101.
READERS = 100


class Burst(unittest.TestCase):
    """The registry when every member of a group looks one account up at once."""

    def setUp(self):
        registry = subprocess.Popen(
            [sys.executable, "-B", str(REGISTRY), "--port", "0"],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(registry.stdout.close)
        self.addCleanup(registry.wait)
        self.addCleanup(registry.terminate)
        self.port = urlsplit(registry.stdout.readline().strip()).port

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)

    def test_a_lookup_from_every_member_at_once_is_answered_within_a_second(self):
        poster = self.connection()
        self.addCleanup(poster.close)
        poster.request("POST", ACCOUNT, body=LOG)
        self.assertEqual(poster.getresponse().status, 200)

        gate = threading.Barrier(READERS)
        took: List[Optional[float]] = [None] * READERS

        def look_up(reader: int) -> None:
            connection = self.connection()
            gate.wait()
            asked = time.monotonic()
            try:
                connection.request("GET", ACCOUNT)
                if connection.getresponse().read() == LOG:
                    took[reader] = time.monotonic() - asked
            except OSError:
                pass
            finally:
                connection.close()

        readers = [threading.Thread(target=look_up, args=(reader,)) for reader in range(READERS)]
        for reader in readers:
            reader.start()
        for reader in readers:
            reader.join()

        # `None` stands for a lookup that got no answer.
        late = [seconds for seconds in took if seconds is None or seconds >= 1]
        self.assertEqual(late, [])


if __name__ == "__main__":
    unittest.main()
