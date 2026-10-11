"""The report page as the collector serves it, and a browser's way in to it."""

import gzip
import hmac
import itertools
import json
import os
import re
import shutil
import subprocess
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from unittest import mock

from forks import serve
from forks.records import MAX_CONVO
from forks.serve import PAGE

from .test_rule import CONVO
from .test_serve import (
    DAY,
    MAX_BODY,
    NO_TOKEN,
    ON_EVERY_ANSWER,
    OTHER,
    POLICY,
    TOKEN,
    Answer,
    Collecting,
    Log,
    hexed,
    logged,
    member,
    numbered,
    positions,
    tree,
)

FILES = {
    "index.html": "text/html; charset=utf-8",
    "report.css": "text/css; charset=utf-8",
    "report.js": "text/javascript; charset=utf-8",
}
COOKIE = f"forks_token={TOKEN}"


def carried(answer: Answer, name: str) -> List[str]:
    """Each value an answer carries under a header's name."""
    return [value for header, value in answer.headers if header == name]


class Browsing(Collecting):
    """A collector that holds a group, and the calls a browser makes on it."""

    def setUp(self) -> None:
        super().setUp()
        self.run, self.receipt = self.served.share(logged(member("saro")))
        self.served.share(logged(member("raya")), "raya")

    def browse(self, method: str, path: str, body=None, **headers: str) -> Answer:
        """A call without the header an uploader shows the token in."""
        return self.served.call(method, path, body, None, headers=headers.items())

    def send(self, *lines: str) -> Answer:
        """What a request is answered whose lines go as they stand, as when a header comes twice."""
        request = "".join(f"{line}\r\n" for line in (*lines, "")).encode()
        head, _, body = self.served.raw(request).partition(b"\r\n\r\n")
        status, *headers = head.decode().split("\r\n")
        pairs = tuple(tuple(header.split(": ", 1)) for header in headers)
        return Answer(int(status.split()[1]), pairs, body)

    def reads(self) -> List[str]:
        return [
            "/",
            *(f"/page/{name}" for name in FILES),
            "/conversations",
            f"/conversations/{CONVO}",
            f"/runs/{self.run}/log?line=1",
        ]

    def writes(self) -> List[Tuple[str, str, Optional[bytes], Dict[str, str]]]:
        size = len(self.served.held(self.run))
        receipt = {"Forks-Receipt": self.receipt}
        return [
            ("POST", "/runs", b'{"name": "pax", "app": "tests"}', {}),
            ("PUT", f"/runs/{self.run}/log?offset={size}", b"more\n", receipt),
            ("PUT", f"/runs/{self.run}/log?offset={size}&final=1", b"", receipt),
            ("DELETE", f"/runs/{self.run}", None, receipt),
        ]


class WayIn(Browsing):
    def test_the_share_address_answers_303_to_the_page_and_sets_the_cookie(self):
        answer = self.browse("GET", f"/?token={TOKEN}")
        self.assertEqual((answer.status, answer.body), (303, b""))
        self.assertEqual(carried(answer, "Location"), ["./"])
        (cookie,) = carried(answer, "Set-Cookie")
        self.assertEqual(
            cookie.split("; "),
            [f"forks_token={TOKEN}", "HttpOnly", "SameSite=Lax", "Max-Age=2592000"],
        )
        self.assertEqual(30 * DAY, 2592000)
        self.assertEqual(carried(answer, "Content-Type") + carried(answer, "Content-Length"), [])

    def test_the_cookie_is_secure_when_a_proxy_says_the_browser_came_by_https(self):
        for said in ("https", "HTTPS", " https "):
            with self.subTest(said=said):
                answer = self.browse("GET", f"/?token={TOKEN}", **{"X-Forwarded-Proto": said})
                (cookie,) = carried(answer, "Set-Cookie")
                self.assertEqual(
                    cookie.split("; "),
                    [
                        f"forks_token={TOKEN}",
                        "HttpOnly",
                        "SameSite=Lax",
                        "Max-Age=2592000",
                        "Secure",
                    ],
                )
        for said in ("http", "", "https, http", "wss"):
            with self.subTest(said=said):
                answer = self.browse("GET", f"/?token={TOKEN}", **{"X-Forwarded-Proto": said})
                self.assertEqual(answer.status, 303)
                (cookie,) = carried(answer, "Set-Cookie")
                self.assertNotIn("Secure", cookie)

    def test_of_two_proxies_that_say_how_the_browser_came_the_first_is_believed(self):
        for first, second, secure in (("https", "http", True), ("http", "https", False)):
            with self.subTest(first=first, second=second):
                answer = self.send(
                    f"GET /?token={TOKEN} HTTP/1.1",
                    f"X-Forwarded-Proto: {first}",
                    f"X-Forwarded-Proto: {second}",
                )
                self.assertEqual(answer.status, 303)
                (cookie,) = carried(answer, "Set-Cookie")
                self.assertEqual("Secure" in cookie.split("; "), secure)

    def test_the_cookie_names_no_path_and_no_domain(self):
        (cookie,) = carried(self.browse("GET", f"/?token={TOKEN}"), "Set-Cookie")
        self.assertNotRegex(cookie, r"(?i)path|domain|expires")

    def test_it_lets_a_browser_in_whatever_cookie_or_header_stands_beside_it(self):
        beside = [
            {"Cookie": COOKIE},
            {"Cookie": "forks_token=the-token-of-last-month"},
            {"Authorization": f"Bearer {TOKEN}"},
            {"Authorization": "Bearer the-token-of-last-month"},
        ]
        for headers in beside:
            with self.subTest(headers=headers):
                answer = self.browse("GET", f"/?token={TOKEN}", **headers)
                self.assertEqual(answer.status, 303)
                self.assertEqual(len(carried(answer, "Set-Cookie")), 1)

    def test_the_cookie_it_sets_opens_the_page_it_sends_the_browser_to(self):
        (cookie,) = carried(self.browse("GET", f"/?token={TOKEN}"), "Set-Cookie")
        page = self.browse("GET", "/", Cookie=cookie.split("; ")[0])
        self.assertEqual((page.status, page.body), (200, (PAGE / "index.html").read_bytes()))

    def test_only_the_base_and_only_the_token_alone_in_its_query_is_the_way_in(self):
        held, written = tree(self.data), list(self.served.written)
        not_the_way = [
            f"/?token={TOKEN}&more=1",
            f"/?more=1&token={TOKEN}",
            f"/?token={TOKEN}&token={TOKEN}",
            f"/?token={TOKEN[:-1]}",
            f"/?token={TOKEN}s",
            f"/?token={TOKEN}%20",
            f"/?token={TOKEN[:-1]}%{ord(TOKEN[-1]):02x}",
            f"/?Token={TOKEN}",
            f"/?token%3D{TOKEN}",
            "/?token=",
            "/?token",
            f"/page/?token={TOKEN}",
            f"/page/index.html?token={TOKEN}",
            f"/index.html?token={TOKEN}",
        ]
        for path in not_the_way:
            with self.subTest(path=path):
                answer = self.browse("GET", path)
                self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
                self.assertEqual(carried(answer, "Set-Cookie") + carried(answer, "Location"), [])
        for method in ("POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                answer = self.browse(method, f"/?token={TOKEN}", b"" if method != "HEAD" else None)
                self.assertEqual(answer.status, 401)
                self.assertEqual(carried(answer, "Set-Cookie"), [])
        # An address that names no path is not the base.
        answer = self.send(f"GET ?token={TOKEN} HTTP/1.1")
        self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
        self.assertEqual(carried(answer, "Set-Cookie") + carried(answer, "Location"), [])
        self.assertEqual((tree(self.data), self.served.written), (held, written))

    def test_what_a_request_shows_is_compared_with_the_token_in_constant_time(self):
        with mock.patch.object(serve.hmac, "compare_digest", wraps=hmac.compare_digest) as compared:
            self.served.call("GET", "/conversations", token="shown-in-the-header")
            self.browse("GET", "/?token=shown-in-the-query")
            self.browse("GET", "/conversations", Cookie="forks_token=shown-in-the-cookie")
        self.assertEqual(
            [call.args for call in compared.call_args_list],
            [
                (b"shown-in-the-header", TOKEN.encode()),
                (b"shown-in-the-query", TOKEN.encode()),
                (b"shown-in-the-cookie", TOKEN.encode()),
            ],
        )

    def test_a_token_in_the_query_of_any_other_call_opens_nothing(self):
        held, written = tree(self.data), list(self.served.written)
        calls = [("GET", path, None) for path in self.reads()[1:]]
        calls += [(method, path, body) for method, path, body, _ in self.writes()]
        for method, path, body in calls:
            for query in (f"token={TOKEN}", f"forks_token={TOKEN}"):
                with self.subTest(method=method, path=path, query=query):
                    asked = path + ("&" if "?" in path else "?") + query
                    answer = self.browse(method, asked, body)
                    self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
        self.assertEqual((tree(self.data), self.served.written), (held, written))

    def test_with_a_token_already_shown_a_query_that_is_not_the_way_in_is_not_read(self):
        page = self.browse("GET", f"/?token={TOKEN[:-1]}", Cookie=COOKIE)
        self.assertEqual((page.status, page.body), (200, (PAGE / "index.html").read_bytes()))
        self.assertEqual(carried(page, "Set-Cookie"), [])


class Cookies(Browsing):
    def test_the_cookie_opens_every_get(self):
        with_the_header = {path: self.served.get(path) for path in self.reads()}
        for path, answer in with_the_header.items():
            with self.subTest(path=path):
                self.assertEqual(answer.status, 200)
                self.assertEqual(self.browse("GET", path, Cookie=COOKIE), answer)

    def test_the_cookie_opens_no_write(self):
        held, written = tree(self.data), list(self.served.written)
        for method, path, body, headers in self.writes():
            with self.subTest(method=method, path=path):
                answer = self.browse(method, path, body, Cookie=COOKIE, **headers)
                self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
        for method in ("HEAD", "PATCH", "OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.browse(method, "/conversations", Cookie=COOKIE).status, 401)
        self.assertEqual((tree(self.data), self.served.written), (held, written))
        # The same calls with the header are a run opened, bytes appended, a mark at a size the
        # run has grown past, and a delete.
        with_the_header = [
            self.served.call(method, path, body, headers=headers.items()).status
            for method, path, body, headers in self.writes()
        ]
        self.assertEqual(with_the_header, [201, 200, 409, 204])

    def test_a_wrong_cookie_opens_nothing(self):
        wrong = [
            f"forks_token={TOKEN[:-1]}",
            f"forks_token={TOKEN}s",
            f"forks_token={TOKEN.upper()}",
            f'forks_token="{TOKEN}"',
            f"forks_token={TOKEN[:-1]}%{ord(TOKEN[-1]):02x}",
            f"theme=dark, {COOKIE}",
            "forks_token=",
            "forks_token",
            f"Forks_Token={TOKEN}",
            f"forks_token_={TOKEN}",
            f"token={TOKEN}",
            f"other=forks_token={TOKEN}",
            TOKEN,
            "",
        ]
        for cookie, path in itertools.product(wrong, self.reads()):
            with self.subTest(cookie=cookie, path=path):
                answer = self.browse("GET", path, Cookie=cookie)
                self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))

    def test_of_two_cookies_one_that_is_the_token_opens_a_get(self):
        # A browser sends every cookie its address falls under, as when a proxy serves one
        # collector under a prefix of another's.
        both = [
            f"forks_token=the-token-of-last-month; {COOKIE}",
            f"{COOKIE}; forks_token=the-token-of-last-month",
            f"theme=dark;{COOKIE};  lang=en",
        ]
        for cookie in both:
            with self.subTest(cookie=cookie):
                self.assertEqual(self.browse("GET", "/conversations", Cookie=cookie).status, 200)
        two_wrong = "forks_token=the-token-of-last-month; forks_token=and-the-month-before"
        self.assertEqual(self.browse("GET", "/conversations", Cookie=two_wrong).status, 401)

    def test_the_token_in_any_cookie_header_of_a_request_opens_a_get(self):
        other, right = "Cookie: theme=dark", f"Cookie: {COOKIE}"
        for headers in ((other, right), (right, other)):
            with self.subTest(headers=headers):
                self.assertEqual(self.send("GET /conversations HTTP/1.1", *headers).status, 200)
        wrong = ("Cookie: theme=dark", "Cookie: forks_token=the-token-of-last-month")
        self.assertEqual(self.send("GET /conversations HTTP/1.1", *wrong).status, 401)

    def test_the_cookie_beside_a_wrong_header_opens_a_get_and_no_write(self):
        wrong = {"Authorization": f"Bearer {TOKEN[:-1]}", "Cookie": COOKIE}
        for path in self.reads():
            with self.subTest(path=path):
                self.assertEqual(self.browse("GET", path, **wrong).status, 200)
        held = tree(self.data)
        for method, path, body, headers in self.writes():
            with self.subTest(method=method, path=path):
                answer = self.browse(method, path, body, **wrong, **headers)
                self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
        self.assertEqual(tree(self.data), held)

    def test_a_wrong_cookie_beside_the_right_header_does_not_close_a_call(self):
        cookie = "forks_token=the-token-of-last-month"
        self.assertEqual(
            self.served.call("GET", "/conversations", headers={"Cookie": cookie}.items()).status,
            200,
        )


class Headers(Browsing):
    def answers(self) -> Dict[str, Answer]:
        """An answer of each status the collector gives, the page and its files among them."""
        size = len(self.served.held(self.run))
        answers = {
            "the way in, 303": self.browse("GET", f"/?token={TOKEN}"),
            "no token, 401": self.browse("GET", "/conversations"),
            "a list, 200": self.served.get("/conversations"),
            "a report, 200": self.served.get(f"/conversations/{CONVO}"),
            "lines, 200": self.served.get(f"/runs/{self.run}/log?line=1"),
            "no such conversation, 404": self.served.get(f"/conversations/{OTHER}"),
            "no such call, 404": self.served.get("/nothing"),
            "no such file, 404": self.served.get("/page/nothing.js"),
            "headers alone, 404": self.served.call("HEAD", "/conversations"),
            "a run, 201": self.served.call("POST", "/runs", b'{"name": "pax", "app": "tests"}'),
            "not a run, 400": self.served.call("POST", "/runs", b"no name and no app"),
            "no receipt, 403": self.served.put(self.run, None),
            "another size, 409": self.served.put(self.run, self.receipt, b"more\n", 0),
            "appended, 200": self.served.put(self.run, self.receipt, b"more\n", size),
            "too long, 413": self.too_long(),
            "deleted, 204": self.served.delete(self.run, self.receipt),
        }
        answers.update({f"/page/{name}, 200": self.served.get(f"/page/{name}") for name in FILES})
        answers["the page, 200"] = self.served.get("/")
        return answers

    def too_long(self) -> Answer:
        """What a write is answered that says its body is longer than the collector takes."""
        return self.send(
            f"PUT /runs/{self.run}/log?offset=0 HTTP/1.1",
            f"Authorization: Bearer {TOKEN}",
            f"Forks-Receipt: {self.receipt}",
            f"Content-Length: {MAX_BODY + 1}",
        )

    def test_every_answer_carries_the_four_headers_once(self):
        for name, answer in self.answers().items():
            with self.subTest(answer=name):
                self.assertEqual(answer.status, int(name[-3:]))
                self.assertEqual(answer.headers[: len(ON_EVERY_ANSWER)], ON_EVERY_ANSWER)
                for header, value in ON_EVERY_ANSWER:
                    self.assertEqual(carried(answer, header), [value])

    def test_a_request_that_is_not_http_is_answered_with_them_too(self):
        answer = self.served.raw(b"what a log says\r\n\r\n").decode()
        for header, value in ON_EVERY_ANSWER:
            self.assertEqual(answer.count(f"\r\n{header}: {value}\r\n"), 1)

    def test_a_request_line_that_names_no_version_is_answered_and_fails_nothing(self):
        written = list(self.served.written)
        for line in (f"GET /?token={TOKEN}", "GET /", "GET /conversations", f"GET ?token={TOKEN}"):
            with self.subTest(line=line):
                answer = self.served.raw(f"{line}\r\n".encode())
                self.assertEqual(json.loads(answer), {"error": "the request cannot be read"})
        self.assertEqual(self.served.written, written)

    def test_the_policy_lets_the_page_load_from_the_collector_alone_and_run_nothing_inline(self):
        sources = dict(part.split(" ", 1) for part in POLICY.split("; "))
        self.assertEqual(sources.pop("default-src"), "'none'")
        self.assertEqual(sources.pop("base-uri"), "'none'")
        self.assertEqual(sources.pop("form-action"), "'none'")
        self.assertEqual(sources.pop("frame-ancestors"), "'none'")
        self.assertEqual(set(sources.values()), {"'self'"})
        self.assertEqual(sorted(sources), ["connect-src", "img-src", "script-src", "style-src"])

    def test_no_answer_but_the_way_in_sets_a_cookie_or_names_a_place(self):
        for name, answer in self.answers().items():
            with self.subTest(answer=name):
                expected = 2 if name.startswith("the way in") else 0
                self.assertEqual(
                    len(carried(answer, "Set-Cookie") + carried(answer, "Location")), expected
                )


