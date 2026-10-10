"""The report page as the collector serves it, and a browser's way in to it."""

import hmac
import itertools
import json
import os
import re
import shutil
import unittest
from pathlib import Path
from typing import Dict, List, Optional, Tuple
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
