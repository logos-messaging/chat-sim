"""The report page as the collector serves it, and a browser's way in to it."""

import hmac
import itertools
import json
import os
import re
import shutil
import subprocess
import unittest
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