class Rereads(Browsing):
    """What the list and a report are answered when they are read again, and how a body is sent."""

    def read_again(self) -> List[str]:
        return ["/conversations", f"/conversations/{CONVO}"]

    def tags(self) -> Dict[str, str]:
        """The validator each address answers now."""
        return {path: carried(self.served.get(path), "ETag")[0] for path in self.read_again()}

    def said_more(self) -> None:
        """Saro's log holds a state more."""
        log = Log("saro", 11)
        log.epoch("saro", 3, "X3", "saro raya")
        self.served.put(self.run, self.receipt, logged(log))

    def coded(self) -> List[str]:
        """The addresses whose answer goes coded to a request that takes gzip."""
        return ["/", *(f"/page/{name}" for name in FILES), *self.read_again()]

    def test_the_list_and_a_report_carry_one_validator_and_no_other_answer_does(self):
        for path in self.read_again():
            with self.subTest(path=path):
                self.assertRegex(self.tags()[path], r'^W/"[0-9a-f]{64}"$')
                self.assertEqual(len(carried(self.served.get(path), "ETag")), 1)
        others = ["/", "/page/report.js", f"/runs/{self.run}/log?line=1", "/nothing"]
        for path in [*others, f"/conversations/{OTHER}"]:
            with self.subTest(path=path):
                answer = self.served.call("GET", path, headers=[("If-None-Match", "*")])
                self.assertEqual(carried(answer, "ETag"), [])
                self.assertNotEqual(answer.status, 304)

    def test_a_validator_changes_when_the_body_it_came_with_does_and_only_then(self):
        def read() -> Dict[str, Tuple[bytes, List[str]]]:
            answers = {path: self.served.get(path) for path in self.read_again()}
            return {path: (said.body, carried(said, "ETag")) for path, said in answers.items()}

        first = read()
        self.assertEqual(read(), first)
        # A second on, the list says its conversation went unheard a second longer.
        self.clock.now += 1
        later = read()
        self.said_more()
        more = read()
        for path in self.read_again():
            with self.subTest(path=path):
                unheard = path == "/conversations"
                self.assertEqual(later[path][0] != first[path][0], unheard)
                self.assertEqual(later[path][1] != first[path][1], unheard)
                self.assertNotEqual(more[path][0], later[path][0])
                self.assertNotEqual(more[path][1], later[path][1])

    def test_a_request_that_names_the_validator_is_answered_304_and_no_body(self):
        for path, tag in self.tags().items():
            strong = tag[2:]
            for named in (tag, strong, f'"0", {tag}', f'{strong} , W/"0"', "*"):
                with self.subTest(path=path, named=named):
                    answer = self.served.call("GET", path, headers=[("If-None-Match", named)])
                    self.assertEqual((answer.status, answer.body), (304, b""))
                    self.assertEqual(
                        answer.headers,
                        (*ON_EVERY_ANSWER, ("ETag", tag), ("Vary", "Accept-Encoding")),
                    )
            body = self.served.get(path).body
            for named in ('W/"0"', strong.strip('"'), f"{tag}0", f"w/{strong}", tag.upper(), ""):
                with self.subTest(path=path, named=named):
                    answer = self.served.call("GET", path, headers=[("If-None-Match", named)])
                    self.assertEqual((answer.status, answer.body), (200, body))
                    self.assertEqual(carried(answer, "ETag"), [tag])

    def test_of_two_headers_that_name_validators_either_may_name_it(self):
        for path, tag in self.tags().items():
            for named in (('"0"', tag), (tag, '"0"')):
                with self.subTest(path=path, named=named):
                    answer = self.send(
                        f"GET {path} HTTP/1.1",
                        f"Authorization: Bearer {TOKEN}",
                        *(f"If-None-Match: {name}" for name in named),
                    )
                    self.assertEqual((answer.status, answer.body), (304, b""))

    def test_a_validator_of_a_body_that_changed_since_is_answered_the_new_body(self):
        held = self.tags()
        self.said_more()
        for path, tag in self.tags().items():
            with self.subTest(path=path):
                self.assertNotEqual(tag, held[path])
                answer = self.served.call("GET", path, headers=[("If-None-Match", held[path])])
                self.assertEqual((answer.status, answer.body), (200, self.served.get(path).body))
                self.assertEqual(carried(answer, "ETag"), [tag])
                again = self.served.call("GET", path, headers=[("If-None-Match", tag)])
                self.assertEqual(again.status, 304)

    def test_a_validator_opens_nothing_without_the_token(self):
        for path, tag in self.tags().items():
            for named in (tag, "*"):
                with self.subTest(path=path, named=named):
                    answer = self.browse("GET", path, **{"If-None-Match": named})
                    self.assertEqual((answer.status, answer.body), (401, NO_TOKEN))
                    self.assertEqual(carried(answer, "ETag"), [])
                    cookie = {"If-None-Match": named, "Cookie": COOKIE}
                    self.assertEqual(self.browse("GET", path, **cookie).status, 304)

    def test_the_page_the_list_and_a_report_are_gzip_coded_for_a_request_that_names_gzip(self):
        takes = {
            "gzip": True,
            "GZIP": True,
            "deflate, gzip;q=0.5": True,
            "gzip;Q=0.5": True,
            "br;q=1.0, gzip ; q=0.001": True,
            "identity": False,
            "deflate, br": False,
            "gzip;q=0": False,
            "gzip; q=0.000": False,
            "x-gzip": False,
            "gzipped": False,
            "*": False,
            "": False,
        }
        for path in self.coded():
            plain = self.served.get(path)
            self.assertEqual(carried(plain, "Content-Encoding"), [])
            for named, coded in takes.items():
                with self.subTest(path=path, named=named):
                    answer = self.served.call("GET", path, headers=[("Accept-Encoding", named)])
                    self.assertEqual(answer.status, plain.status)
                    self.assertEqual(carried(answer, "Content-Encoding"), ["gzip"] * coded)
                    self.assertEqual(carried(answer, "Content-Length"), [str(len(answer.body))])
                    said = gzip.decompress(answer.body) if coded else answer.body
                    self.assertEqual(said, plain.body)
                    for name in ("Content-Type", "ETag"):
                        self.assertEqual(carried(answer, name), carried(plain, name))

    def test_of_two_headers_that_name_codings_either_may_name_gzip(self):
        for named in (("br", "gzip"), ("gzip", "br")):
            with self.subTest(named=named):
                answer = self.send(
                    "GET /conversations HTTP/1.1",
                    f"Authorization: Bearer {TOKEN}",
                    *(f"Accept-Encoding: {name}" for name in named),
                )
                self.assertEqual(carried(answer, "Content-Encoding"), ["gzip"])
                plain = self.served.get("/conversations").body
                self.assertEqual(gzip.decompress(answer.body), plain)

    def test_an_answer_that_may_go_coded_says_that_it_goes_by_the_coding_asked_for(self):
        asked = {"Accept-Encoding": "gzip"}
        answers = [self.served.get(path) for path in self.coded()]
        answers += [self.served.call("GET", path, headers=asked.items()) for path in self.coded()]
        tag = self.tags()["/conversations"]
        unchanged = self.served.call(
            "GET", "/conversations", headers=[("If-None-Match", tag), *asked.items()]
        )
        for answer in [*answers, unchanged]:
            with self.subTest(status=answer.status, body=len(answer.body)):
                self.assertEqual(answer.headers[: len(ON_EVERY_ANSWER)], ON_EVERY_ANSWER)
                self.assertEqual(carried(answer, "Vary"), ["Accept-Encoding"])
        self.assertEqual((unchanged.status, unchanged.body), (304, b""))
        self.assertEqual(carried(unchanged, "Content-Encoding"), [])

    def test_any_other_answer_goes_as_it_stands_whatever_coding_is_asked_for(self):
        asked = {"Accept-Encoding": "gzip"}
        lines = f"/runs/{self.run}/log?line=1"
        answers = []
        # A read that is never coded is answered as when no coding is asked for.
        for path in (lines, "/nothing", f"/conversations/{OTHER}", "/page/nothing.js"):
            answers.append(self.served.call("GET", path, headers=asked.items()))
            self.assertEqual(answers[-1], self.served.get(path), path)
        # So is a refusal, of whatever would have gone coded.
        for path in (*self.coded(), lines):
            answers.append(self.browse("GET", path, **asked))
            self.assertEqual(answers[-1], self.browse("GET", path), path)
            self.assertEqual((answers[-1].status, answers[-1].body), (401, NO_TOKEN), path)
        answers.append(self.browse("GET", f"/?token={TOKEN}", **asked))
        for method, path, body, receipt in self.writes():
            sent = {**receipt, **asked}.items()
            answers.append(self.served.call(method, path, body, headers=sent))
        # The final mark names the size before the append, and is refused for it.
        self.assertEqual([answer.status for answer in answers[-5:]], [303, 201, 200, 409, 204])
        for answer in answers:
            with self.subTest(status=answer.status, body=answer.body[:20]):
                self.assertEqual(carried(answer, "Vary") + carried(answer, "Content-Encoding"), [])
                # A body here is JSON, and begins as JSON does.
                self.assertIn(answer.body[:1], (b"{", b""))
                self.assertEqual(carried(answer, "Content-Length") == [], answer.body == b"")

    def test_a_coded_body_says_nothing_of_when_it_was_coded(self):
        answer = self.served.call("GET", "/conversations", headers=[("Accept-Encoding", "gzip")])
        # The four bytes after a gzip stream's flags are the time it was made at.
        self.assertEqual((answer.body[:4], answer.body[4:8]), (b"\x1f\x8b\x08\x00", bytes(4)))


class Files(Browsing):
    def test_the_base_is_the_page(self):
        answer = self.served.get("/")
        self.assertEqual((answer.status, answer.body), (200, (PAGE / "index.html").read_bytes()))
        self.assertEqual(carried(answer, "Content-Type"), ["text/html; charset=utf-8"])
        self.assertEqual(carried(answer, "Content-Length"), [str(len(answer.body))])

    def test_the_page_has_no_address_but_the_base_and_its_files_none_but_under_page(self):
        for path in ("/index.html", "/report.js", "/report.css", "/page"):
            with self.subTest(path=path):
                answer = self.browse("GET", path, Cookie=COOKIE)
                self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))

    def test_a_file_is_named_by_its_name_however_the_address_spells_it(self):
        for name in ("report%2Ejs", "%72eport.js", "report.j%73"):
            with self.subTest(name=name):
                answer = self.served.get(f"/page/{name}")
                script = (PAGE / "report.js").read_bytes()
                self.assertEqual((answer.status, answer.body), (200, script))

    def test_the_directory_holds_the_three_files_and_each_is_answered_with_its_type(self):
        self.assertEqual(sorted(file.name for file in PAGE.iterdir()), sorted(FILES))
        for name, kind in FILES.items():
            with self.subTest(name=name):
                answer = self.served.get(f"/page/{name}")
                self.assertEqual((answer.status, answer.body), (200, (PAGE / name).read_bytes()))
                self.assertEqual(carried(answer, "Content-Type"), [kind])
                self.assertEqual(carried(answer, "Content-Length"), [str(len(answer.body))])

    def test_every_address_the_page_loads_is_a_file_it_is_served(self):
        page = (PAGE / "index.html").read_text()
        loaded = re.findall(r'(?:src|href)="([^"#][^"]*)"', page)
        self.assertEqual(loaded, ["page/report.css", "page/report.js"])
        for address in loaded:
            self.assertEqual(self.served.get(f"/{address}").status, 200)

    def test_nothing_but_a_file_of_the_directory_can_be_named(self):
        outside = self.scratch / "page"
        outside.mkdir()
        (outside / "inside.js").write_text("inside")
        (outside / "a directory").mkdir()
        (outside / "a directory" / "deeper.js").write_text("deeper")
        (self.scratch / "above.js").write_text("above")
        absolute = str(self.scratch / "above.js")
        names = [
            "..",
            ".",
            "",
            "../above.js",
            "..%2Fabove.js",
            "..%2fabove.js",
            "%2e%2e/above.js",
            "%2e%2e%2fabove.js",
            "..%5Cabove.js",
            "inside.js/..%2F..%2Fabove.js",
            absolute,
            absolute.replace("/", "%2F"),
            "%2F" + absolute.replace("/", "%2F"),
            "inside.js%00",
            "inside.js%00.css",
            "%00",
            "inside.js/",
            "inside.js/more",
            "inside.js%2F",
            "inside.js%20",
            "%20inside.js",
            "INSIDE.JS",
            "inside",
            "a%20directory",
            "a%20directory/",
            "a%20directory/deeper.js",
            "a%20directory%2Fdeeper.js",
            "index.html",
            "report.js",
        ]
        with mock.patch.object(serve, "PAGE", outside):
            inside = self.served.get("/page/inside.js")
            self.assertEqual((inside.status, inside.body), (200, b"inside"))
            for name in names:
                with self.subTest(name=name):
                    answer = self.served.get(f"/page/{name}")
                    self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))
                    self.assertNotIn(b"above", answer.body)
                    self.assertNotIn(b"deeper", answer.body)

    def test_a_link_out_of_the_directory_is_not_followed_to_a_directory(self):
        outside = self.scratch / "page"
        outside.mkdir()
        (outside / "up").symlink_to(self.scratch, target_is_directory=True)
        with mock.patch.object(serve, "PAGE", outside):
            for name in ("up", "up/", "up%2Fdata"):
                with self.subTest(name=name):
                    self.assertEqual(self.served.get(f"/page/{name}").status, 404)

    def test_a_link_in_the_directory_to_a_file_is_that_file(self):
        # A package may be installed as links to its files.
        outside = self.scratch / "page"
        outside.mkdir()
        (self.scratch / "kept elsewhere.js").write_text("linked")
        (outside / "linked.js").symlink_to(self.scratch / "kept elsewhere.js")
        with mock.patch.object(serve, "PAGE", outside):
            answer = self.served.get("/page/linked.js")
        self.assertEqual((answer.status, answer.body), (200, b"linked"))

    def test_what_is_in_the_directory_and_is_no_file_is_not_read(self):
        outside = self.scratch / "page"
        outside.mkdir()
        (outside / "nothing.js").symlink_to(os.devnull)
        with mock.patch.object(serve, "PAGE", outside):
            self.assertEqual(self.served.get("/page/nothing.js").status, 404)

    def test_a_directory_without_the_page_answers_404(self):
        with mock.patch.object(serve, "PAGE", self.scratch / "no such directory"):
            for path in ("/", "/page/index.html"):
                with self.subTest(path=path):
                    answer = self.served.get(path)
                    self.assertEqual((answer.status, list(answer.said)), (404, ["error"]))

    def test_a_file_is_read_as_it_stands_when_it_is_asked_for(self):
        outside = self.scratch / "page"
        shutil.copytree(PAGE, outside)
        with mock.patch.object(serve, "PAGE", outside):
            (outside / "report.css").write_text("body { color: red; }")
            self.assertEqual(self.served.get("/page/report.css").body, b"body { color: red; }")

    def test_a_file_of_another_kind_is_answered_as_bytes_no_browser_runs(self):
        outside = self.scratch / "page"
        outside.mkdir()
        (outside / "notes.txt").write_text("<script>alert(1)</script>")
        with mock.patch.object(serve, "PAGE", outside):
            answer = self.served.get("/page/notes.txt")
        self.assertEqual(carried(answer, "Content-Type"), ["application/octet-stream"])
        self.assertEqual(carried(answer, "X-Content-Type-Options"), ["nosniff"])


class Sources(unittest.TestCase):
    """What the page's files hold, read as text: nothing that could turn a log line into code."""

    def read(self, name: str) -> str:
        return (PAGE / name).read_text(encoding="utf-8")

    def test_the_script_and_the_styles_make_none_of_the_calls_that_run_or_parse_text(self):
        forbidden = [
            r"innerHTML",
            r"outerHTML",
            r"insertAdjacentHTML",
            r"document\s*\.\s*write",
            r"\beval\b",
            r"\bFunction\b",
            r"createContextualFragment",
            r"DOMParser",
            r"srcdoc",
            r"\bimport\s*\(",
            r"set(?:Timeout|Interval)\s*\(\s*[\"'`]",
            r"javascript:",
        ]
        for name in ("report.js", "report.css"):
            text = self.read(name)
            for pattern in forbidden:
                with self.subTest(file=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text))

    def test_no_file_of_the_page_names_another_host(self):
        for name in FILES:
            with self.subTest(file=name):
                text = self.read(name)
                self.assertIsNone(re.search(r"(?i)https?:|wss?:|ftp:|//[a-z0-9]", text))
                elsewhere = r"(?i)@import|@font-face|url\(\s*[\"']?[^#\"')\s]"
                self.assertIsNone(re.search(elsewhere, text))

    def test_the_script_builds_the_document_from_elements_and_text_alone(self):
        script = self.read("report.js")
        self.assertIn("document.createElement(", script)
        self.assertIn(".textContent = ", script)
        # An attribute's name is written in the script, never read from an answer.
        for name in re.findall(r"setAttribute\(\s*([^,]+),", script):
            with self.subTest(attribute=name):
                self.assertRegex(name, r"^'[a-z-]+'$|^name$")
                self.assertNotRegex(name, r"^'(on[a-z]*|style|src|srcdoc|action|formaction)'$")
        self.assertIsNone(re.search(r"\.style\b|cssText|\.on[a-z]+\s*=", script))

    def test_what_makes_an_element_is_called_with_names_the_script_holds(self):
        script = self.read("report.js")
        # `el` and `svg` alone make an element, each of the tag it is called with.
        self.assertEqual(
            re.findall(r"createElement(?:NS)?\(([^)]*)\)", script),
            ["tag", "sprite.namespaceURI, tag"],
        )
        calls = re.findall(r"\b(el|svg)\(\s*([^,)]+)(?:,\s*(\S))?", script)
        defined = [("el", "tag", "c"), ("svg", "tag", "a")]
        self.assertEqual([call for call in calls if call[1] == "tag"], defined)
        loads = r"^'(script|style|i?frame|object|embed|link|meta|base|form|input|img|image)'$"
        for made, tag, then in sorted(set(calls) - set(defined)):
            with self.subTest(call=f"{made}({tag}"):
                self.assertRegex(tag, r"^'[a-z][a-z0-9]*'$")
                self.assertNotRegex(tag, loads)
                if made == "svg":
                    # What `svg` sets is the keys of an object written where it is called.
                    self.assertIn(then, ("", "{"))
        # A key is written in the script: none is worked out, and no object is spread into one.
        self.assertIsNone(re.search(r"[{,]\s*\[[^,\n]*\]\s*:", script))
        self.assertIsNone(re.search(r"\{\s*\.\.\.|\.\.\.[\w.]+(?:\([^()]*\))?\s*\}", script))
        runs = r"[{,]\s*'?(on[a-z]+|style|src|srcdoc|action|formaction)'?\s*:"
        self.assertIsNone(re.search(runs, script))

    def test_data_enters_an_address_only_through_encodeuricomponent(self):
        script = self.read("report.js")
        # Every call the script makes, as it is written: an id goes in as one part of a path.
        self.assertEqual(re.findall(r"\bfetch\(([^,)]*)", script), ["path"])
        self.assertEqual(
            re.findall(r"\bask\((.+?)\)[,;]\n", script),
            [
                "'conversations'",
                "part(id) && `conversations/${part(id)}`",
                "part(run) && `runs/${part(run)}/log?line=${encodeURIComponent(line)}`",
            ],
        )
        self.assertIn(
            "const part = (id) => (/^\\.*$/.test(id) ? null : encodeURIComponent(id));", script
        )
        # Every place a link leads to: the front page, or a conversation as a fragment.
        self.assertEqual(
            set(re.findall(r"setAttribute\('href', (.+?)\);\n", script)),
            {"'#'", "`#${encodeURIComponent(id)}`"},
        )
        # A mark is drawn by a reference into the page itself, by a name the script holds.
        self.assertEqual(re.findall(r"\bhref: (.+?) \}", script), ["`#mark-${name}`"])
        self.assertIsNone(re.search(r"location\s*(\.\s*\w+)?\s*=[^=]", script))
        self.assertIsNone(re.search(r"location\.(assign|replace)|window\.open|\.src\b", script))

    def test_the_page_holds_no_script_and_no_style_of_its_own(self):
        page = self.read("index.html")
        self.assertEqual(
            re.findall(r"<script\b[^>]*>[^<]*</script>", page),
            ['<script src="page/report.js" defer></script>'],
        )
        self.assertEqual(page.count("<script"), 1)
        inline = r"(?i)<style|\sstyle\s*=|\son[a-z]+\s*=|<base|<meta[^>]*http-equiv"
        self.assertIsNone(re.search(inline, page))
        loads = r"(?i)<(?:iframe|object|embed|form|img|link(?![^>]*stylesheet))"
        self.assertIsNone(re.search(loads, page))

    def test_the_type_is_public_sans_then_the_systems_and_no_font_is_shipped(self):
        styles = self.read("report.css")
        self.assertIn('--font: "Public Sans", system-ui, sans-serif;', styles)
        self.assertIn("font-family: var(--font);", styles)
        self.assertEqual({file.suffix for file in PAGE.iterdir()}, {".html", ".css", ".js"})

    def test_the_colours_the_spacing_and_the_branch_hues_are_custom_properties(self):
        styles = self.read("report.css")
        (root,) = re.findall(r":root \{(.*?)\n\}", styles, re.S)
        declared = dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", root))
        hues = {"--branch-a": "#3987e5", "--branch-b": "#d55181", "--branch-c": "#c98500"}
        self.assertEqual({name: declared[name] for name in hues}, hues)
        self.assertEqual(
            {name: declared[name] for name in ("--sp-tiny", "--sp-small", "--sp-medium")},
            {"--sp-tiny": "4px", "--sp-small": "8px", "--sp-medium": "12px"},
        )
        # Outside the properties, a colour is one of them.
        rules = styles.replace(root, "")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", rules), [])
        for used in set(re.findall(r"var\((--[a-z0-9-]+)", styles)):
            with self.subTest(property=used):
                self.assertIn(used, declared)

    def test_a_focused_control_is_outlined_and_no_rule_takes_the_outline_away(self):
        styles = self.read("report.css")
        ring = ":focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }"
        self.assertIn(ring, styles)
        self.assertIsNone(re.search(r"outline:\s*(none|0)\b", styles))

    def test_a_first_commit_is_in_the_colour_of_its_value_and_a_gap_stands_before_the_next(self):
        styles = self.read("report.css")
        self.assertIn(".kv dd .mono { color: inherit; }", styles)
        self.assertIn(".kv dd > div + div { margin-top: var(--sp-tiny); }", styles)


def split_logs() -> Dict[str, Log]:
    """Saro and Raya on one state at epoch 3, Pax and Mira on another."""
    four = "saro raya pax mira"
    logs = {name: member(name, 2, four) for name in four.split()}
    for name, state in (("saro", "A3"), ("raya", "A3"), ("pax", "B3"), ("mira", "B3")):
        logs[name].epoch(name, 3, state, four)
    return logs


MARKUP = '<img src=x onerror="window.pwned=1"><script>window.pwned=2</script>'
# A frame as long as the tool reads one.
LONG = "41d6" + "a9e0" * 3


def post_logs() -> Dict[str, bytes]:
    """The split group's logs once its members posted, as Saro, Raya and Pax share them.

    Saro posts before the split and everyone reads it. Raya's post after it is read on her side
    and is unreadable to Pax, and Pax's long one is unreadable on hers. A frame no post names
    reaches Pax. Around Saro's post his log holds lines that are markup.
    """
    logs = split_logs()
    logs["saro"].sent("saro", "8f21", 2)
    for name in ("raya", "pax"):
        logs[name].received(name, "8f21", "saro", 2)
    logs["raya"].sent("raya", "e3b9", 3)
    logs["saro"].received("saro", "e3b9", "raya", 3)
    logs["pax"].unreadable("pax", "e3b9", 3, "decrypt")
    logs["pax"].sent("pax", LONG, 3)
    for name in ("saro", "raya"):
        logs[name].unreadable(name, LONG, 3, "decrypt")
    logs["pax"].unreadable("pax", "77b4", 3, "epoch")
    sent = {name: logged(logs[name]) for name in ("saro", "raya", "pax")}
    said = f"2026-10-08T10:00:04.500Z  INFO chat_module::actions: {MARKUP}\n".encode()
    lines = sent["saro"].splitlines(keepends=True)
    sent["saro"] = b"".join(lines[:4] + [said] + lines[4:5] + [said] + lines[5:])
    return sent


class EndToEnd(Collecting):
    """Logs sent as an uploader sends them, and the report read as a browser reads it."""

    def setUp(self) -> None:
        super().setUp()
        way_in = self.served.call("GET", f"/?token={TOKEN}", token=None)
        (cookie,) = carried(way_in, "Set-Cookie")
        self.cookie = cookie.split("; ")[0]

    def browsed(self, path: str) -> dict:
        answer = self.served.call("GET", path, token=None, headers=[("Cookie", self.cookie)])
        self.assertEqual(answer.status, 200)
        return answer.said

    def test_two_logs_with_different_states_at_one_epoch_read_split_at_that_epoch(self):
        logs = split_logs()
        for name in ("saro", "pax"):
            self.served.share(logged(logs[name]), name)
        report = self.browsed(f"/conversations/{CONVO}")
        self.assertEqual((report["verdict"], report["split_epoch"]), ("split at epoch 3", 3))
        self.assertEqual(positions(report, ["saro", "pax"]), dict(saro="tip", pax="tip"))
        self.assertEqual(
            [(lane["under"], lane["branch"]) for lane in report["lanes"]],
            [("branch", "A"), ("branch", "B"), ("not placed", None)],
        )
        (listed,) = self.browsed("/conversations")["conversations"]
        self.assertEqual(
            (listed["id"], listed["verdict"], listed["split_epoch"]),
            (CONVO, "split at epoch 3", 3),
        )

    def test_one_sides_one_log_marked_final_reads_split_one_side_live(self):
        logs = split_logs()
        self.served.share(logged(logs["saro"]), "saro")
        self.served.share(logged(logs["pax"]), "pax", final=True)
        report = self.browsed(f"/conversations/{CONVO}")
        self.assertEqual(report["verdict"], "split at epoch 3, one side live")
        self.assertEqual(positions(report, ["saro", "pax"]), dict(saro="tip", pax="ended"))
        (listed,) = self.browsed("/conversations")["conversations"]
        self.assertEqual(listed["verdict"], "split at epoch 3, one side live")

    def test_both_marked_final_read_split_no_side_live(self):
        logs = split_logs()
        for name in ("saro", "pax"):
            self.served.share(logged(logs[name]), name, final=True)
        report = self.browsed(f"/conversations/{CONVO}")
        self.assertEqual(report["verdict"], "split at epoch 3, no side live")
        self.assertEqual(positions(report, ["saro", "pax"]), dict(saro="ended", pax="ended"))
        (listed,) = self.browsed("/conversations")["conversations"]
        self.assertEqual(listed["verdict"], "split at epoch 3, no side live")

    def test_a_mark_on_a_cell_leads_to_the_lines_around_its_record(self):
        logs = split_logs()
        sent = {name: logged(logs[name]) for name in ("saro", "pax")}
        for name, log in sent.items():
            self.served.share(log, name)
        report = self.browsed(f"/conversations/{CONVO}")
        names = {run["id"]: run["name"] for run in report["runs"]}
        for seat in report["installations"]:
            for step in seat["history"]:
                at = step["at"]
                with self.subTest(seat=seat["id"], epoch=step["epoch"]):
                    around = self.browsed(f"/runs/{at['run']}/log?line={at['line']}")["lines"]
                    (line,) = [each["text"] for each in around if each["line"] == at["line"]]
                    written = sent[names[at["run"]]].decode().split("\n")[at["line"] - 1]
                    self.assertEqual(line, written)
                    self.assertIn(f"epoch={step['epoch']} state={step['state']}", line)

    def shared_posts(self) -> Tuple[Dict[str, bytes], dict]:
        sent = post_logs()
        for name, log in sent.items():
            self.served.share(log, name)
        return sent, self.browsed(f"/conversations/{CONVO}")

    def test_a_post_has_a_row_and_a_cell_under_every_lane(self):
        _, report = self.shared_posts()
        saro, raya, pax, mira = map(hexed, ("saro", "raya", "pax", "mira"))
        posts = report["messages"]
        self.assertEqual(
            [(post["frame"], post["sender"], post["epoch"]) for post in posts],
            [("8f21", saro, 2), ("e3b9", raya, 3), (LONG, pax, 3)],
        )
        self.assertEqual(
            [post["cells"] for post in posts],
            [
                {saro: "sent", raya: "read", pax: "read", mira: "no log"},
                {saro: "read", raya: "sent", pax: "unreadable", mira: "no log"},
                {saro: "unreadable", raya: "unreadable", pax: "sent", mira: "no log"},
            ],
        )
        self.assertEqual(
            [post["why"] for post in posts],
            [{}, {pax: "decrypt"}, {saro: "decrypt", raya: "decrypt"}],
        )
        self.assertEqual((report["shown_messages"], report["unmatched_unreadable"]), (3, 1))
        # The columns a page draws are the lanes, and each installation is under one of them.
        columns = [seat for lane in report["lanes"] for seat in lane["installations"]]
        for post in posts:
            self.assertEqual(sorted(columns), sorted(post["cells"]))

    def test_a_cell_of_a_post_leads_to_the_lines_around_its_record(self):
        sent, report = self.shared_posts()
        names = {run["id"]: run["name"] for run in report["runs"]}
        wrote = {"sent": "sent", "read": "received", "unreadable": "unreadable"}
        led = 0
        for post in report["messages"]:
            self.assertEqual(post["marks"][post["sender"]], post["at"])
            for seat, cell in post["cells"].items():
                with self.subTest(frame=post["frame"][:8], seat=seat):
                    self.assertEqual(seat in post["marks"], cell in wrote)
                    if cell not in wrote:
                        continue
                    at = post["marks"][seat]
                    around = self.browsed(f"/runs/{at['run']}/log?line={at['line']}")["lines"]
                    (line,) = [each["text"] for each in around if each["line"] == at["line"]]
                    written = sent[names[at["run"]]].decode().split("\n")[at["line"] - 1]
                    self.assertEqual(line, written)
                    self.assertIn(f"libchat::groupv2trace: {wrote[cell]} installation={seat} ", line)
                    self.assertIn(f" frame={post['frame']}", line)
                    self.assertEqual(seat in post["why"], cell == "unreadable")
                    if cell == "unreadable":
                        self.assertTrue(line.endswith(f" why={post['why'][seat]}"))
                    self.assertTrue(line.startswith(at["time"]))
                    led += 1
        self.assertEqual(led, 9)

    def test_what_a_log_says_around_a_post_is_answered_as_text_in_json(self):
        _, report = self.shared_posts()
        at = report["messages"][0]["at"]
        path = f"/runs/{at['run']}/log?line={at['line']}"
        answer = self.served.call("GET", path, token=None, headers=[("Cookie", self.cookie)])
        self.assertEqual(carried(answer, "Content-Type"), ["application/json; charset=utf-8"])
        texts = [each["text"] for each in answer.said["lines"]]
        # The two lines before the record, the record, and the three after it.
        self.assertEqual(
            [text.endswith(MARKUP) for text in texts], [False, True, False, True, False, False]
        )

    def test_the_script_reads_every_field_the_json_holds_of_a_post(self):
        _, report = self.shared_posts()
        script = self.served.call(
            "GET", "/page/report.js", token=None, headers=[("Cookie", self.cookie)]
        ).body.decode()
        for field in report["messages"][0]:
            with self.subTest(field=field):
                self.assertRegex(script, rf"\bmessage\.{field}\b")
        for field in ("messages", "shown_messages", "unmatched_unreadable"):
            with self.subTest(field=field):
                self.assertIn(field, report)
                self.assertRegex(script, rf"\bconvo\.{field}\b")


NODE = shutil.which("node")
DOM = Path(__file__).with_name("dom.js")
REST = {"press": "[data-rest]"}
# How a cell is drawn for what became of a post: its class, and the sign and the word in it.
DRAWN = {
    "sent": ("mk sent", "sent"),
    "read": ("mk read", "\u2713read"),
    "unreadable": ("mk unread", "\u2715unreadable"),
    "no record": ("mk none", "no record"),
    "no log": ("mk hatch", ""),
    "not a member": ("mk out", ""),
}


def within(made: dict) -> Iterator[dict]:
    """An element as `tests/dom.js` writes one, and every element in it."""
    yield made
    for kid in made["kids"]:
        if isinstance(kid, dict):
            yield from within(kid)


def words(made: dict) -> str:
    return "".join(kid if isinstance(kid, str) else words(kid) for kid in made["kids"])


def classed(made: dict, cls: str) -> List[dict]:
    return [each for each in within(made) if cls in each["attrs"].get("class", "").split()]


def rows(state: dict) -> List[dict]:
    """The rows of the messages a document holds, a post each."""
    (table,) = classed(state["report"], "posts")
    return [row for row in within(table) if "data-key" in row["attrs"]]


def keys(state: dict) -> List[str]:
    return [row["attrs"]["data-key"] for row in rows(state)]


def fold(state: dict) -> List[str]:
    """What a document says of the posts it draws, and the button beside it."""
    (rest,) = classed(state["report"], "rest")
    return [words(made) for made in rest["kids"]]


@unittest.skipUnless(NODE, "node runs the page's script, and is not on this machine")
class Drawn(Collecting):
    """What the script draws of what a collector answers, in the document of `tests/dom.js`."""

    def setUp(self) -> None:
        super().setUp()
        self.runs = {name: self.served.share(log, name) for name, log in post_logs().items()}

    def post(self, hour: int, frame: str) -> None:
        """Saro posts in a later hour, and nobody reads it."""
        log = Log("saro", hour)
        log.sent("saro", frame, 3)
        self.served.put(*self.runs["saro"], logged(log))

    def answers(self) -> Dict[str, dict]:
        """What the collector answers each address the page asks of the conversation."""
        marks = [at for post in self.served.report()["messages"] for at in post["marks"].values()]
        paths = ["conversations", f"conversations/{CONVO}"]
        paths += [f"runs/{at['run']}/log?line={at['line']}" for at in marks]
        said = {}
        for path in paths:
            answer = self.served.get(f"/{path}")
            said[path] = {"status": answer.status, "text": answer.body.decode()}
        return said

    def draw(self, answers: Dict[str, dict], *steps: dict) -> List[dict]:
        """What the document holds once the page has read, and after each step a reader takes."""
        script = (PAGE / "report.js").read_text(encoding="utf-8")
        given = {"script": script, "hash": f"#{CONVO}", "answers": answers, "steps": steps}
        ran = subprocess.run(
            [NODE, str(DOM)], input=json.dumps(given), capture_output=True, text=True, timeout=60
        )
        self.assertEqual((ran.stderr, ran.returncode), ("", 0))
        return json.loads(ran.stdout)

    def test_a_post_is_a_row_and_each_cell_says_what_the_json_says_became_of_it(self):
        # Saro posts once Pax and Mira are off his roster, late in a second, and nobody reads it.
        log = Log("saro", 11)
        log.epoch("saro", 4, "A4", "saro raya")
        log.sent("saro", "5ac1", 4)
        self.served.put(*self.runs["saro"], logged(log).replace(b"02.000Z", b"02.9996Z"))
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        (state,) = self.draw(answers)
        order = [seat for lane in report["lanes"] for seat in lane["installations"]]
        self.assertEqual(len(rows(state)), 4)
        seen = set()
        for row, post in zip(rows(state), report["messages"]):
            sent, frame, sender, _, *cells = row["kids"]
            self.assertEqual(
                (words(sent), sent["attrs"]["title"]), (post["utc"], post["at"]["time"])
            )
            self.assertEqual((words(frame), frame["attrs"]["title"]), (post["frame"],) * 2)
            self.assertEqual(row["attrs"]["data-key"], f"post {post['frame']}")
            self.assertEqual(words(sender), post["sender"])
            self.assertEqual(len(cells), len(order))
            for seat, cell in zip(order, cells):
                with self.subTest(frame=post["frame"], seat=seat):
                    ((made,), fate, why) = cell["kids"], post["cells"][seat], post["why"].get(seat)
                    cls, said = DRAWN[fate]
                    of = f"frame {post['frame']} from {post['sender']}"
                    self.assertEqual(
                        made["attrs"]["aria-label"], ", ".join(filter(None, [seat, of, fate, why]))
                    )
                    self.assertEqual(
                        (made["attrs"]["class"], words(made)), (cls, said + (why or ""))
                    )
                    # A cell that holds no word says what it is to a pointer.
                    self.assertEqual(made["attrs"].get("title"), None if said else fate)
                    # A cell opens log lines where the JSON holds a record for it, and nowhere else.
                    opens = seat in post["marks"]
                    self.assertEqual(made["attrs"]["role"], "button" if opens else "img")
                    self.assertEqual("data-m" in made["attrs"], opens)
                    seen.add(fate)
        self.assertEqual(seen, set(DRAWN))
        self.assertEqual(report["messages"][-1]["utc"], "2026-10-08T11:00:02Z")
        (table,) = classed(state["report"], "posts")
        titles = [made for made in within(table["kids"][0]) if made["tag"] == "th"]
        self.assertEqual({made["attrs"]["scope"] for made in titles}, {"col"})

    def test_a_ring_says_how_many_installations_behind_stay_at_a_state(self):
        # Saro and Raya go on to epoch 3, Pax and Mira stay at epoch 2 and Kes at epoch 1.
        five = "saro raya pax mira kes"
        self.served = self.elsewhere()
        for name, epochs in (("saro", 3), ("raya", 3), ("pax", 2), ("mira", 2), ("kes", 1)):
            self.served.share(logged(member(name, epochs, five)), name)
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        self.assertEqual(
            positions(report, five.split()),
            dict(saro="tip", raya="tip", pax="behind", mira="behind", kes="behind"),
        )
        (state,) = self.draw(answers)
        lineage = keyed(state["report"], "Lineage of states")
        drawn = {}
        for node in (made for made in within(lineage) if made["tag"] == "g"):
            (title,) = [words(made) for made in node["kids"] if made["tag"] == "title"]
            rings = [made for made in node["kids"] if made["tag"] == "circle"][1:]
            said = [words(made) for made in classed(node, "stay") if made["tag"] == "text"]
            drawn[title.split(",")[0]] = (title.split(", ")[3:5], len(rings), said)
        self.assertEqual(
            drawn,
            {
                "epoch 1": (["reported by 5", "1 of them behind"], 1, ["1 behind"]),
                "epoch 2": (["reported by 4", "2 of them behind"], 1, ["2 behind"]),
                "epoch 3": (["reported by 2", "5 in its roster"], 0, []),
            },
        )

    def test_the_lineage_draws_a_state_in_its_row_and_names_a_row_for_its_newest_state(self):
        # Pax leaves Saro's and Raya's line at T2; Mira and Kes leave it at X1, and each other
        # at L3.
        five = "saro raya pax mira kes"
        wrote = {
            "saro": ["X1", "T2", "T3", "T4"],
            "raya": ["X1", "T2", "T3", "T4"],
            "pax": ["X1", "T2", "P3"],
            "mira": ["X1", "L2", "L3", "M4"],
            "kes": ["X1", "L2", "L3", "K4"],
        }
        self.served = self.elsewhere()
        for name, states in wrote.items():
            log = Log(name)
            log.start(name)
            for epoch, held in enumerate(states, 1):
                log.epoch(name, epoch, held, five)
            self.served.share(logged(log), name)
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        (state,) = self.draw(answers)
        lineage = keyed(state["report"], "Lineage of states")
        height = {}
        for drawn in (made for made in within(lineage) if made["tag"] == "g"):
            (title,) = [words(made) for made in drawn["kids"] if made["tag"] == "title"]
            circle = next(made for made in drawn["kids"] if made["tag"] == "circle")
            height[title.split(", ")[1].removeprefix("state ")] = float(circle["attrs"]["cy"])
        rows: Dict[int, List[dict]] = {}
        for held in report["states"]:
            rows.setdefault(held["row"], []).append(held)
        self.assertEqual(sorted(rows), [0, 1, 2, 3])
        at = [{height[held["state"]] for held in rows[row]} for row in sorted(rows)]
        self.assertTrue(all(len(heights) == 1 for heights in at), at)
        self.assertEqual(at, sorted(at, key=min))
        self.assertEqual(len({min(heights) for heights in at}), len(at))
        names = [
            (float(made["attrs"]["y"]), words(made))
            for made in within(lineage)
            if made["tag"] == "text"
            and made["attrs"].get("x") == "8"
            and "class" not in made["attrs"]
        ]
        newest = [rows[row][-1] for row in sorted(rows)]
        self.assertEqual(
            names,
            [(min(heights) + 4, f"Branch {held['branch']}") for heights, held in zip(at, newest)],
        )

    def test_a_conversation_of_few_installations_is_drawn_a_lane_for_each_and_nothing_unfolds(self):
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        self.assertIs(report["folded"], False)
        (state,) = self.draw(answers)
        order = [seat for lane in report["lanes"] for seat in lane["installations"]]
        self.assertEqual(lines(state, "Installations"), [f"lane {seat}" for seat in order])
        self.assertEqual(lines(state, "Timeline"), [f"time {seat}" for seat in order])
        self.assertEqual((unfolding(state["report"]), classed(state["report"], "fold")), ([], []))

    def test_a_cell_and_a_mark_of_a_post_ask_for_the_lines_of_their_own_record(self):
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        posts = report["messages"]
        order = [seat for lane in report["lanes"] for seat in lane["installations"]]
        # The cells stand post by post, and the marks of the timeline installation by installation.
        cells = [(post, seat) for post in posts for seat in order if seat in post["marks"]]
        marks = [(post, seat) for seat in order for post in posts if seat in post["marks"]]
        first, *pressed = self.draw(
            answers,
            *({"press": ".mk[data-m]", "nth": nth} for nth in range(len(cells))),
            *({"press": ".post[data-m]", "nth": nth} for nth in range(len(marks))),
        )
        for cls, records in (("mk", cells), ("post", marks)):
            made = [each for each in classed(first["report"], cls) if "data-m" in each["attrs"]]
            self.assertEqual((cls, len(made)), (cls, 9))
            self.assertEqual(
                {each["attrs"]["aria-label"].split(",")[0] for each in made},
                {seat for _, seat in records},
            )
        for state, (post, seat) in zip(pressed, cells + marks):
            with self.subTest(frame=post["frame"], seat=seat):
                at = post["marks"][seat]
                self.assertEqual(state["asked"], [f"runs/{at['run']}/log?line={at['line']}"])
                bar = f"line {at['line']}{seat}, frame {post['frame']} "
                self.assertIn(bar, words(state["log"]))

    def test_the_reports_of_frames_no_post_names_are_counted_as_the_json_counts_them(self):
        log = Log("raya", 11)
        log.unreadable("raya", "77b4", 3, "decrypt")
        self.served.put(*self.runs["raya"], logged(log))
        (state,) = self.draw(self.answers())
        said = [words(made) for made in classed(state["report"], "quiet")]
        counted = [each for each in said if "no known post" in each]
        self.assertEqual(
            counted,
            [
                "Reports of an unreadable frame of no known post: 2. Each is of a frame that could "
                "not be read and that no shared log says was a post, and a frame has one for each "
                "time an installation reported it. A vote or any other encrypted frame of the "
                "group counts as much as a post whose sender shares no log."
            ],
        )

    def test_the_newest_posts_are_drawn_until_the_reader_asks_for_all(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        self.assertEqual(report["shown_messages"], 2)
        every = [f"post {post['frame']}" for post in report["messages"]]
        newest = "The newest 2 of 3 posts are drawn, here and on the timeline."
        states = self.draw(answers, REST, REST)
        for state, whole in zip(states, (False, True, False)):
            with self.subTest(whole=whole):
                drawn = every if whole else every[1:]
                self.assertEqual(keys(state), drawn)
                self.assertEqual(
                    fold(state),
                    ["All 3 posts are drawn, here and on the timeline.", "Draw the newest 2 only"]
                    if whole
                    else [newest, "Draw all 3"],
                )
                # The timeline marks the posts that are drawn, and says so while they are not all.
                marks = classed(state["report"], "post")
                self.assertEqual(
                    len([each for each in marks if "data-m" in each["attrs"]]),
                    sum(len(post["marks"]) for post in report["messages"][0 if whole else 1 :]),
                )
                noted = [words(made) for made in classed(state["report"], "quiet")]
                self.assertEqual(
                    "The posts marked are those the messages below draw, of 3." in noted, not whole
                )
        # The button a reader pressed holds the focus in what is drawn of the press.
        for state, button in zip(states[1:], ("Draw the newest 2 only", "Draw all 3")):
            self.assertEqual(
                [words(made) for made in within(state["report"]) if made.get("focused")], [button]
            )

    def test_a_press_for_the_newest_alone_closes_the_lines_of_an_older_post(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        opened, folded = self.draw(self.answers(), REST, {"press": ".mk[data-m]"}, REST)[2:]
        self.assertIn(", frame 8f21 from ", words(opened["log"]))
        self.assertEqual((len(rows(opened)), len(rows(folded)), folded["log"]), (3, 2, None))
        self.assertEqual(
            fold(folded),
            ["The newest 2 of 3 posts are drawn, here and on the timeline.", "Draw all 3"],
        )

    def test_a_read_by_the_clock_keeps_a_post_drawn_while_its_lines_are_open(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        before = self.answers()
        self.post(11, "5ac1")
        self.post(12, "9d07")
        _, opened, read, _, folded = self.draw(
            before, {"press": ".mk[data-m]"}, {"answers": self.answers()}, REST, REST
        )
        self.assertIn(", frame e3b9 from ", words(opened["log"]))
        self.assertEqual(keys(read), ["post e3b9", f"post {LONG}", "post 5ac1", "post 9d07"])
        self.assertIn(", frame e3b9 from ", words(read["log"]))
        self.assertEqual(
            fold(read),
            [
                "The newest 2 of 5 posts are drawn, here and on the timeline, and the older ones "
                "back to the one that is open or has the focus.",
                "Draw all 5",
            ],
        )
        # A press of the reader's own keeps none.
        self.assertEqual((keys(folded), folded["log"]), (["post 5ac1", "post 9d07"], None))

    def test_a_row_in_sight_stays_where_it_stood_in_its_box_while_rows_come_and_go(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 16))
        for hour, frame in ((11, "5ac1"), (12, "9d07"), (13, "c4d2")):
            self.post(hour, frame)
        before = self.answers()
        self.post(14, "b17e")
        # The box shows two rows, and is scrolled to the newest two of the four that are drawn.
        scroll = {"scroll": ".postsbox", "top": 20}
        first, *states = self.draw(before, scroll, {"answers": self.answers()}, REST, REST)
        self.assertEqual(keys(first), [f"post {LONG}", "post 5ac1", "post 9d07", "post c4d2"])
        self.assertEqual([len(rows(state)) for state in states], [4, 4, 7, 4])
        self.assertEqual(keys(states[1])[-1], "post b17e")
        for state in states:
            self.assertEqual(
                state["sight"]["card scroll postsbox"], [["post 9d07", 0], ["post c4d2", 10]]
            )

    def test_the_window_stays_while_the_oldest_rows_leave_a_box_that_is_at_its_top(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 16))
        for hour, frame in ((11, "5ac1"), (12, "9d07"), (13, "c4d2")):
            self.post(hour, frame)
        before = self.answers()
        self.post(14, "b17e")
        after = {"answers": self.answers()}
        # The window is at the messages, where rows of the box stand above its middle.
        reach = {"reach": '[data-key="Messages"]'}
        for opened in (False, True):
            with self.subTest(opened=opened):
                # The lines of a post two rows down are open, or none are.
                steps = [reach, {"press": ".mk[data-m]", "nth": 4}] if opened else [reach]
                *_, reached, read = self.draw(before, *steps, after)
                box = "card scroll postsbox"
                self.assertEqual(reached["sight"][box][0], [f"post {LONG}", 0])
                self.assertEqual(read["sight"][box], [["post 5ac1", 0], ["post 9d07", 10]])
                self.assertEqual(opened, ", frame 9d07 from " in words(read["log"] or {"kids": []}))
                self.assertGreater(reached["window"], 0)
                self.assertEqual(read["window"], reached["window"])

    def test_a_row_holds_the_window_where_its_box_does_not_scroll(self):
        self.served = self.elsewhere()
        saro, raya = member("saro", 1), member("raya", 1, hour=9)
        saro.sent("saro", "9d07", 1)
        raya.sent("raya", "5ac1", 1)
        self.served.share(logged(saro), "saro")
        before = self.answers()
        # A post sent an hour earlier comes with a log shared later, and stands above.
        self.served.share(logged(raya), "raya")
        reach = {"reach": '[data-key="Messages"]'}
        _, reached, read = self.draw(before, reach, {"answers": self.answers()})
        self.assertEqual((keys(reached), keys(read)), (["post 9d07"], ["post 5ac1", "post 9d07"]))
        self.assertEqual(read["window"], reached["window"] + 10)

    def test_the_button_stays_where_it_is_when_its_press_makes_the_box_higher(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 4))
        # The window is at the part under the messages, which a higher box pushes down.
        reach = {"reach": '[data-key="The state each branch holds"]'}
        _, reached, whole = self.draw(self.answers(), reach, REST)
        self.assertEqual((len(rows(reached)), len(rows(whole))), (1, 3))
        self.assertGreater(reached["window"], 0)
        self.assertEqual(whole["window"], reached["window"])

    def test_the_line_of_when_it_was_last_heard_from_keeps_its_words_through_a_press(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        first, whole = self.draw(self.answers(), REST)
        (was,), (line,) = (
            [made for made in within(state["report"]) if "data-heard" in made["attrs"]]
            for state in (first, whole)
        )
        self.assertRegex(words(was), r"^Last heard from \d+ s ago$")
        self.assertRegex(was["attrs"]["title"], r"^2026-\S+Z$")
        self.assertEqual((words(line), line["attrs"]["title"]), (words(was), was["attrs"]["title"]))

    def test_every_post_stays_drawn_while_another_conversation_is_asked_for_and_not_drawn(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        every = ["All 3 posts are drawn, here and on the timeline.", "Draw the newest 2 only"]
        newest = ["The newest 2 of 3 posts are drawn, here and on the timeline.", "Draw all 3"]
        # The reader is back before the other conversation is answered: nothing was drawn.
        back = [{"hash": "#another"}, {"hash": f"#{CONVO}"}]
        away, anew = {"hash": "#"}, {"hash": f"#{CONVO}"}
        states = self.draw(self.answers(), REST, back, REST, REST, away, anew)
        _, whole, returned, folded, again, _, anew = states
        self.assertEqual(
            [fold(state) for state in (whole, returned, folded, again)],
            [every, every, newest, every],
        )
        # The list of conversations was drawn in between.
        self.assertEqual(fold(anew), newest)

    def cells(self, state: dict) -> List[Tuple[str, bool, bool]]:
        """The cells of the messages that open log lines: its name, and whether each is the stop
        of the Tab key and whether it has the focus."""
        (table,) = classed(state["report"], "posts")
        return [
            (
                made["attrs"]["aria-label"],
                made["attrs"].get("tabindex") == "0",
                bool(made.get("focused")),
            )
            for made in within(table)
            if "data-m" in made["attrs"]
        ]

    def test_a_read_leaves_the_stop_where_the_arrow_keys_took_it_while_lines_are_open(self):
        before = self.answers()
        self.post(11, "5ac1")
        steps = [{"press": ".mk[data-m]"}, {"focus": ".mk[data-m]"}, {"key": "ArrowRight"}]
        first, *_, moved, read = self.draw(before, *steps, {"answers": self.answers()})
        (opened, _, _), (beside, _, _) = self.cells(first)[:2]
        self.assertEqual(len(rows(read)), len(rows(moved)) + 1)
        for state in (moved, read):
            self.assertEqual(self.cells(state)[:2], [(opened, False, False), (beside, True, True)])
            self.assertEqual(sum(stop for _, stop, _ in self.cells(state)), 1)
            self.assertIn(opened, words(state["log"]))

    def test_closing_the_lines_gives_the_cell_that_opened_them_the_stop_and_the_focus(self):
        steps = [{"press": ".mk[data-m]"}, {"focus": ".mk[data-m]"}, {"key": "ArrowRight"}]
        first, *_, closed = self.draw(self.answers(), *steps, {"key": "Escape"})
        (opened, _, _), (beside, _, _) = self.cells(first)[:2]
        self.assertEqual(self.cells(closed)[:2], [(opened, True, True), (beside, False, False)])
        self.assertEqual(sum(stop for _, stop, _ in self.cells(closed)), 1)
        self.assertIsNone(closed["log"])

    def test_a_press_elsewhere_leaves_the_stop_where_the_arrow_keys_took_it(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        steps = [{"press": ".mk[data-m]"}, {"focus": ".mk[data-m]"}, {"key": "ArrowRight"}]
        first, *_, moved, pressed = self.draw(self.answers(), *steps, REST)
        (opened, _, _), (beside, _, _) = self.cells(first)[:2]
        self.assertEqual(self.cells(moved)[:2], [(opened, False, False), (beside, True, True)])
        # The lines of the first cell are open still, and the button has the focus.
        self.assertIn(opened, words(pressed["log"]))
        at = {name: (stop, focused) for name, stop, focused in self.cells(pressed)}
        self.assertEqual((at[opened], at[beside]), ((False, False), (True, False)))
        self.assertEqual(sum(stop for stop, _ in at.values()), 1)

    def test_a_read_leaves_a_table_the_stop_it_had_while_the_focus_is_elsewhere(self):
        before = self.answers()
        self.post(11, "5ac1")
        steps = [{"focus": ".mk[data-m]"}, {"key": "ArrowRight"}, {"focus": "a.sub"}]
        first, *_, away, read = self.draw(before, *steps, {"answers": self.answers()})
        (_, _, _), (beside, _, _) = self.cells(first)[:2]
        self.assertEqual(len(rows(read)), len(rows(away)) + 1)
        for state in (away, read):
            self.assertEqual(
                [name for name, stop, _ in self.cells(state) if stop], [beside], len(rows(state))
            )
            self.assertEqual(
                [words(made) for made in within(state["report"]) if made.get("focused")],
                ["All conversations"],
            )

    def test_lines_that_were_asked_for_do_not_open_once_they_were_called_off(self):
        asks = [{"hold": "runs/"}, {"press": ".mk[data-m]"}]
        (closed, late), (away, later) = (
            self.draw(self.answers(), [*asks, off], {"come": True})[1:]
            for off in ({"key": "Escape"}, {"hash": "#"})
        )
        for state in (closed, away):
            self.assertEqual((state["asked"][0][:5], state["log"]), ("runs/", None))
        # Their answer comes to a page that asks for them no more.
        self.assertEqual((late["log"], later["log"]), (None, None))
        self.assertEqual(rows(closed)[0]["kids"][4]["kids"][0].get("focused"), True)

    def test_closing_the_lines_calls_off_the_lines_asked_of_another_mark(self):
        first, other = {"press": ".mk[data-m]"}, {"press": ".mk[data-m]", "nth": 1}
        steps = first, [{"hold": "runs/"}, other, {"key": "Escape"}], {"come": True}
        opened, closed, late = self.draw(self.answers(), *steps)[1:]
        self.assertIsNotNone(opened["log"])
        self.assertEqual(
            (closed["asked"][0][:5], closed["log"], late["log"]), ("runs/", None, None)
        )

    def test_lines_asked_of_a_post_do_not_open_once_the_newest_alone_are_drawn(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        asks = [{"hold": "runs/"}, {"press": ".mk[data-m]"}]
        whole, newest, late = self.draw(self.answers(), REST, [*asks, REST], {"come": True})[1:]
        self.assertEqual((len(rows(whole)), len(rows(newest))), (3, 2))
        self.assertEqual((newest["asked"][0][:5], late["log"]), ("runs/", None))

    def test_closing_the_lines_gives_the_title_the_focus_when_their_mark_is_drawn_no_more(self):
        before = self.answers()
        path = f"conversations/{CONVO}"
        report = json.loads(before[path]["text"])
        later = dict(report, messages=report["messages"][1:])
        after = {path: {"status": 200, "text": json.dumps(later)}}
        opened, read = self.draw(before, {"press": ".mk[data-m]"}, {"answers": after})[1:]
        self.assertIn(", frame 8f21 from ", words(opened["log"]))
        self.assertEqual((len(rows(read)), read["log"]), (len(rows(opened)) - 1, None))
        self.assertEqual(
            [made["tag"] for made in within(read["report"]) if made.get("focused")], ["h1"]
        )

    def other(self, answers: Dict[str, dict], text: Optional[str] = None) -> Dict[str, dict]:
        """The same answers with a conversation the list does not name: this one's report by
        default, under the other's id."""
        report = dict(json.loads(answers[f"conversations/{CONVO}"]["text"]), id="other")
        said = {"status": 200, "text": json.dumps(report) if text is None else text}
        return {**answers, "conversations/other": said}

    def test_every_post_is_forgotten_once_another_conversation_was_drawn(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        every = ["All 3 posts are drawn, here and on the timeline.", "Draw the newest 2 only"]
        newest = ["The newest 2 of 3 posts are drawn, here and on the timeline.", "Draw all 3"]
        there, back = {"hash": "#other"}, {"hash": f"#{CONVO}"}
        whole, elsewhere, again = self.draw(self.other(self.answers()), REST, there, back)[1:]
        drawn = [fold(state) for state in (whole, elsewhere, again)]
        self.assertEqual(drawn, [every, newest, newest])
        # A conversation the list does not name says nothing of when it was heard from.
        for state, said in ((whole, True), (elsewhere, False), (again, True)):
            (line,) = [made for made in within(state["report"]) if "data-heard" in made["attrs"]]
            self.assertEqual((bool(words(line)), bool(line["attrs"]["title"])), (said, said))

    def test_every_post_stays_asked_for_when_another_conversation_cannot_be_drawn(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 8))
        every = ["All 3 posts are drawn, here and on the timeline.", "Draw the newest 2 only"]
        newest = ["The newest 2 of 3 posts are drawn, here and on the timeline.", "Draw all 3"]
        there, back = {"hash": "#other"}, {"hash": f"#{CONVO}"}
        answers = self.other(self.answers(), "{}")
        whole, elsewhere, again, pressed = self.draw(answers, REST, there, back, REST)[1:]
        self.assertTrue(elsewhere["notice"].startswith("The answer at this address is not one"))
        self.assertEqual([fold(state) for state in (whole, elsewhere, again)], [every] * 3)
        self.assertEqual(fold(pressed), newest)


SIX = "saro raya kes tomo pax mira"
# The sign a count is drawn with, as a cell of the same thing is.
SIGNS = {"read": "\u2713", "unreadable": "\u2715"}
HEADINGS = {
    "one line": "The one line",
    "neither branch": "On neither branch",
    "not placed": "Not placed",
}


def crowd_logs(group: str = "") -> Dict[str, bytes]:
    """A group of six that split, as five of its members share their logs.

    Saro, Raya and Kes are at the tip of one branch and Tomo is an epoch behind them on it, Pax
    is on the other, and Mira shares no log. Everyone reads Saro's post from before the split.
    Raya's is read at her tip and is unreadable to Tomo and to Pax. Pax's is unreadable on the
    other branch, where Kes holds no record of it.
    """
    logs = {name: member(name, 2, SIX) for name in SIX.split()[:5]}
    for name in ("saro", "raya", "kes", "tomo"):
        logs[name].epoch(name, 3, "A3", SIX)
    logs["pax"].epoch("pax", 3, "B3", SIX)
    for name in ("saro", "raya", "kes"):
        logs[name].epoch(name, 4, "A4", SIX)
    logs["saro"].sent("saro", "8f21", 2)
    for name in ("raya", "kes", "tomo", "pax"):
        logs[name].received(name, "8f21", "saro", 2)
    logs["raya"].sent("raya", "e3b9", 4)
    for name in ("saro", "kes"):
        logs[name].received(name, "e3b9", "raya", 4)
    logs["tomo"].unreadable("tomo", "e3b9", 4, "epoch")
    logs["pax"].unreadable("pax", "e3b9", 4, "decrypt")
    logs["pax"].sent("pax", LONG, 3)
    for name in ("saro", "raya"):
        logs[name].unreadable(name, LONG, 3, "decrypt")
    logs["tomo"].unreadable("tomo", LONG, 3, "sender")
    return {name: logged(log, group) for name, log in logs.items()}


def torn_logs() -> Dict[str, Log]:
    """The group of six as two of its members write the states of both branches at an epoch.

    Raya writes the other branch's state before her own at the third epoch, and she and Kes do
    at the fourth. Tomo leaves at the fourth without a state there, and Mira shares no log.
    """
    logs = {name: member(name, 2, SIX) for name in SIX.split()[:5]}
    logs["raya"].epoch("raya", 3, "B3", SIX)
    for name in ("saro", "raya", "kes", "tomo"):
        logs[name].epoch(name, 3, "A3", SIX)
    for epoch in (3, 4):
        logs["pax"].epoch("pax", epoch, f"B{epoch}", SIX)
    for name in ("raya", "kes"):
        logs[name].epoch(name, 4, "B4", SIX)
    for name in ("saro", "raya", "kes"):
        logs[name].epoch(name, 4, "A4", SIX)
    logs["tomo"].left("tomo", 4)
    return logs


def keyed(made: dict, key: str) -> dict:
    """The one element in a document that keeps this name from one drawing to the next."""
    (found,) = [each for each in within(made) if each["attrs"].get("data-key") == key]
    return found


def lines(state: dict, part: str) -> List[str]:
    """The names of the rows the arrow keys move between in a part of the report."""
    under = within(keyed(state["report"], part))
    return [row["attrs"]["data-key"] for row in under if "data-row" in row["attrs"]]


def unfolding(made: dict) -> List[dict]:
    """The buttons in an element that open something and close it again."""
    return [each for each in within(made) if "data-unfolds" in each["attrs"]]


def open_in(made: dict) -> List[str]:
    """The names of what stands open in an element."""
    stands = [each["attrs"] for each in unfolding(made)]
    return [attrs["data-unfolds"] for attrs in stands if attrs["aria-expanded"] == "true"]


def focused(state: dict) -> dict:
    """The attributes of the one element of the report that holds the focus."""
    (held,) = [each for each in within(state["report"]) if each.get("focused")]
    return held["attrs"]


def held_open(state: dict) -> Tuple[str, str]:
    """What the button that holds the focus is named, and whether it says it is open."""
    return focused(state)["aria-label"], focused(state)["aria-expanded"]


def plain(made: dict) -> dict:
    """An element without what says where the focus and the Tab key are, and which mark it is."""
    moves = ("data-m", "tabindex")
    return {
        "tag": made["tag"],
        "attrs": {name: value for name, value in made["attrs"].items() if name not in moves},
        "kids": [kid if isinstance(kid, str) else plain(kid) for kid in made["kids"]],
    }


def name_of(fold: dict) -> str:
    """The heading and the position a fold is named by."""
    under = HEADINGS.get(fold["under"], f"Branch {fold['branch']}")
    return f"{under}, {fold['position']}"


def counted(count: int) -> str:
    return "1 installation" if count == 1 else f"{count} installations"


def reach(numbers: List[int]) -> str:
    low, high = min(numbers), max(numbers)
    return f"{low}" if low == high else f"{low} to {high}"


def lined(cell: dict) -> List[str]:
    """The words of each line a cell holds."""
    return [words(line) for line in cell["kids"]]


def spanned(row: dict) -> int:
    """How many columns the cells of a row span between them."""
    return sum(int(cell["attrs"].get("colspan", 1)) for cell in row["kids"])


def stop_in(state: dict, part: str) -> dict:
    """The attributes of the one cell of a part's table that the Tab key stops at."""
    moved = [made["attrs"] for made in within(keyed(state["report"], part))]
    moved = [attrs for attrs in moved if "data-m" in attrs or "data-unfolds" in attrs]
    (held,) = [attrs for attrs in moved if attrs.get("tabindex") == "0"]
    return held


def size(made: dict) -> int:
    """How many elements an element is, with all it holds."""
    return sum(1 for _ in within(made))


def utc(seconds: float) -> str:
    """A stamp as a browser writes one, to the millisecond."""
    at = datetime.fromtimestamp(seconds, timezone.utc)
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@unittest.skipUnless(NODE, "node runs the page's script, and is not on this machine")
class Folded(Collecting):
    """What the script draws of a conversation the collector says a page starts folded."""

    post = Drawn.post
    draw = Drawn.draw

    def setUp(self) -> None:
        super().setUp()
        # Three lanes are in sight, so the six fold. Each log is shared a minute after the last.
        self.enter(mock.patch("forks.rule.LANES_IN_SIGHT", 3))
        self.runs = {}
        for name, log in crowd_logs().items():
            self.runs[name] = self.served.share(log, name)
            self.clock.now += 60

    def answers(self) -> Dict[str, dict]:
        """What the collector answers each address the page asks, with the validator it carries."""
        report = self.served.report()
        marks = [at for post in report["messages"] for at in post["marks"].values()]
        marks += [step["at"] for seat in report["installations"] for step in seat["history"]]
        paths = ["conversations", f"conversations/{CONVO}"]
        paths += [f"runs/{at['run']}/log?line={at['line']}" for at in marks]
        said = {}
        for path in paths:
            answer = self.served.get(f"/{path}")
            said[path] = {"status": answer.status, "text": answer.body.decode()}
            said[path]["tag"] = "".join(carried(answer, "ETag"))
        return said

    def read(self, answers: Dict[str, dict]) -> Tuple[dict, List[str], Dict[str, dict]]:
        """The report in some answers, the names of its folds and its installations by id."""
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        seats = {seat["id"]: seat for seat in report["installations"]}
        return report, [name_of(fold) for fold in report["folds"]], seats

    def unfolded(self, answers: Dict[str, dict]) -> Dict[str, dict]:
        """The same answers from a collector that says the page does not start folded."""
        path = f"conversations/{CONVO}"
        report = dict(json.loads(answers[path]["text"]), folded=False)
        return {**answers, path: {"status": 200, "text": json.dumps(report), "tag": ""}}

    def shares(self, logs: Dict[str, Log]) -> None:
        """Another collector, to which each of these logs is shared a minute after the last."""
        self.served = self.elsewhere()
        self.runs = {}
        for name, log in logs.items():
            self.runs[name] = self.served.share(logged(log), name)
            self.clock.now += 60

    def test_a_folded_page_draws_a_row_for_each_fold_where_the_other_draws_a_lane_for_each(self):
        answers = self.answers()
        report, names, seats = self.read(answers)
        self.assertIs(report["folded"], True)
        self.assertEqual(
            [(fold["under"], fold["branch"], fold["position"]) for fold in report["folds"]],
            [
                ("branch", "A", "tip"),
                ("branch", "A", "behind"),
                ("branch", "B", "tip"),
                ("not placed", None, "no data"),
            ],
        )
        (state,) = self.draw(answers)
        (flat,) = self.draw(self.unfolded(answers))
        order = [seat for lane in report["lanes"] for seat in lane["installations"]]
        self.assertEqual(lines(state, "Installations"), [f"fold {name}" for name in names])
        self.assertEqual(lines(flat, "Installations"), [f"lane {seat}" for seat in order])
        self.assertEqual(lines(state, "Timeline"), [f"span {name}" for name in names])
        self.assertEqual(lines(flat, "Timeline"), [f"time {seat}" for seat in order])
        for drawn, columns in ((state, len(names)), (flat, len(order))):
            for row in rows(drawn):
                self.assertEqual(len(row["kids"]), 4 + columns)
        (table,) = classed(state["report"], "posts")
        over, titles = table["kids"][0]["kids"]
        self.assertEqual(
            [words(title) for title in titles["kids"]],
            [fold["position"] + counted(len(fold["installations"])) for fold in report["folds"]],
        )
        # A heading spans the folds under it, as it spans its installations on the other page.
        self.assertEqual(
            [(words(under), int(under["attrs"]["colspan"])) for under in over["kids"][4:]],
            [("Branch A", 2), ("Branch B", 1), ("Not placed", 1)],
        )
        # Nothing unfolds on a page that is not folded, and a fold is nowhere on it.
        self.assertEqual((unfolding(flat["report"]), classed(flat["report"], "fold")), ([], []))
        # A fold opens among the lanes and on the timeline, and each post has a count under it.
        # Four are at the tip, and of the rosters all but the one that is on the second branch
        # hold more than a tile names.
        self.assertEqual(
            Counter(made["attrs"]["class"] for made in unfolding(state["report"])),
            {"tog": 2 * len(names), "tally": 3 * len(names), "btn": 1 + 3},
        )

    def test_every_control_that_unfolds_is_a_button_that_says_whether_it_is_open(self):
        answers = self.answers()
        _, names, _ = self.read(answers)
        closed, opened = self.draw(answers, {"press": ".tog"})
        for state in (closed, opened):
            for made in unfolding(state["report"]):
                attrs = made["attrs"]
                with self.subTest(control=attrs["aria-label"]):
                    self.assertEqual((made["tag"], attrs["type"]), ("button", "button"))
                    self.assertIn(attrs["aria-expanded"], ("true", "false"))
                    self.assertTrue(attrs["data-unfolds"])
        # One press opens the fold among the lanes and on the timeline, and nothing else.
        self.assertEqual(open_in(closed["report"]), [])
        self.assertEqual(open_in(opened["report"]), names[:1] * 2)

    def test_a_fold_counts_at_each_epoch_the_installations_that_hold_a_record_there(self):
        # Saro goes on to a state more, which leaves Kes and Raya behind with Tomo.
        ahead = Log("saro", 12)
        ahead.epoch("saro", 5, "A5", SIX)
        self.served.put(*self.runs["saro"], logged(ahead))
        answers = self.answers()
        report, _, seats = self.read(answers)
        (state,) = self.draw(answers)
        runs = {run["id"]: run for run in report["runs"]}
        for fold in report["folds"]:
            name, under = name_of(fold), [seats[seat] for seat in fold["installations"]]
            of = counted(len(under))
            with self.subTest(fold=name):
                who, none, *cells = keyed(state["report"], f"fold {name}")["kids"]
                (button,) = unfolding(who)
                self.assertEqual(button["attrs"]["aria-label"], f"{name}: {of}")
                self.assertEqual((words(who), words(none)), (fold["position"] + of, ""))
                *epochs, over, where, heard = cells
                last = max(held["epoch"] for seat in under for held in seat["cells"])
                self.assertEqual(len(epochs), report["epochs"].index(last) + 1)
                for epoch, cell in zip(report["epochs"], epochs):
                    # What each installation of the fold holds at the epoch, and wrote there.
                    held = [
                        (each["kind"], [seat["history"][place] for place in each["steps"]])
                        for seat in under
                        for each in seat["cells"]
                        if each["epoch"] == epoch
                    ]
                    states = Counter(
                        state for _, wrote in held for state in {step["state"] for step in wrote}
                    )
                    kinds = Counter(kind for kind, _ in held)
                    expected = [(states[state], f", state {state}") for state in sorted(states)]
                    expected += [(kinds["unreached"], ", not reached")] * bool(kinds["unreached"])
                    expected += [(kinds["listed"], ", no log shared")] * bool(kinds["listed"])
                    self.assertEqual(len(cell["kids"]), len(expected))
                    for made, (count, ends) in zip(cell["kids"], expected):
                        label = made["attrs"]["aria-label"]
                        self.assertTrue(label.startswith(f"epoch {epoch}, {count} of {of}, "))
                        self.assertTrue(label.endswith(ends), label)
                        self.assertTrue(words(made).endswith(str(count)), words(made))
                at = [seat["epoch"] for seat in under if seat["epoch"] is not None]
                behind = [seat["behind_by"] for seat in under if seat["behind_by"] is not None]
                stands = [fold["position"]]
                stands += [f"epoch{'s' * (min(at) != max(at))} {reach(at)}"] if at else []
                stands += [] if at else [f"listed from epoch {under[0]['listed'][0]}"]
                stands += [f"{reach(behind)} behind"] if behind else []
                self.assertEqual(words(where), ", ".join(stands))
                stamps = sorted(runs[run]["heard"] for seat in under for run in seat["runs"])
                span = [f"{stamps[0]} to", stamps[-1]] if len(set(stamps)) > 1 else stamps[:1]
                self.assertEqual(lined(heard), span or ["no log held"])
                self.assertEqual(words(over), "")
        tip, behind = (keyed(state["report"], f"fold Branch A, {at}") for at in ("tip", "behind"))
        # Of the three behind, two reached the fourth epoch, and each is one or two behind.
        (fourth,) = report["folds"][1]["cells"][3]["states"]
        self.assertEqual(
            [made["attrs"]["aria-label"] for made in behind["kids"][5]["kids"]],
            [
                f"epoch 4, 2 of 3 installations, branch A, state {fourth['state']}",
                "epoch 4, 1 of 3 installations, not reached",
            ],
        )
        self.assertEqual(words(behind["kids"][-2]), "behind, epochs 3 to 4, 1 to 2 behind")
        # Saro was heard from last, and Raya, Kes and Tomo a minute apart before.
        self.assertEqual(
            (lined(tip["kids"][-1]), lined(behind["kids"][-1])),
            (["2026-10-09T12:05:00Z"], ["2026-10-09T12:01:00Z to", "2026-10-09T12:03:00Z"]),
        )

    def test_a_fold_says_how_many_of_its_runs_are_final_and_of_how_many_it_holds_no_log(self):
        # Both branches go on without Mira and Tomo. Mira shares two logs that hold none of the
        # group, Raya a run more that is final, and Tomo's run ends in a leave.
        for name, epoch, state in (("saro", 5, "A5"), ("pax", 4, "B4")):
            ahead = Log(name, 12)
            ahead.epoch(name, epoch, state, "saro raya kes pax")
            self.served.put(*self.runs[name], logged(ahead))
        for name, hour, final in (("mira", 12, False), ("mira", 13, False), ("raya", 12, True)):
            alone = Log(name, hour)
            alone.start(name)
            self.runs[f"{name}, alone at {hour}"] = self.served.share(logged(alone), name, final)
        leave = Log("tomo", 12)
        leave.left("tomo", 4)
        self.served.put(*self.runs.pop("tomo"), logged(leave), final=True)
        # Every run but Tomo's is heard from on the day before his log is deleted.
        self.clock.pass_days(29)
        for run in self.runs.values():
            self.served.put(*run)
        self.clock.pass_days(2)
        (state,) = self.draw(self.answers())
        behind, removed = (
            keyed(state["report"], f"fold {name}")
            for name in ("Branch A, behind", "Not placed, removed")
        )
        self.assertEqual(lined(behind["kids"][-1]), ["2026-11-07T12:05:00Z", "1 of 3 runs final"])
        self.assertEqual(
            [words(cell) for cell in removed["kids"][-3:-1]],
            ["removed, the log is gone of 1", "removed, epoch 4, listed from epoch 1"],
        )
        # No log is held of Tomo, the one installation of two: Mira's two runs are one log more.
        self.assertEqual(lined(removed["kids"][-1]), ["2026-11-07T12:05:00Z", "no log held of 1"])

    def test_a_fold_counts_an_installation_under_each_state_and_leave_it_wrote_at_an_epoch(self):
        self.shares(torn_logs())
        answers = self.answers()
        report, _, seats = self.read(answers)
        (state,) = self.draw(answers)
        # How a state is said and lettered: one of a branch by its letter, the others by none.
        looks = {
            (held["epoch"], held["state"]): (f"branch {held['branch']}", held["branch"])
            if held["lineage"] == "branch"
            else ("shared line", "")
            for held in report["states"]
        }
        self.assertEqual({held["lineage"] for held in report["states"]}, {"shared", "branch"})
        for fold in report["folds"]:
            name, under = name_of(fold), [seats[seat] for seat in fold["installations"]]
            of = counted(len(under))
            row = keyed(state["report"], f"fold {name}")
            for epoch, cell in zip(report["epochs"], row["kids"][2:-3]):
                at = [held for seat in under for held in seat["cells"] if held["epoch"] == epoch]
                # The states each installation wrote at the epoch, and None for a leave.
                wrote = [
                    {seat["history"][place]["state"] for place in held["steps"]}
                    for seat in under
                    for held in seat["cells"]
                    if held["epoch"] == epoch and held["steps"]
                ]
                states = Counter(state for each in wrote for state in each if state is not None)
                left = sum(None in each for each in wrote)
                kinds = Counter(held["kind"] for held in at)
                expected = [
                    (f"{looks[epoch, each][0]}, state {each}", looks[epoch, each][1], states[each])
                    for each in sorted(states)
                ]
                expected += [("left", "", left)] * bool(left)
                expected += [("not reached", "", kinds["unreached"])] * bool(kinds["unreached"])
                expected += [("no log shared", "", kinds["listed"])] * bool(kinds["listed"])
                with self.subTest(fold=name, epoch=epoch):
                    self.assertEqual(len(cell["kids"]), len(expected))
                    for made, (word, letter, count) in zip(cell["kids"], expected):
                        label = f"epoch {epoch}, {count} of {of}, {word}"
                        attrs = made["attrs"]
                        self.assertEqual(
                            (attrs["aria-label"], attrs["title"], attrs["role"]),
                            (label, label, "img"),
                        )
                        self.assertEqual(words(made), f"{letter}{count}")
        tip, removed = (
            keyed(state["report"], f"fold {name}")
            for name in ("Branch A, tip", "On neither branch, removed")
        )
        # Raya is counted under both states of the third epoch, and she and Kes of the fourth.
        self.assertEqual(
            [
                sorted(made["attrs"]["aria-label"].split(", ")[1] for made in cell["kids"])
                for cell in tip["kids"][4:6]
            ],
            [
                ["1 of 3 installations", "3 of 3 installations"],
                ["2 of 3 installations", "3 of 3 installations"],
            ],
        )
        twice = "wrote more than one state, or a state and a leave, and"
        self.assertEqual(
            [words(more) for more in classed(tip["kids"][-2], "more")],
            [
                f"at epoch 3, 1 of them {twice} is counted under each",
                f"at epoch 4, 2 of them {twice} are counted under each",
            ],
        )
        # Tomo's leave is all that his row holds at the fourth epoch.
        (leave,) = removed["kids"][5]["kids"]
        self.assertEqual(leave["attrs"]["aria-label"], "epoch 4, 1 of 1 installation, left")
        self.assertEqual(classed(removed["kids"][-2], "more"), [])

    def test_a_fold_says_whose_log_is_gone_and_who_shared_none(self):
        # Both branches go on without Kes and Tomo, who leave. Their runs are final, and Pax's.
        for name, epoch, state in (("saro", 5, "A5"), ("raya", 5, "A5"), ("pax", 4, "B4")):
            ahead = Log(name, 12)
            ahead.epoch(name, epoch, state, "saro raya pax mira")
            self.served.put(*self.runs[name], logged(ahead), final=name == "pax")
        for name, epoch in (("tomo", 4), ("kes", 5)):
            leave = Log(name, 12)
            leave.left(name, epoch)
            self.served.put(*self.runs[name], logged(leave), final=True)
        (held,) = self.draw(self.answers())
        ended = keyed(held["report"], "fold Branch B, ended")
        self.assertEqual(words(ended["kids"][-3]), "ended")
        self.assertEqual(lined(ended["kids"][-1]), ["2026-10-09T12:05:00Z", "1 of 1 run final"])
        # Saro and Raya are heard from on the day before the three final logs are deleted, and
        # after it their rosters list Mira no more.
        self.clock.pass_days(29)
        for name in ("saro", "raya"):
            self.served.put(*self.runs[name])
        self.clock.pass_days(2)
        gone = self.answers()
        for name in ("saro", "raya"):
            ahead = Log(name, 13)
            ahead.epoch(name, 6, "A6", "saro raya pax")
            self.served.put(*self.runs[name], logged(ahead))
        mixed = self.answers()

        def said(state: dict, name: str) -> List[str]:
            """What a fold says of its logs: at the end of its row, as its runs, on its line."""
            lane, line = (keyed(state["report"], f"{row} {name}") for row in ("fold", "span"))
            (on_line,) = classed(line, "said")
            return [words(lane["kids"][-3]), *lined(lane["kids"][-1]), words(on_line)]

        report, names, _ = self.read(gone)
        self.assertEqual(
            [(name, fold["kept"], fold["logs"]) for name, fold in zip(names, report["folds"])][1:],
            [
                ("Not placed, ended", 1, 0),
                ("Not placed, removed", 2, 0),
                ("Not placed, no data", 0, 0),
            ],
        )
        (state,) = self.draw(gone)
        self.assertEqual(
            [said(state, name) for name in names[1:]],
            [
                ["ended, its log is gone", "no log held", "its log is gone"],
                ["removed, the log is gone of 2", "no log held", "their logs are gone"],
                ["", "no log held", "no log shared"],
            ],
        )
        report, names, seats = self.read(mixed)
        fold = report["folds"][2]
        self.assertEqual(
            (names[2], len(fold["installations"]), fold["kept"], fold["logs"]),
            ("Not placed, removed", 3, 2, 0),
        )
        state, opened = self.draw(mixed, {"press": ".tog", "nth": 2})
        self.assertEqual(
            said(state, names[2]),
            [
                "removed, the log is gone of 2",
                "no log held",
                "the log is gone of 2, no log shared of 1",
            ],
        )
        # Opened, the line of each says the same of its own log.
        lines_of = [keyed(opened["report"], f"time {seat}") for seat in fold["installations"]]
        self.assertEqual(
            [words(line["kids"][1]) for line in lines_of],
            [
                "its log is gone" if seats[seat]["kept"] else "no log shared"
                for seat in fold["installations"]
            ],
        )
        self.assertEqual(sum(seats[seat]["kept"] for seat in fold["installations"]), 2)

    def test_a_fold_that_holds_the_log_of_some_counts_the_others_and_draws_its_line(self):
        # Tomo's run is deleted, both branches go on without him and Mira, and Mira shares a log
        # that holds none of the group.
        self.served.delete(*self.runs.pop("tomo"))
        for name, epoch, state in (("saro", 5, "A5"), ("pax", 4, "B4")):
            ahead = Log(name, 12)
            ahead.epoch(name, epoch, state, "saro raya kes pax")
            self.served.put(*self.runs[name], logged(ahead))
        alone = Log("mira", 12)
        alone.start("mira")
        self.served.share(logged(alone), "mira")
        answers = self.answers()
        report, names, _ = self.read(answers)
        fold = report["folds"][-1]
        self.assertEqual(
            (names[-1], len(fold["installations"]), fold["kept"], fold["logs"]),
            ("Not placed, removed", 2, 0, 1),
        )
        (state,) = self.draw(answers)
        lane, line = (keyed(state["report"], f"{row} {names[-1]}") for row in ("fold", "span"))
        self.assertEqual(words(lane["kids"][-3]), "removed")
        self.assertEqual(lined(lane["kids"][-1]), ["2026-10-09T12:05:00Z", "no log held of 1"])
        self.assertEqual((classed(line, "hatched"), len(classed(line, "plot"))), ([], 1))

    def test_a_fold_that_holds_no_cell_is_its_words_from_where_a_roster_first_lists_it(self):
        # Mira is listed from the first epoch and Tomo from the second, and each shares a log
        # that holds none of the group.
        logs = {name: Log(name) for name in ("saro", "raya", "mira", "tomo")}
        for name, log in logs.items():
            log.start(name)
        for name in ("saro", "raya"):
            logs[name].epoch(name, 1, "X1", "saro raya mira")
            logs[name].epoch(name, 2, "X2", "saro raya mira tomo")
        self.shares(logs)
        answers = self.answers()
        report, names, _ = self.read(answers)
        fold = report["folds"][-1]
        self.assertEqual(
            (names[-1], len(fold["installations"]), fold["cells"], fold["listed_from"]),
            ("Not placed, never joined", 2, [], {"low": 1, "high": 2}),
        )
        (state,) = self.draw(answers)
        who, none, over, where, heard = keyed(state["report"], f"fold {names[-1]}")["kids"]
        self.assertEqual(
            (words(who), words(none), words(over), words(where)),
            (
                "never joined2 installations",
                "",
                "never joined",
                "never joined, listed from epochs 1 to 2",
            ),
        )
        # Its words start under the first epoch, behind the mark of where it is.
        self.assertEqual(int(over["attrs"]["colspan"]), len(report["epochs"]) + 1)
        self.assertEqual(len(classed(over, "endmark")), 1)
        self.assertEqual(lined(heard), ["2026-10-09T12:07:00Z to", "2026-10-09T12:08:00Z"])

    def test_every_row_of_a_table_spans_the_columns_of_its_head(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        # A fold is open among the lanes, and a count under the first post.
        *_, opened = self.draw(answers, {"press": ".tog"}, {"press": ".tally"})
        (lanes,) = classed(opened["report"], "lanes")
        (titles,), body = (part["kids"] for part in lanes["kids"])
        self.assertEqual(len(titles["kids"]), 4 + len(report["epochs"]) + 3)
        self.assertEqual(len(body), len(report["lanes"]) + len(names) + 3)
        for row in body:
            self.assertEqual(spanned(row), len(titles["kids"]), row["attrs"]["data-key"])
        # A fold has no account, name or sender: its counts start under the first epoch.
        for name in names:
            who, none, *_ = keyed(opened["report"], f"fold {name}")["kids"]
            self.assertEqual((who["tag"], int(none["attrs"]["colspan"])), ("th", 3))
        (posts,) = classed(opened["report"], "posts")
        (over, titles), body = (part["kids"] for part in posts["kids"])
        self.assertEqual((spanned(over), len(titles["kids"])), (4 + len(names), len(names)))
        self.assertEqual(keys(opened), ["post 8f21", "who 8f21", f"post {LONG}", "post e3b9"])
        self.assertEqual([spanned(row) for row in body], [4 + len(names)] * 4)

    def test_a_fold_opens_to_the_lane_of_each_of_its_installations_and_closes_again(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        closed, opened, again = self.draw(answers, {"press": ".tog"}, {"press": ".tog"})
        (flat,) = self.draw(self.unfolded(answers))
        under = report["folds"][0]["installations"]
        for part, fold, lane in (("Installations", "fold", "lane"), ("Timeline", "span", "time")):
            with self.subTest(part=part):
                folds = [f"{fold} {name}" for name in names]
                self.assertEqual(lines(closed, part), folds)
                lanes = [f"{lane} {seat}" for seat in under]
                self.assertEqual(lines(opened, part), [folds[0], *lanes, *folds[1:]])
                self.assertEqual(lines(again, part), folds)
                # An opened lane is the lane of the page that is not folded, node for node.
                for seat in under:
                    self.assertEqual(
                        plain(keyed(opened["report"], f"{lane} {seat}")),
                        plain(keyed(flat["report"], f"{lane} {seat}")),
                    )
        # One press opens the fold among the lanes and on the timeline, and the focus stays.
        label = f"{names[0]}: 3 installations"
        self.assertEqual((held_open(opened), held_open(again)), ((label, "true"), (label, "false")))
        self.assertEqual(open_in(opened["report"]), names[:1] * 2)
        self.assertEqual(plain(again["report"]), plain(closed["report"]))
        # Pressed on the timeline, the fold opens in both, and the focus stays on the timeline.
        _, there = self.draw(answers, {"press": ".tog", "nth": len(names)})
        self.assertEqual(open_in(there["report"]), names[:1] * 2)
        (held,) = [made for made in within(keyed(there["report"], "Timeline")) if "focused" in made]
        self.assertEqual(held["attrs"]["data-unfolds"], names[0])

    def test_a_folds_line_reaches_from_its_first_installation_at_a_state_to_its_last(self):
        late = Log("kes", 12)
        late.epoch("kes", 4, "A4", SIX)
        self.served.put(*self.runs["kes"], logged(late))
        answers = self.answers()
        report, _, seats = self.read(answers)
        (drawn,) = self.draw(answers)
        start, end = report["timeline"]["start"], report["timeline"]["end"]
        for fold in report["folds"]:
            name, under = name_of(fold), [seats[seat] for seat in fold["installations"]]
            with self.subTest(fold=name):
                row = keyed(drawn["report"], f"span {name}")
                wrote = [
                    (step["epoch"], step["state"], seat["id"], step["at"]["seconds"])
                    for seat in under
                    for step in seat["history"]
                    if step["kind"] == "epoch"
                ]
                bands = [made for made in within(row) if made["tag"] == "g"]
                held = sorted({(epoch, state) for epoch, state, _, _ in wrote})
                self.assertEqual(len(bands), len(held))
                for band, (epoch, state) in zip(bands, held):
                    stamps = [at for *place, _, at in wrote if place == [epoch, state]]
                    holders = {seat for *place, seat, _ in wrote if place == [epoch, state]}
                    label = band["attrs"]["aria-label"]
                    self.assertTrue(label.startswith(f"{name}, epoch {epoch}, "), label)
                    ends = f", {len(holders)} of {counted(len(under))}, "
                    ends += f"from {utc(min(stamps))} to {utc(max(stamps))}"
                    self.assertTrue(label.endswith(ends), label)
                    (band_line,) = [made for made in band["kids"][1:] if made["tag"] == "line"]
                    for edge, stamp in (("x1", min(stamps)), ("x2", max(stamps))):
                        across = float(band_line["attrs"][edge].rstrip("%"))
                        self.assertAlmostEqual(across, (stamp - start) / (end - start) * 100, 3)
                # Of the posts, a fold's line marks those its installations sent, and no other.
                sent = [
                    f"{seat}, frame {post['frame']} from {seat}, sent, at {post['at']['time']}"
                    for seat in fold["installations"]
                    for post in report["messages"]
                    if post["sender"] == seat
                ]
                marks = [made["attrs"] for made in classed(row, "post")]
                self.assertEqual([mark["aria-label"] for mark in marks], sent)
                self.assertEqual({mark["class"] for mark in marks} - {"post sent"}, set())
                self.assertEqual(classed(row, "on") + classed(row, "off"), [])
                self.assertEqual("no log shared" in words(row), not fold["logs"])
        # Kes wrote the newest state again two hours after Raya and Saro did.
        (newest,) = [
            made["attrs"]["aria-label"]
            for made in within(keyed(drawn["report"], "span Branch A, tip"))
            if made["tag"] == "g" and ", epoch 4, " in made["attrs"]["aria-label"]
        ]
        self.assertTrue(
            newest.endswith(
                "3 of 3 installations, from 2026-10-08T10:00:05.000Z to 2026-10-08T12:00:01.000Z"
            ),
            newest,
        )
        # The timeline says what a line of several leaves out, and only where one is drawn.
        (noted,) = classed(keyed(drawn["report"], "Timeline"), "quiet")
        self.assertIn(" It leaves out the rest of what a line for each of them draws", words(noted))
        (flat,) = self.draw(self.unfolded(answers))
        self.assertEqual(classed(keyed(flat["report"], "Timeline"), "quiet"), [])

    def test_a_band_says_how_many_of_its_fold_reached_its_state_between_its_two_ticks(self):
        self.shares(torn_logs())
        answers = self.answers()
        report, names, _ = self.read(answers)
        (drawn,) = self.draw(answers)
        start, end = report["timeline"]["start"], report["timeline"]["end"]
        fold = report["folds"][0]
        held = [(cell["epoch"], each) for cell in fold["cells"] for each in cell["states"]]
        # Of the three at the tip, one reached the other branch's state of the third epoch.
        self.assertEqual((names[0], len(fold["installations"])), ("Branch A, tip", 3))
        self.assertEqual(
            [sorted(each["count"] for each in cell["states"]) for cell in fold["cells"]],
            [[3], [3], [1, 3], [2, 3]],
        )
        self.assertTrue(any(each["first"] != each["last"] for _, each in held))
        row = keyed(drawn["report"], f"span {names[0]}")
        bands = [made for made in within(row) if made["tag"] == "g"]
        self.assertEqual(len(bands), len(held))
        for band, (epoch, each) in zip(bands, held):
            with self.subTest(epoch=epoch, state=each["state"]):
                title, _, first, last, *letter = band["kids"]
                label = band["attrs"]["aria-label"]
                self.assertEqual((band["attrs"]["role"], words(title)), ("img", label))
                self.assertTrue(label.startswith(f"{names[0]}, epoch {epoch}, "), label)
                ends = f", {each['count']} of 3 installations, "
                ends += f"from {utc(each['first'])} to {utc(each['last'])}"
                self.assertTrue(label.endswith(ends), label)
                for tick, stamp in ((first, each["first"]), (last, each["last"])):
                    across = float(tick["attrs"]["x"].rstrip("%"))
                    self.assertEqual(tick["tag"], "rect")
                    self.assertAlmostEqual(across, (stamp - start) / (end - start) * 100, 3)
                # Off the shared line a band carries the letter of its branch.
                branch = re.search(r", branch (\w), ", label)
                self.assertEqual([words(made) for made in letter], [branch[1]] if branch else [])
                self.assertEqual(branch is None, ", shared line, " in label)

    def test_an_open_fold_leaves_the_posts_of_its_installations_to_their_own_lines(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        closed, opened = self.draw(answers, {"press": ".tog"})
        under = report["folds"][0]["installations"]
        sent = [post for post in report["messages"] if post["sender"] in under]
        self.assertEqual(len(sent), 2)
        for state, marks in ((closed, 2), (opened, 0)):
            line = keyed(state["report"], f"span {names[0]}")
            self.assertEqual(len(classed(line, "post")), marks)
        # A record has one mark on the timeline, whichever line draws it.
        for state in (closed, opened):
            said = [
                made["attrs"]["aria-label"]
                for made in within(keyed(state["report"], "Timeline"))
                if "data-m" in made["attrs"]
            ]
            self.assertEqual(len(said), len(set(said)))
            for post in sent:
                of = f"{post['sender']}, frame {post['frame']} from {post['sender']}, sent, at "
                self.assertEqual(len([each for each in said if each.startswith(of)]), 1)

    def test_a_cell_of_a_post_counts_the_installations_of_a_fold_by_what_became_of_it(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        (state,) = self.draw(answers)
        self.assertEqual(keys(state), [f"post {post['frame']}" for post in report["messages"]])
        seen = set()
        for row, post in zip(rows(state), report["messages"]):
            for fold, fates, cell in zip(report["folds"], post["folds"], row["kids"][4:]):
                name = name_of(fold)
                with self.subTest(frame=post["frame"], fold=name):
                    (button,) = cell["kids"]
                    became = Counter(post["cells"][seat] for seat in fold["installations"])
                    causes = Counter(
                        post["why"][seat] for seat in fold["installations"] if seat in post["why"]
                    )
                    self.assertEqual(sorted(fates["cells"]), sorted(became))
                    why = [f"{causes[cause]} {cause}" for cause in sorted(causes)]
                    said = [
                        f"{became[fate]} {fate}" + (f" ({', '.join(why)})" if why else "")
                        if fate == "unreadable"
                        else f"{became[fate]} {fate}"
                        for fate in fates["cells"]
                    ]
                    of = f"frame {post['frame']} from {post['sender']}, {name}"
                    attrs = button["attrs"]
                    self.assertEqual(attrs["aria-label"], f"{of}: {', '.join(said)}")
                    self.assertEqual(
                        (button["tag"], attrs["class"], attrs["aria-expanded"]),
                        ("button", "tally", "false"),
                    )
                    self.assertEqual(len(button["kids"]), len(became))
                    for fate, made in zip(fates["cells"], button["kids"]):
                        cls, word = DRAWN[fate]
                        self.assertEqual(made["attrs"]["class"], cls if word else "mk")
                        drawn = f"{SIGNS.get(fate, '')}{became[fate]} {fate}"
                        self.assertEqual(
                            words(made), drawn + ("".join(why) if fate == "unreadable" else "")
                        )
                        seen.add(fate)
        self.assertEqual(seen, set(DRAWN) - {"not a member"})

    def test_a_count_opens_the_installations_it_stands_for_each_with_its_record(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        # Who could not read Pax's post at the tip of the other branch: Raya and Saro.
        post, fold = report["messages"][1], report["folds"][0]
        fates = post["folds"][0]
        self.assertEqual(fates, {"cells": {"unreadable": 2, "no record": 1}, "why": {"decrypt": 2}})
        count = {"press": ".tally", "nth": len(names)}
        mark = {"press": ".mk[data-m]", "nth": 1}
        closed, opened, read, shut = self.draw(answers, count, mark, count)
        self.assertEqual(keys(opened), ["post 8f21", f"post {LONG}", f"who {LONG}", "post e3b9"])
        self.assertEqual(open_in(opened["report"]), [focused(opened)["data-unfolds"]])
        ((under,),) = [each["kids"] for each in keyed(opened["report"], f"who {LONG}")["kids"]]
        title, *parts = under["kids"]
        self.assertEqual(words(title), f"{names[0]}, 3 installations")
        said = []
        for fate, chip, listed in zip(fates["cells"], parts[::2], parts[1::2]):
            self.assertEqual(words(chip), f"{SIGNS.get(fate, '')}{fates['cells'][fate]} {fate}")
            for item in listed["kids"]:
                (made,) = item["kids"]
                said.append((words(made), made["attrs"]["role"], made["attrs"]["aria-label"]))
        of = f"frame {LONG} from {post['sender']}"
        # Each installation of the fold once, under what became of the post on it.
        expected = [
            (
                seat + post["why"].get(seat, ""),
                "button" if seat in post["marks"] else "img",
                ", ".join(filter(None, [seat, of, fate, post["why"].get(seat)])),
            )
            for fate in fates["cells"]
            for seat in fold["installations"]
            if post["cells"][seat] == fate
        ]
        self.assertEqual(said, expected)
        self.assertEqual([role for _, role, _ in said], ["button", "button", "img"])
        # The second of them opens the lines around its own record of the post.
        second = [seat for seat in fold["installations"] if post["cells"][seat] == "unreadable"][1]
        at = post["marks"][second]
        self.assertEqual(read["asked"], [f"runs/{at['run']}/log?line={at['line']}"])
        self.assertIn(f"line {at['line']}{second}, {of}, unreadable, decrypt", words(read["log"]))
        self.assertEqual((keys(shut), shut["log"]), (keys(closed), None))

    def test_what_a_reader_opened_stays_open_while_the_page_reads_on(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 12))
        before = self.answers()
        _, names, _ = self.read(before)
        self.post(11, "5ac1")
        self.post(12, "9d07")
        after = {"answers": self.answers()}
        count, lane, record = {"press": ".tally"}, {"press": ".tog"}, {"press": ".mk[data-m]"}
        newest = "The newest 2 of 5 posts are drawn, here and on the timeline"
        kept = [f"post {LONG}", f"who {LONG}", "post e3b9", "post 5ac1", "post 9d07"]
        # The focus goes on to a fold among the lanes, or to the lines of a record, or stays.
        for then in (lane, record, None):
            with self.subTest(then=then):
                steps = [count, then, after] if then else [count, after]
                first, *_, opened, read = self.draw(before, *steps)
                self.assertEqual(keys(first), [f"post {LONG}", "post e3b9"])
                self.assertEqual(keys(opened), [f"post {LONG}", f"who {LONG}", "post e3b9"])
                self.assertEqual(keys(read), kept)
                self.assertEqual(
                    fold(read)[0],
                    f"{newest}, and the older ones back to the one that is open or has the focus.",
                )
                messages = keyed(read["report"], "Messages")
                self.assertEqual(open_in(messages), [f"{LONG} under {names[0]}"])
                self.assertEqual(
                    plain(keyed(read["report"], f"who {LONG}")),
                    plain(keyed(opened["report"], f"who {LONG}")),
                )
                self.assertEqual(lines(read, "Installations"), lines(opened, "Installations"))
                self.assertEqual(len(lines(read, "Installations")), 7 if then == lane else 4)
                if then == record:
                    self.assertIn(f", frame {LONG} from ", words(opened["log"]))
                    self.assertEqual(words(read["log"]), words(opened["log"]))
                else:
                    self.assertEqual((read["log"], opened["log"]), (None, None))
                    self.assertEqual(held_open(read), held_open(opened))
        # A press of the reader's own for the newest alone keeps none of it, and what is drawn
        # no more is open no more when it is drawn again.
        *_, whole, folded, again = self.draw(before, count, after, REST, REST, REST)
        self.assertEqual(keys(whole), ["post 8f21", *kept])
        self.assertEqual(keys(folded), ["post 5ac1", "post 9d07"])
        self.assertEqual(keys(again), [key for key in keys(whole) if key != f"who {LONG}"])
        self.assertEqual(open_in(again["report"]), [])

    def test_the_arrow_keys_move_between_the_folds_and_their_lanes_and_two_keys_press_a_fold(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        tip, behind = (fold["installations"] for fold in report["folds"][:2])
        typed = [{"key": key} for key in ("ArrowDown", "ArrowUp", "Enter", "ArrowDown", " ", "End")]
        states = self.draw(answers, {"press": ".tog"}, *typed)[1:]
        opened, down, up, enter, onward, space, end = states
        label, other = f"{names[0]}: 3 installations", f"{names[1]}: 1 installation"
        self.assertEqual(held_open(opened), (label, "true"))
        self.assertTrue(focused(down)["aria-label"].startswith(f"{tip[0]}, epoch 1, "))
        self.assertEqual(focused(down)["role"], "button")
        self.assertEqual(held_open(up), (label, "true"))
        # Enter closes the fold, and the focus stays on its button.
        self.assertEqual(held_open(enter), (label, "false"))
        self.assertEqual(lines(enter, "Installations"), [f"fold {name}" for name in names])
        self.assertEqual(held_open(onward), (other, "false"))
        self.assertEqual(held_open(space), (other, "true"))
        self.assertEqual(lines(space, "Installations")[2], f"lane {behind[0]}")
        self.assertEqual(held_open(end), (other, "true"))
        # Of what the arrow keys move between in a table, the Tab key stops at the one held.
        for state in states:
            for part in ("Installations", "Timeline", "Messages"):
                stops = [
                    made["attrs"].get("tabindex", "none" if "data-m" in made["attrs"] else "")
                    for made in within(keyed(state["report"], part))
                    if "data-m" in made["attrs"] or "data-unfolds" in made["attrs"]
                ]
                self.assertEqual(Counter(stops)["0"], 1, part)
                self.assertEqual(set(stops) - {"0", "-1", "none"}, set(), part)
        # A button outside a table is a stop of the Tab key as any button is.
        apart = [made["attrs"] for made in classed(opened["report"], "btn")]
        self.assertEqual([attrs.get("tabindex") for attrs in apart], [None] * 4)

    def test_the_arrow_keys_move_between_the_counts_of_a_post_and_the_installations_under_it(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        typed = ("ArrowRight", "Enter", "ArrowDown", "ArrowRight", "ArrowUp")
        typed += ("ArrowDown", "ArrowDown", "ArrowUp")
        states = self.draw(answers, {"press": ".tally"}, *({"key": key} for key in typed))[1:]
        opened, right, enter, down, along, up, into, below, back = states
        first, second = (f"8f21 under {name}" for name in names[:2])
        held = [focused(state) for state in (opened, right, enter)]
        self.assertEqual(
            [(attrs["data-unfolds"], attrs["aria-expanded"]) for attrs in held],
            [(first, "true"), (second, "false"), (second, "true")],
        )
        # Under the post stand the installations of both counts, as they are read.
        under = [seat for fold in report["folds"][:2] for seat in fold["installations"]]
        marked = [
            made["attrs"]["aria-label"].split(",")[0]
            for made in within(keyed(enter["report"], "who 8f21"))
            if "data-m" in made["attrs"]
        ]
        self.assertEqual(sorted(marked), sorted(under))
        self.assertEqual(focused(down)["aria-label"].split(",")[0], marked[0])
        self.assertEqual(focused(along)["aria-label"].split(",")[0], marked[1])
        self.assertEqual((focused(up)["class"], focused(up)["data-unfolds"]), ("tally", first))
        # The installations are entered at the first of them from the post, and at the last of
        # them from the post below.
        self.assertEqual(focused(into)["aria-label"].split(",")[0], marked[0])
        self.assertEqual(
            (focused(below)["class"], focused(below)["data-unfolds"]),
            ("tally", f"{LONG} under {names[0]}"),
        )
        self.assertEqual(focused(back)["aria-label"].split(",")[0], marked[-1])

    def test_a_count_of_more_than_a_tile_names_gives_the_names_when_it_is_asked(self):
        answers = self.answers()
        report, _, _ = self.read(answers)
        closed, opened, again = self.draw(answers, {"press": ".btn"}, {"press": ".btn"})
        (flat,) = self.draw(self.unfolded(answers))
        tip, behind = report["positions"][:2]
        self.assertEqual((tip["position"], tip["count"], behind["count"]), ("tip", 4, 1))

        def tile(state: dict, position: dict) -> List[str]:
            tiles = keyed(state["report"], "Tiles")["kids"]
            (made,) = [each for each in tiles if words(each["kids"][0]) == position["position"]]
            return [words(kid) for kid in made["kids"][3:]]

        names = ", ".join(tip["installations"])
        for state, under in ((closed, []), (opened, [f"tip {names}"]), (again, [])):
            self.assertEqual(tile(state, tip), ["Names of all 4"])
            self.assertEqual([words(made) for made in classed(state["report"], "named")], under)
        held = focused(opened)
        self.assertEqual(
            (held["aria-label"], held["aria-expanded"]), ("Names of all 4 that are tip", "true")
        )
        self.assertEqual(focused(again)["aria-expanded"], "false")
        # A tile of no more than it names is as on a page that is not folded.
        for state in (closed, opened, flat):
            self.assertEqual(tile(state, behind), [", ".join(behind["installations"])])
        self.assertEqual(tile(flat, tip), [])

    def test_a_roster_of_more_than_a_tile_names_is_a_count_and_its_chips_when_they_are_asked(self):
        answers = self.answers()
        report, _, _ = self.read(answers)
        (flat,) = self.draw(self.unfolded(answers))
        closed, opened = self.draw(answers, {"press": ".btn", "nth": 1})
        a, b = report["branches"]
        self.assertEqual((len(a["installations"]), len(b["installations"])), (4, 1))
        card = keyed(closed["report"], "state A")
        counts = classed(card, "count")
        self.assertEqual(
            [words(made) for made in counts],
            ["4 installationsNames of all 4", "6 installationsNames of all 6"],
        )
        self.assertEqual(
            [made["attrs"]["aria-label"] for made in unfolding(card)],
            ["Names of all 4 on branch A", "Names of all 6 in the roster of branch A"],
        )
        self.assertEqual(classed(card, "roster"), [])
        # Asked, the chips of those on the branch are the ones a page that is not folded draws.
        (asked,) = classed(keyed(opened["report"], "state A"), "roster")
        on_it, _ = classed(keyed(flat["report"], "state A"), "roster")
        self.assertEqual((asked, len(asked["kids"])), (on_it, 4))
        self.assertEqual(held_open(opened), ("Names of all 4 on branch A", "true"))
        # The one installation on the other branch is named as it stands.
        for state in (closed, flat):
            on_it, *_ = classed(keyed(state["report"], "state B"), "roster")
            (chip,) = on_it["kids"]
            self.assertEqual(words(chip), f"{b['installations'][0]}tip, on B")

    def test_a_post_stays_drawn_while_the_focus_is_in_its_row(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 12))
        before = self.answers()
        _, names, _ = self.read(before)
        self.post(11, "5ac1")
        self.post(12, "9d07")
        # A count of the older post is opened and closed again: the focus is on it, and no more.
        count = {"press": ".tally"}
        first, _, closed, read = self.draw(before, count, count, {"answers": self.answers()})
        self.assertEqual(keys(first), [f"post {LONG}", "post e3b9"])
        self.assertEqual((keys(closed), open_in(closed["report"])), (keys(first), []))
        self.assertEqual(keys(read), [f"post {LONG}", "post e3b9", "post 5ac1", "post 9d07"])
        self.assertEqual(focused(read)["data-unfolds"], f"{LONG} under {names[0]}")
        self.assertEqual(focused(read)["aria-expanded"], "false")
        self.assertIn("back to the one that is open or has the focus.", fold(read)[0])

    def test_an_arrow_key_brings_the_cell_it_moves_the_focus_to_into_sight(self):
        answers = self.answers()
        pressed, right, down = self.draw(
            answers, {"press": ".tally"}, {"key": "ArrowRight"}, {"key": "ArrowDown"}
        )[1:]
        # A press leaves the box where it is: what is pressed is in sight.
        self.assertEqual(pressed["reached"], [])
        # A cell is brought no further than into sight, down its box and across it.
        nearest = {"block": "nearest", "inline": "nearest"}
        for state in (right, down):
            self.assertEqual(state["reached"], [[focused(state)["aria-label"], nearest]])
        self.assertNotEqual(right["reached"], down["reached"])

    def test_a_press_leaves_each_table_the_stop_of_the_tab_key_it_had(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        # A count of the second post is opened and the focus goes down to the installation
        # under it. Then the second fold is pressed on the timeline, and the names of a tile.
        count = {"press": ".tally", "nth": len(names) + 1}
        line = {"press": ".tog", "nth": len(names) + 1}
        counted_at, under, lined_at, named = self.draw(
            answers, count, {"key": "ArrowDown"}, line, {"press": ".btn"}
        )[1:]
        tomo = report["folds"][1]["installations"][0]
        mark = f"{tomo}, frame {LONG} from {report['messages'][1]['sender']}, unreadable, sender"
        self.assertEqual(
            stop_in(counted_at, "Messages")["data-unfolds"], f"{LONG} under {names[1]}"
        )
        self.assertEqual(focused(under)["aria-label"], mark)
        self.assertEqual(open_in(named["report"])[0], "that are tip")
        for state in (under, lined_at, named):
            self.assertEqual(stop_in(state, "Messages")["aria-label"], mark)
        # A pressed fold is the stop of its own table, and the fold in the other table is not.
        first, second = (
            f"{name}: {counted(len(fold['installations']))}"
            for name, fold in zip(names, report["folds"][:2])
        )
        for state in (lined_at, named):
            self.assertEqual(stop_in(state, "Timeline")["aria-label"], second)
            self.assertEqual(stop_in(state, "Installations")["aria-label"], first)
        self.assertEqual(stop_in(under, "Timeline")["aria-label"], first)
        # A press that takes no focus gives none to a stop it leaves where it was.
        (unfocused,) = self.draw(answers, {"press": ".mk.sent"})[1:]
        self.assertEqual(open_in(unfocused["report"]), [f"8f21 under {names[0]}"])
        self.assertEqual([made for made in within(unfocused["report"]) if "focused" in made], [])

    def test_the_button_of_the_messages_leaves_each_table_the_stop_of_the_tab_key_it_had(self):
        self.enter(mock.patch("forks.rule.SHOWN_CELLS", 12))
        answers = self.answers()
        report, names, _ = self.read(answers)
        # The second fold is pressed among the installations, the third on the timeline and a
        # count of the newest post. Then every post is asked for, and the newest two again.
        lane = {"press": ".tog", "nth": 1}
        line = {"press": ".tog", "nth": len(names) + 2}
        count = {"press": ".tally", "nth": len(names) + 1}
        whole, newest = self.draw(answers, lane, line, count, REST, REST)[4:]
        self.assertEqual(
            (fold(whole)[1], fold(newest)[1]), ("Draw the newest 2 only", "Draw all 3")
        )
        second, third = (
            f"{name}: {counted(len(under['installations']))}"
            for name, under in zip(names[1:3], report["folds"][1:3])
        )
        for state in (whole, newest):
            self.assertEqual(stop_in(state, "Installations")["aria-label"], second)
            self.assertEqual(stop_in(state, "Timeline")["aria-label"], third)
            self.assertEqual(stop_in(state, "Messages")["data-unfolds"], f"e3b9 under {names[1]}")

    def test_names_that_were_asked_of_a_tile_stand_under_the_tiles_and_move_none(self):
        closed, opened, again = self.draw(self.answers(), {"press": ".btn"}, {"press": ".btn"})

        def tiles(state: dict) -> List[Tuple[str, str]]:
            made = keyed(state["report"], "Tiles")["kids"]
            return [(words(tile), tile["attrs"]["class"]) for tile in made]

        self.assertEqual({cls for _, cls in tiles(closed)}, {"tile"})
        self.assertEqual((tiles(opened), tiles(again)), (tiles(closed), tiles(closed)))
        # The tiles and the names under them are one part of the report, in that order.
        (under,) = classed(opened["report"], "tiled")
        self.assertEqual([kid["attrs"]["class"] for kid in under["kids"]], ["tiles", "named"])
        self.assertIn(under, opened["report"]["kids"])
        for state in (closed, again):
            self.assertEqual(classed(state["report"], "tiled"), [])
            self.assertIn(keyed(state["report"], "Tiles"), state["report"]["kids"])

    def test_names_that_were_asked_of_two_tiles_stand_on_a_line_each(self):
        # Four at the tip and four an epoch behind them, of a roster of eight.
        eight = f"{SIX} saro-2 raya-2".split()
        logs = {name: member(name, 3 if name in eight[:4] else 2, " ".join(eight)) for name in eight}
        self.shares(logs)
        answers = self.answers()
        report, _, _ = self.read(answers)
        tip, behind = report["positions"][:2]
        self.assertEqual((tip["count"], behind["position"], behind["count"]), (4, "behind", 4))
        one, both = self.draw(answers, {"press": ".btn", "nth": 1}, {"press": ".btn"})[1:]
        said = [f"{at['position']} {', '.join(at['installations'])}" for at in (tip, behind)]
        self.assertEqual([words(made) for made in classed(one["report"], "named")], said[1:])
        self.assertEqual([words(made) for made in classed(both["report"], "named")], said)

    def test_names_that_were_asked_of_a_tile_go_once_the_page_is_folded_no_more(self):
        answers = self.answers()
        steps = {"press": ".btn"}, {"answers": self.unfolded(answers)}
        opened, flat = self.draw(answers, *steps)[1:]
        self.assertEqual(len(classed(opened["report"], "named")), 1)
        self.assertEqual(classed(flat["report"], "named"), [])

    def test_what_was_opened_is_not_put_back_over_another_conversation_that_was_drawn(self):
        answers = self.answers()
        report = json.loads(answers[f"conversations/{CONVO}"]["text"])
        listing = json.loads(answers["conversations"]["text"])
        # The other conversation is drawn, and the list beside it then cannot be searched.
        bent = {
            "conversations": {"status": 200, "text": json.dumps(dict(listing, conversations=""))},
            "conversations/other": {"status": 200, "text": json.dumps(dict(report, id="other"))},
        }
        steps = {"press": ".tog"}, {"answers": bent}, {"hash": "#other"}, {"press": ".btn"}
        opened, _, other, named = self.draw(answers, *steps)[1:]
        self.assertEqual(len(open_in(opened["report"])), 2)
        self.assertTrue(other["notice"].startswith("The answer at this address is not one"))
        self.assertEqual(open_in(named["report"]), ["that are tip"])

    def test_three_installations_are_named_where_they_stand_and_no_button_asks_for_them(self):
        # Three at the tip of one line, whose roster lists Mira as well.
        four = "saro raya pax mira"
        self.shares({name: member(name, 2, four) for name in four.split()[:3]})
        answers = self.answers()
        report, _, _ = self.read(answers)
        (tip,) = report["positions"][:1]
        self.assertEqual((report["folded"], tip["position"], tip["count"]), (True, "tip", 3))
        (state,) = self.draw(answers)
        (tile,) = [
            made
            for made in keyed(state["report"], "Tiles")["kids"]
            if words(made["kids"][0]) == "tip"
        ]
        self.assertEqual(
            (tile["attrs"]["class"], [words(kid) for kid in tile["kids"][3:]]),
            ("tile", [", ".join(tip["installations"])]),
        )
        (card,) = classed(state["report"], "state")
        (on_it,) = classed(card, "roster")
        self.assertEqual(len(on_it["kids"]), 3)
        # The roster of four is the one count the page gives the names of when asked.
        (asks,) = classed(state["report"], "btn")
        self.assertTrue(asks["attrs"]["aria-label"].startswith("Names of all 4 in the roster of"))

    def test_the_caption_of_a_part_says_what_a_row_of_it_stands_for(self):
        answers = self.answers()
        (state,) = self.draw(answers)
        (flat,) = self.draw(self.unfolded(answers))
        captions = {
            "Installations": (
                "a row is the installations of one position, ",
                "a row is an installation, ",
            ),
            "Timeline": ("a line is the installations of one position: ", "a line is a run, "),
            "Messages": (
                "a row is a post, a column the installations of one position, ",
                "a row is a post, a column an installation, ",
            ),
        }
        for part, starts in captions.items():
            for drawn, start in zip((state, flat), starts):
                (head,) = classed(keyed(drawn["report"], part), "sechead")
                self.assertTrue(words(head["kids"][1]).startswith(start), words(head))

    def test_another_conversation_starts_with_nothing_open(self):
        # The same group twice: the folds of the two conversations go by the same names.
        other = f"{CONVO}b"
        for name, log in crowd_logs("b").items():
            self.served.share(log, name)
        answers = self.answers()
        _, names, _ = self.read(answers)
        said = self.served.get(f"/conversations/{other}")
        answers[f"conversations/{other}"] = {"status": said.status, "text": said.body.decode()}
        self.assertEqual([name_of(each) for each in said.said["folds"]], names)
        count = {"press": ".tally"}
        there, back = ({"hash": f"#{convo}"} for convo in (other, CONVO))
        opened, elsewhere, again = self.draw(answers, {"press": ".tog"}, count, there, back)[2:]
        self.assertEqual(open_in(opened["report"]), [names[0], names[0], f"8f21 under {names[0]}"])
        for state, convo in ((elsewhere, other), (again, CONVO)):
            (title,) = [made for made in within(state["report"]) if made["tag"] == "h1"]
            self.assertEqual(words(title), convo)
            self.assertEqual(open_in(state["report"]), [])
            self.assertEqual(lines(state, "Installations"), [f"fold {name}" for name in names])

    def test_what_a_reader_opened_stays_open_while_another_conversation_is_not_drawn(self):
        answers = self.answers()
        _, names, _ = self.read(answers)
        press = {"press": ".tog"}
        there, back = {"hash": "#another"}, {"hash": f"#{CONVO}"}
        # Back before the other conversation is answered, and back from one that cannot be drawn.
        for name, steps in (("asked", ([there, back],)), ("misshapen", (there, back))):
            with self.subTest(name):
                given = {**answers, "conversations/another": {"status": 200, "text": "{}"}}
                opened, *between, closed = self.draw(given, press, *steps, press)[1:]
                for state in (opened, *between):
                    self.assertEqual(open_in(state["report"]), [names[0]] * 2)
                self.assertEqual(open_in(closed["report"]), [])

    def test_what_a_reader_opened_is_forgotten_once_the_list_was_drawn(self):
        answers = self.answers()
        _, names, _ = self.read(answers)
        steps = {"press": ".tog"}, {"hash": "#"}, {"hash": f"#{CONVO}"}
        opened, listed, back = self.draw(answers, *steps)[1:]
        self.assertEqual(open_in(opened["report"]), [names[0]] * 2)
        self.assertEqual((open_in(listed["report"]), open_in(back["report"])), ([], []))

    def test_a_fold_stays_where_it_stood_in_the_window_when_it_is_pressed(self):
        # The window is at the installations, under the line of when the group was heard from.
        reach = {"reach": '[data-key="Installations"]'}
        _, reached, opened = self.draw(self.answers(), reach, {"press": ".tog"})
        (line,) = [made for made in within(opened["report"]) if "data-heard" in made["attrs"]]
        self.assertTrue(words(line).startswith("Last heard from "))
        self.assertEqual(len(open_in(opened["report"])), 2)
        self.assertGreater(reached["window"], 0)
        self.assertEqual(opened["window"], reached["window"])

    def test_a_press_draws_anew_only_the_parts_it_changes(self):
        answers = self.answers()
        _, names, _ = self.read(answers)
        steps = {"press": ".tog"}, {"press": ".btn"}, {"press": ".tally"}, {"press": ".tog"}
        lanes, named, count, closed = self.draw(answers, *steps)[1:]
        # Each of the three parts that grow with the group stands unless the press changes it:
        # a fold the lanes and the timeline, the names of a tile none, a count the messages.
        for state, stand in (
            (lanes, ["Messages"]),
            (named, ["Installations", "Timeline", "Messages"]),
            (count, ["Installations", "Timeline"]),
            (closed, ["Messages"]),
        ):
            stood = sum(size(keyed(state["report"], part)) for part in stand)
            # The rest is made anew, but for the report itself.
            self.assertEqual(state["made"], size(state["report"]) - 1 - stood)
        self.assertGreater(size(keyed(named["report"], "Installations")), 100)

    def test_a_mark_under_a_count_opens_its_own_record_after_a_fold_is_opened_above_it(self):
        answers = self.answers()
        report, names, _ = self.read(answers)
        post, fold = report["messages"][1], report["folds"][0]
        count = {"press": ".tally", "nth": len(names)}
        lane = {"press": ".tog"}
        mark = {"press": ".mk[data-m]", "nth": 1}
        second = [seat for seat in fold["installations"] if post["cells"][seat] == "unreadable"][1]
        at = post["marks"][second]
        # The fold's lanes and lines put their marks before those of the messages, and take
        # them away again.
        for steps in ([count, lane, mark], [lane, count, lane, mark]):
            with self.subTest(steps=len(steps)):
                *_, before, read = self.draw(answers, *steps)
                opened = len(steps) == 3
                self.assertEqual(len(lines(before, "Installations")), len(names) + 3 * opened)
                self.assertEqual(read["asked"], [f"runs/{at['run']}/log?line={at['line']}"])
                said = f"line {at['line']}{second}, frame {LONG} from {post['sender']}, unreadable"
                self.assertIn(said, words(read["log"]))

    def test_the_log_lines_of_an_installation_stay_open_when_it_moves_to_a_closed_fold(self):
        before = self.answers()
        report, names, _ = self.read(before)
        (tomo,) = report["folds"][1]["installations"]
        # Tomo's fold is open and the lines around his first record are read as he catches up.
        late = Log("tomo", 12)
        late.epoch("tomo", 4, "A4", SIX)
        self.served.put(*self.runs["tomo"], logged(late))
        after = self.answers()
        moved, there, _ = self.read(after)
        self.assertEqual(
            (names[1], there[:2]), ("Branch A, behind", ["Branch A, tip", "Branch B, tip"])
        )
        self.assertIn(tomo, moved["folds"][0]["installations"])
        steps = {"press": ".tog", "nth": 1}, {"press": ".cell[data-m]"}, {"answers": after}
        opened, read = self.draw(before, *steps)[2:]
        self.assertIn(f"{tomo}, epoch 1, shared line, state ", words(opened["log"]))
        self.assertEqual(open_in(opened["report"]), [names[1]] * 2)
        # His new fold is opened for him: the lines stay, and the focus on their button.
        self.assertEqual(read["log"], opened["log"])
        self.assertEqual(open_in(read["report"]), [there[0]] * 2)
        self.assertIn(f"lane {tomo}", lines(read, "Installations"))
        held = stop_in(read, "Installations")["aria-label"]
        self.assertTrue(held.startswith(f"{tomo}, epoch 1, "), held)

    def test_the_log_lines_of_a_post_stay_open_when_the_page_turns_folded(self):
        # Three installations are no more than are in sight. Raya's record of Saro's post is
        # read as Saro goes on to a roster that lists a fourth.
        logs = {name: member(name, 2, "saro raya pax") for name in ("saro", "raya")}
        logs["saro"].sent("saro", "8f21", 2)
        logs["raya"].received("raya", "8f21", "saro", 2)
        self.shares(logs)
        before = self.answers()
        ahead = Log("saro", 12)
        ahead.epoch("saro", 3, "X3", "saro raya pax mira")
        self.served.put(*self.runs["saro"], logged(ahead))
        after = self.answers()
        (plain_report, _, _), (report, names, _) = self.read(before), self.read(after)
        self.assertEqual((plain_report["folded"], report["folded"]), (False, True))
        post = plain_report["messages"][0]
        (raya,) = [seat for seat in post["marks"] if seat != post["sender"]]
        order = [seat for lane in plain_report["lanes"] for seat in lane["installations"]]
        marked = [seat for seat in order if seat in post["marks"]]
        cell = {"press": ".mk[data-m]", "nth": marked.index(raya)}
        first, opened, read = self.draw(before, cell, {"answers": after})
        self.assertEqual((unfolding(first["report"]), keys(first)), ([], ["post 8f21"]))
        self.assertIn(f"{raya}, frame 8f21 from ", words(opened["log"]))
        # The count that stands for her record is opened under the post: the lines stay.
        self.assertEqual(read["log"], opened["log"])
        self.assertEqual(keys(read), ["post 8f21", "who 8f21"])
        (hers,) = [
            name for name, fold in zip(names, report["folds"]) if raya in fold["installations"]
        ]
        self.assertEqual(open_in(read["report"]), [f"8f21 under {hers}"])
        self.assertTrue(stop_in(read, "Messages")["aria-label"].startswith(f"{raya}, frame 8f21"))
        # Her mark of the post pressed on the timeline: her fold is opened, and her line holds it.
        opened, read = self.draw(before, {"press": ".post.read[data-m]"}, {"answers": after})[1:]
        self.assertIn(f"{raya}, frame 8f21 from ", words(opened["log"]))
        self.assertEqual(read["log"], opened["log"])
        self.assertEqual(open_in(read["report"]), [hers] * 2)
        self.assertIn(f"time {raya}", lines(read, "Timeline"))
        self.assertTrue(stop_in(read, "Timeline")["aria-label"].startswith(f"{raya}, frame 8f21"))

    def test_a_read_that_is_answered_304_draws_nothing_and_keeps_what_was_opened(self):
        before = self.answers()
        listing, report = "conversations", f"conversations/{CONVO}"
        both = [listing, report]
        self.clock.now += 60
        later = {listing: self.answers()[listing]}
        self.post(11, "5ac1")
        reads = [{"answers": said} for said in ({}, later, self.answers(), {})]
        first, opened, *read = self.draw(before, {"press": ".tog"}, *reads)
        same, unheard, changed, again = read
        # The first read names no validator, and every later one names the one it holds.
        self.assertEqual((first["asked"], first["validators"]), (both, [None, None]))
        self.assertEqual([state["asked"] for state in read], [both] * 4)
        tags = [before[path]["tag"] for path in both]
        self.assertEqual((same["validators"], unheard["validators"]), (tags, tags))
        self.assertEqual([state["notice"] for state in read], [None] * 4)
        self.assertEqual((same["unchanged"], same["report"]), (both, opened["report"]))
        # The list alone changed: the conversation went unheard a minute longer.
        self.assertEqual(unheard["unchanged"], [report])
        self.assertIn("Last heard from 1 min ago", words(same["report"]))
        self.assertIn("Last heard from 2 min ago", words(unheard["report"]))
        lanes = [plain(keyed(state["report"], "Installations")) for state in (opened, unheard)]
        self.assertEqual(lanes[0], lanes[1])
        # A post more: both are read whole, the fold stays open, and the new validators are held.
        self.assertEqual((changed["unchanged"], keys(changed)[-1]), ([], "post 5ac1"))
        self.assertEqual(lines(changed, "Installations"), lines(opened, "Installations"))
        self.assertEqual((again["unchanged"], again["report"]), (both, changed["report"]))

    def test_a_collector_that_gives_no_validator_is_read_whole_each_time(self):
        answers = {path: dict(said, tag="") for path, said in self.answers().items()}
        first, again = self.draw(answers, {"answers": {}})
        self.assertEqual((again["asked"], again["validators"]), (first["asked"], [None, None]))
        self.assertEqual((again["report"], again["notice"]), (first["report"], None))


FIVE = "saro raya kes pax mira"


def named_logs() -> Dict[str, Log]:
    """A group of five that split, as four of its members name the commit that made each state.

    Raya commits the second epoch. At the third Saro, Raya and Mira take a state Saro commits,
    which Mira names under another hash as a commit of Kes, who shares no log. Pax takes a state
    of his own there and names Tomo, whom no roster lists, as the one that committed it.
    """
    logs = {name: member(name, 1, FIVE) for name in ("saro", "raya", "pax", "mira")}
    for name, log in logs.items():
        log.epoch(name, 2, "X2", FIVE, commit="c2", by="raya")
    for name in ("saro", "raya"):
        logs[name].epoch(name, 3, "A3", FIVE, commit="a3", by="saro")
    logs["mira"].epoch("mira", 3, "A3", FIVE, commit="m3", by="kes")
    logs["pax"].epoch("pax", 3, "B3", FIVE, commit="b3", by="tomo")
    return logs


def unnamed(log: bytes) -> bytes:
    """A log without the commit any of its records names."""
    return re.sub(rb" commit=\S+ by=\S+", b"", log)


def facts(state: dict, branch: str) -> Dict[str, dict]:
    """What the card of a branch says of it, by what each row is called."""
    (listed,) = classed(keyed(state["report"], f"state {branch}"), "kv")
    called, said = listed["kids"][::2], listed["kids"][1::2]
    return {words(name): value for name, value in zip(called, said)}


def titles(report: dict, state: dict) -> Dict[str, str]:
    """What the circle of each state says to a pointer, by the name a test logs the state under."""
    (drawing,) = classed(state["report"], "lineage")
    said = [words(made) for made in within(drawing) if made["tag"] == "title"]
    names = {hexed(name, 16): name for name in ("X1", "X2", "A3", "B3", "B4")}
    return {names[held["state"]]: title for held, title in zip(report["states"], said)}


def circle(epoch: int, state: str, word: str, holders: int, members: int = 5) -> str:
    """What the circle of a state that names no commit says to a pointer."""
    held = f"state {hexed(state, 16)}, {word}, reported by {holders}"
    return f"epoch {epoch}, {held}, {members} in its roster"


def apart(name: str) -> str:
    """A run's name as a circle says it: between the marks that set it apart from the id."""
    return f"\u2068{name}\u2069"


def uncommitted(made: dict) -> dict:
    """An element without what is drawn of a commit: a row of a card, the end of a title."""
    kids = [kid if isinstance(kid, str) else uncommitted(kid) for kid in made["kids"]]
    if made["tag"] == "title":
        kids = [re.sub(r", commits? .*", "", kid) for kid in kids]
    if made["tag"] == "dl":
        rows = {at for at, kid in enumerate(kids) if words(kid) == "Its first commit"}
        kids = [kid for at, kid in enumerate(kids) if not {at, at - 1} & rows]
    return {**made, "kids": kids}


@unittest.skipUnless(NODE, "node runs the page's script, and is not on this machine")
class Commits(Collecting):
    """The commit the logs name as the one that made a state, as the script draws it."""

    answers = Drawn.answers
    draw = Drawn.draw

    def drawn(self, logs: Dict[str, bytes]) -> Tuple[dict, dict]:
        """What a collector of their own reports of these logs, and what the page draws of it."""
        # Each collector names its runs alike, so two drawings differ by their logs alone.
        self.mint = numbered(self.numbers())
        self.served = self.elsewhere()
        for name, log in logs.items():
            self.served.share(log, name)
        (state,) = self.draw(self.answers())
        self.assertIsNone(state["notice"])
        return self.served.report(), state

    def named(self) -> Tuple[dict, dict]:
        return self.drawn({name: logged(log) for name, log in named_logs().items()})

    def test_the_card_of_a_branch_says_its_first_commit_and_who_committed_it(self):
        report, state = self.named()
        a, b = report["branches"]
        self.assertEqual(
            (a["first_commits"], b["first_commits"]),
            (
                [
                    {"commit": hexed("a3", 16), "by": hexed("saro"), "holders": 2},
                    {"commit": hexed("m3", 16), "by": hexed("kes"), "holders": 1},
                ],
                [{"commit": hexed("b3", 16), "by": hexed("tomo"), "holders": 1}],
            ),
        )
        for branch in "AB":
            self.assertEqual(
                list(facts(state, branch)),
                ["Epoch", "State", "In its roster", "Its first state", "Its first commit"],
            )
            # The row is in the face of the words: its hash and its id alone are in the mono one.
            self.assertEqual(facts(state, branch)["Its first commit"]["attrs"], {})
        # Each commit of a state under two hashes has a line, and says how many name it. Saro
        # is named by the run he shares. Kes shares none, and is his id alone.
        ours, theirs = facts(state, "A")["Its first commit"]["kids"]
        self.assertEqual(
            (words(ours), words(theirs)),
            (
                f"{hexed('a3', 16)} by saro {hexed('saro')} (2 name it)",
                f"{hexed('m3', 16)} by {hexed('kes')} (1 names it)",
            ),
        )
        # The one commit of a state is a line that counts nobody. Tomo is no seat: his id alone.
        (only,) = facts(state, "B")["Its first commit"]["kids"]
        self.assertEqual(words(only), f"{hexed('b3', 16)} by {hexed('tomo')}")
        for line, commit, by in ((ours, "a3", "saro"), (theirs, "m3", "kes"), (only, "b3", "tomo")):
            self.assertEqual(line["tag"], "div")
            self.assertEqual(
                [words(made) for made in classed(line, "mono")], [hexed(commit, 16), hexed(by)]
            )

    def test_a_circle_of_the_lineage_says_the_commit_that_made_its_state(self):
        report, state = self.named()
        self.assertEqual(
            titles(report, state),
            {
                "X1": circle(1, "X1", "shared line", 4),
                "X2": circle(2, "X2", "shared line", 4)
                + f", commit {hexed('c2', 16)} by {apart('raya')} {hexed('raya')}",
                "A3": circle(3, "A3", "branch A", 3)
                + f", commits {hexed('a3', 16)} by {apart('saro')} {hexed('saro')} (2 name it)"
                + f" and {hexed('m3', 16)} by {hexed('kes')} (1 names it)",
                "B3": circle(3, "B3", "branch B", 1)
                + f", commit {hexed('b3', 16)} by {hexed('tomo')}",
            },
        )

    def test_three_commits_of_a_state_are_listed_with_a_comma_and_an_and(self):
        logs = named_logs()
        logs["raya"] = member("raya", 2, FIVE)
        logs["raya"].epoch("raya", 3, "A3", FIVE, commit="r3", by="raya")
        report, state = self.drawn({name: logged(log) for name, log in logs.items()})
        by = {"a3": f"saro {hexed('saro')}", "m3": hexed("kes"), "r3": f"raya {hexed('raya')}"}
        named = sorted(f"{hexed(commit, 16)} by {who} (1 names it)" for commit, who in by.items())
        self.assertEqual(
            [words(line) for line in facts(state, "A")["Its first commit"]["kids"]], named
        )
        said = [re.sub("saro|raya", lambda name: apart(name[0]), line) for line in named]
        self.assertEqual(
            titles(report, state)["A3"],
            circle(3, "A3", "branch A", 3) + f", commits {said[0]}, {said[1]} and {said[2]}",
        )

    def test_who_committed_is_named_by_each_name_its_runs_were_shared_under_once(self):
        logs = {name: logged(log) for name, log in named_logs().items()}
        again = Log("saro", 11)
        again.start("saro")
        again.epoch("saro", 3, "A3", FIVE)
        said = f"{hexed('a3', 16)} by saro {hexed('saro')} (2 name it)"
        for name, who in ((None, "saro"), ("saro", "saro"), ("at home", "saro, at home")):
            with self.subTest(name=name):
                report, state = self.drawn(logs)
                if name is not None:
                    self.served.share(logged(again), name)
                    report = self.served.report()
                    (state,) = self.draw(self.answers())
                names = [run["name"] for run in report["runs"]]
                self.assertEqual(names, ["saro", "raya", "pax", "mira"] + [name] * bool(name))
                (ours, _) = facts(state, "A")["Its first commit"]["kids"]
                self.assertEqual(words(ours), said.replace("saro", who, 1))
                each = ", ".join(map(apart, who.split(", ")))
                self.assertIn(
                    f", commits {said.replace('saro', each, 1)} and ", titles(report, state)["A3"]
                )

    def test_a_name_stands_apart_from_the_id_after_it_whichever_way_it_is_read(self):
        logs = {name: logged(log) for name, log in named_logs().items()}
        # A name in Hebrew, and one that has what follows it read from the right.
        sara, turned = "\u05e9\u05e8\u05d4", "\u202eoras"
        self.drawn({sara: logs.pop("saro"), **logs})
        again = Log("saro", 11)
        again.start("saro")
        again.epoch("saro", 3, "A3", FIVE)
        self.served.share(logged(again), turned)
        (state,) = self.draw(self.answers())
        (ours, _) = facts(state, "A")["Its first commit"]["kids"]
        self.assertEqual(
            [kid if isinstance(kid, str) else (kid["tag"], words(kid)) for kid in ours["kids"]],
            [
                ("span", hexed("a3", 16)),
                " by ",
                ("bdi", sara),
                ", ",
                ("bdi", turned),
                " ",
                ("span", hexed("saro")),
                " (2 name it)",
            ],
        )
        self.assertIn(
            f" by {apart(sara)}, {apart(turned)} {hexed('saro')} (2 name it) and ",
            titles(self.served.report(), state)["A3"],
        )

    def test_a_branch_whose_first_state_names_no_commit_has_no_row_for_one(self):
        logs = named_logs()
        logs["pax"] = member("pax", 2, FIVE)
        logs["pax"].epoch("pax", 3, "B3", FIVE)
        logs["pax"].epoch("pax", 4, "B4", FIVE, commit="b4", by="pax")
        report, state = self.drawn({name: logged(log) for name, log in logs.items()})
        a, b = report["branches"]
        self.assertEqual((len(a["first_commits"]), b["first_commits"]), (2, []))
        self.assertIn("Its first commit", facts(state, "A"))
        self.assertEqual(
            list(facts(state, "B")), ["Epoch", "State", "In its roster", "Its first state"]
        )
        # The state it began with says no commit, and the one a commit made says that one.
        said = titles(report, state)
        self.assertEqual(
            (said["B3"], said["B4"]),
            (
                circle(3, "B3", "branch B", 1),
                circle(4, "B4", "branch B", 1)
                + f", commit {hexed('b4', 16)} by {apart('pax')} {hexed('pax')}",
            ),
        )

    def test_the_card_of_a_conversation_that_is_whole_has_no_row_for_a_commit(self):
        logs = {name: member(name, 0) for name in ("saro", "raya")}
        for name, log in logs.items():
            log.epoch(name, 2, "X2", "saro raya", commit="c2", by="raya")
        report, state = self.drawn({name: logged(log) for name, log in logs.items()})
        (branch,) = report["branches"]
        named = {"commit": hexed("c2", 16), "by": hexed("raya"), "holders": 2}
        self.assertEqual((report["split_epoch"], branch["first_commits"]), (None, [named]))
        self.assertEqual(
            list(facts(state, "A")), ["Epoch", "State", "In its roster", "Its first state"]
        )
        # The circle of its one state says the commit, as that of any state does.
        said = circle(2, "X2", "shared line", 2, 2)
        raya = f"{apart('raya')} {named['by']}"
        self.assertEqual(
            titles(report, state), {"X2": f"{said}, commit {named['commit']} by {raya}"}
        )

    def test_the_card_of_a_lone_branch_that_begins_in_two_states_has_no_row_for_a_commit(self):
        logs = {name: member(name, 2) for name in ("saro", "raya")}
        logs["saro"].epoch("saro", 3, "A3", "saro raya", commit="a3", by="saro")
        logs["raya"].epoch("raya", 3, "B3", "saro raya", commit="b3", by="raya")
        for name, log in logs.items():
            log.epoch(name, 4, "B4", "saro raya", commit="b4", by="saro")
        report, state = self.drawn({name: logged(log) for name, log in logs.items()})
        (branch,) = report["branches"]
        named = sorted([(hexed("a3", 16), hexed("saro")), (hexed("b3", 16), hexed("raya"))])
        self.assertEqual(
            (report["split_epoch"], branch["since"], branch["first_commits"]),
            (3, 3, [{"commit": commit, "by": by, "holders": 1} for commit, by in named]),
        )
        self.assertEqual(
            list(facts(state, "A")), ["Epoch", "State", "In its roster", "Its first state"]
        )
        # The circle of each of the two says the commit that made it.
        said = titles(report, state)
        by = {"A3": f"{hexed('a3', 16)} by {apart('saro')} {hexed('saro')}"}
        by["B3"] = f"{hexed('b3', 16)} by {apart('raya')} {hexed('raya')}"
        for name, commit in by.items():
            self.assertEqual(said[name], circle(3, name, "branch A", 1, 2) + f", commit {commit}")

    def test_logs_that_name_no_commit_are_drawn_as_the_same_logs_that_do_but_for_the_commits(self):
        logs = {name: logged(log) for name, log in named_logs().items()}
        told, named = self.drawn(logs)
        bare, plain = self.drawn({name: unnamed(log) for name, log in logs.items()})
        self.assertEqual([run["id"] for run in told["runs"]], [run["id"] for run in bare["runs"]])
        self.assertNotEqual(named["report"], plain["report"])
        self.assertEqual(json.dumps(uncommitted(named["report"])), json.dumps(plain["report"]))
        # Nothing of a commit is drawn of them: no row, and no title that goes on.
        self.assertEqual(uncommitted(plain["report"]), plain["report"])
        for branch in "AB":
            self.assertNotIn("Its first commit", facts(plain, branch))
        self.assertEqual(
            [said.split(", ")[-1] for said in titles(bare, plain).values()],
            ["5 in its roster"] * 4,
        )


class Gained(Collecting):
    """What the collector's JSON holds for the page beside what `--json` holds."""

    def test_a_conversation_counts_the_lines_of_the_runs_its_report_lists(self):
        self.share_group("saro", "raya")
        alone = logged(member("mira", roster="mira", convo=OTHER))
        self.served.share(b"  at a backtrace\n" + alone, "mira")
        counts = {convo: self.served.report(convo)["lines"] for convo in (CONVO, OTHER)}
        self.assertEqual(
            counts,
            {
                CONVO: {"lines": 6, "records": 6, "unstamped": 0, "malformed": 0, "unknown": 0},
                OTHER: {"lines": 4, "records": 3, "unstamped": 1, "malformed": 0, "unknown": 0},
            },
        )
        listed = self.served.listed()["lines"]
        self.assertEqual(
            listed, {count: counts[CONVO][count] + counts[OTHER][count] for count in listed}
        )

    def test_a_malformed_line_is_counted_in_the_conversations_of_its_run_alone(self):
        self.share_group("saro")
        torn = logged(member("raya")) + b"2026-10-08T10:00:09.000Z  INFO libchat::groupv2trace: epoch\n"
        self.served.share(torn, "raya")
        self.served.share(logged(member("mira", roster="mira", convo=OTHER)), "mira")
        self.assertEqual(self.served.report()["lines"]["malformed"], 1)
        self.assertEqual(self.served.report(OTHER)["lines"]["malformed"], 0)

    def test_an_entry_says_how_long_its_conversation_has_gone_unheard(self):
        runs = self.share_group("saro", "raya")
        self.served.share(logged(member("mira", roster="mira", convo=OTHER)), "mira")
        self.clock.pass_days(1)
        self.served.put(*runs["raya"])
        self.clock.now += 90
        unheard = {
            entry["id"]: (entry["heard"], entry["unheard"])
            for entry in self.served.listed()["conversations"]
        }
        self.assertEqual(
            unheard,
            {CONVO: ("2026-10-10T12:00:00Z", 90), OTHER: ("2026-10-09T12:00:00Z", DAY + 90)},
        )

    def test_a_run_heard_from_while_the_list_is_drawn_has_gone_unheard_for_no_time(self):
        runs = self.share_group("saro", "raya")
        collector = self.served.collector
        report = collector._report

        def heard_meanwhile(convo: str):
            self.clock.now += 5
            self.served.put(*runs["raya"])
            return report(convo)

        with mock.patch.object(collector, "_report", heard_meanwhile):
            (entry,) = self.served.listed()["conversations"]
        self.assertEqual((entry["heard"], entry["unheard"]), ("2026-10-09T12:00:05Z", 0))

    def test_the_longest_conversation_id_is_asked_for_in_an_address_of_8000_bytes(self):
        convo = "%e6" * MAX_CONVO
        self.served.share(logged(member("mira", roster="mira", convo=convo)), "mira")
        (entry,) = self.served.listed()["conversations"]
        self.assertEqual(entry["id"], convo)
        address = "/conversations/" + convo.replace("%", "%25")
        self.assertEqual(len(address), 8000)
        answer = self.served.get(address)
        self.assertEqual((answer.status, answer.said["id"]), (200, convo))

    def test_what_the_page_reads_of_a_conversation_does_not_change_while_nothing_is_sent(self):
        self.share_group("saro", "raya")
        first = self.served.get(f"/conversations/{CONVO}").body
        self.clock.pass_days(1)
        self.assertEqual(self.served.get(f"/conversations/{CONVO}").body, first)
        self.assertNotIn("unheard", json.loads(first))
