"""End-to-end tests for fitlog-mcp over real HTTP (stdlib unittest + urllib)."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import PROTOCOL_VERSION
from mcp.transport import LoginRateLimiter, MCPHandler, create_server

EXPECTED_TOOLS = {"save_household_fact", "recall_fact", "forget_fact"}


class FitLogServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls._tmp.name, "test.db")
        cls.api_token = "test-token"
        cls.owner_password = "test-owner-pw"
        cls.public_url = "https://fitlog.example.com"
        cls.httpd, cls.store = create_server(
            "127.0.0.1", 0, db,
            api_token=cls.api_token, public_url=cls.public_url,
            owner_password=cls.owner_password,
        )
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.store.close()
        cls._tmp.cleanup()

    # -- helpers ----------------------------------------------------------

    def _post(self, payload, session=None, version=PROTOCOL_VERSION,
              origin=None, raw_body=None, token="test-token"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if session:
            headers["Mcp-Session-Id"] = session
        if version is not None:
            headers["MCP-Protocol-Version"] = version
        if origin:
            headers["Origin"] = origin
        body = raw_body if raw_body is not None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + "/mcp", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _get(self, path, token="test-token", headers=None):
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _new_session(self, token="test-token"):
        status, headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            version=None,
            token=token,
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["result"]["protocolVersion"], PROTOCOL_VERSION)
        sid = headers.get("Mcp-Session-Id")
        self.assertTrue(sid, "initialize must issue Mcp-Session-Id")
        # notifications/initialized -> 202, no body
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session=sid,
            token=token,
        )
        self.assertEqual(status, 202)
        return sid

    def _call(self, session, tool, arguments, token="test-token"):
        status, _, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            session=session,
            token=token,
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertNotIn("error", data)
        return data["result"]

    # -- handshake ----------------------------------------------------------

    def test_initialize_handshake(self):
        sid = self._new_session()
        self.assertTrue(len(sid) >= 16)

    def test_health_endpoint(self):
        with urllib.request.urlopen(self.base + "/health", timeout=10) as resp:
            data = json.loads(resp.read())
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["protocol"], PROTOCOL_VERSION)

    def test_ping(self):
        sid = self._new_session()
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 9, "method": "ping"}, session=sid
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], {})

    # -- tools ----------------------------------------------------------------

    def test_tools_list(self):
        sid = self._new_session()
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, session=sid
        )
        self.assertEqual(status, 200)
        tools = json.loads(body)["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, EXPECTED_TOOLS)
        by_name = {t["name"]: t for t in tools}
        # annotation spot checks
        self.assertFalse(by_name["save_household_fact"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["save_household_fact"]["annotations"]["destructiveHint"])
        self.assertTrue(by_name["recall_fact"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["forget_fact"]["annotations"]["destructiveHint"])
        for t in tools:
            self.assertIn("inputSchema", t)
            self.assertIn("annotations", t)

    def test_fact_roundtrip(self):
        import re
        sid = self._new_session()
        # Save a fact
        res = self._call(sid, "save_household_fact", {
            "content": "Daughter's school is Lincoln Elementary",
            "category": "family",
            "visibility": "family",
            "added_by": "mom",
        })
        self.assertFalse(res["isError"])
        save_text = res["content"][0]["text"]
        # Extract the fact id from "Saved fact #123: ..."
        match = re.search(r"#(\d+)", save_text)
        self.assertIsNotNone(match, f"Could not extract fact id from: {save_text}")
        fact_id = int(match.group(1))

        # Recall the fact
        res = self._call(sid, "recall_fact", {"query": "daughter"})
        self.assertFalse(res["isError"])
        recall_text = res["content"][0]["text"]
        self.assertIn("Lincoln Elementary", recall_text)

        # Forget the fact
        res = self._call(sid, "forget_fact", {"fact_id": fact_id})
        self.assertFalse(res["isError"])
        self.assertIn(str(fact_id), res["content"][0]["text"])

        # Confirm it's gone
        res = self._call(sid, "recall_fact", {"query": "Lincoln"})
        self.assertIn("No facts", res["content"][0]["text"])

    def test_unknown_tool(self):
        sid = self._new_session()
        res = self._call(sid, "nope", {})
        self.assertTrue(res["isError"])

    # -- transport hardening ------------------------------------------------------

    def test_origin_rejection(self):
        sid = self._new_session()
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            session=sid,
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)

    def test_origin_localhost_allowed(self):
        sid = self._new_session()
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
            session=sid,
            origin="http://localhost:3000",
        )
        self.assertEqual(status, 200)

    def test_bad_protocol_version(self):
        sid = self._new_session()
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 8, "method": "ping"},
            session=sid,
            version="1999-01-01",
        )
        self.assertEqual(status, 400)

    def test_missing_session(self):
        status, _, _ = self._post({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(status, 400)

    def test_session_termination(self):
        sid = self._new_session()
        req = urllib.request.Request(
            self.base + "/mcp",
            headers={
                "Mcp-Session-Id": sid,
                "Authorization": "Bearer test-token",
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
        # old session is dead
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, session=sid
        )
        self.assertEqual(status, 404)
        # double delete -> 404
        req = urllib.request.Request(
            self.base + "/mcp",
            headers={
                "Mcp-Session-Id": sid,
                "Authorization": "Bearer test-token",
            },
            method="DELETE",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    # -- auth (Alexa+ checklist) ----------------------------------------------

    def test_unauthenticated_gets_401_without_www_authenticate(self):
        status, headers, body = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": PROTOCOL_VERSION}},
            token=None,
        )
        self.assertEqual(status, 401)
        # Alexa+ forbids the WWW-Authenticate header on 401s.
        self.assertNotIn("WWW-Authenticate", headers)
        self.assertNotIn("Www-Authenticate", headers)
        self.assertIn("oauth-protected-resource", body.decode())

    def test_bad_token_gets_401(self):
        status, _, _ = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}, token="wrong"
        )
        self.assertEqual(status, 401)

    def test_protected_resource_metadata(self):
        status, _, body = self._get(
            "/.well-known/oauth-protected-resource", token=None
        )
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertEqual(doc["resource"], self.public_url + "/mcp")
        self.assertIn(self.public_url, doc["authorization_servers"])
        self.assertIn("fitlog.write", doc["scopes_supported"])

    def test_authorization_server_metadata(self):
        status, _, body = self._get(
            "/.well-known/oauth-authorization-server", token=None
        )
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertIn("S256", doc["code_challenge_methods_supported"])
        self.assertTrue(doc["authorization_endpoint"].endswith("/authorize"))
        self.assertTrue(doc["token_endpoint"].endswith("/token"))

    def _owner_jar(self):
        """Owner sign-in round-trip; return {'Cookie': ...} for /authorize."""
        import re
        import urllib.parse

        qs = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": "alexa-plus",
            "redirect_uri": "https://client.example/cb",
            "scope": "fitlog.read fitlog.write",
            "state": "s1",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
            "resource": self.public_url + "/mcp",
        })
        status, _, body = self._get("/authorize?" + qs, token=None)
        self.assertEqual(status, 200)
        form = dict(re.findall(r'name="([^"]+)" value="([^"]*)"',
                               body.decode()))
        form["owner_password"] = self.owner_password

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(
            self.base + "/authorize",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            opener.open(req, timeout=10)
            self.fail("expected 302")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            set_cookie = e.headers.get("Set-Cookie", "")
        for part in set_cookie.split(";"):
            name, _, v = part.strip().partition("=")
            if name == "fitlog_owner":
                return {"Cookie": f"fitlog_owner={v.strip()}"}
        self.fail("sign-in must set the owner cookie")

    def _oauth_token(self):
        """Run the full authorize -> token PKCE flow; return the token."""
        import urllib.parse

        jar = self._owner_jar()
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # S256(verifier)
        params = {
            "response_type": "code",
            "client_id": "alexa-plus",
            "redirect_uri": "https://client.example/cb",
            "scope": "fitlog.read fitlog.write",
            "state": "s1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self.public_url + "/mcp",
        }
        qs = urllib.parse.urlencode(params)
        status, _, body = self._get("/authorize?" + qs, token=None, headers=jar)
        self.assertEqual(status, 200)
        self.assertIn("Approve", body.decode())

        # approve: POST the form, do not follow the 302
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        form = dict(params, approved="yes")
        req = urllib.request.Request(
            self.base + "/authorize",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **jar},
            method="POST",
        )
        try:
            opener.open(req, timeout=10)
            self.fail("expected 302")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            loc = e.headers["Location"]
        q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        self.assertEqual(q["state"], ["s1"])
        code = q["code"][0]

        # token exchange
        token_form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": verifier,
            "resource": self.public_url + "/mcp",
        }
        req = urllib.request.Request(
            self.base + "/token",
            data=urllib.parse.urlencode(token_form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        self.assertEqual(data["token_type"], "Bearer")
        return data["access_token"], params, verifier

    def test_oauth_pkce_flow_end_to_end(self):
        token, _, _ = self._oauth_token()
        # the issued token works on the MCP endpoint: initialize, then ping
        status, headers, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            token=token,
            version=None,
        )
        self.assertEqual(status, 200)
        sid = headers.get("Mcp-Session-Id")
        self.assertTrue(sid)
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            session=sid,
            token=token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["result"], {})

    def test_oauth_pkce_wrong_verifier(self):
        import urllib.parse

        jar = self._owner_jar()
        # run the flow manually with a bad verifier
        challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        p = {
            "response_type": "code",
            "client_id": "alexa-plus",
            "redirect_uri": "https://client.example/cb",
            "scope": "fitlog.read",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self.public_url + "/mcp",
        }
        qs = urllib.parse.urlencode(p)
        self.assertEqual(
            self._get("/authorize?" + qs, token=None, headers=jar)[0], 200)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        form = dict(p, approved="yes")
        req = urllib.request.Request(
            self.base + "/authorize",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **jar},
            method="POST",
        )
        try:
            opener.open(req, timeout=10)
            self.fail("expected 302")
        except urllib.error.HTTPError as e:
            loc = e.headers["Location"]
        code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
        bad_form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": "wrong-verifier",
            "resource": self.public_url + "/mcp",
        }
        req = urllib.request.Request(
            self.base + "/token",
            data=urllib.parse.urlencode(bad_form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertIn("PKCE", e.read().decode())

    def test_plain_pkce_method_rejected(self):
        status, _, body = self._get(
            "/authorize?response_type=code&client_id=x"
            "&redirect_uri=https://client.example/cb"
            "&code_challenge=abc&code_challenge_method=plain"
            "&resource=" + self.public_url.replace(":", "%3A") + "%2Fmcp",
            token=None,
        )
        self.assertEqual(status, 400)
        self.assertIn("S256", body.decode())

    # -- regression tests for review findings ---------------------------------

    def test_exercise_name_normalization_no_fake_pr(self):
        sid = self._new_session()
        self._call(sid, "log_workout", {
            "exercise": "Deadlift",
            "sets": [{"reps": 5, "weight": 80}],
        })
        res = self._call(sid, "log_workout", {
            "exercise": " Deadlift",  # leading space, lighter weight
            "sets": [{"reps": 5, "weight": 70}],
        })
        self.assertFalse(res["isError"])
        self.assertNotIn("New PR", res["content"][0]["text"])
        prs = self._call(sid, "get_personal_records", {})
        text = prs["content"][0]["text"]
        # one entry only, max still 80
        self.assertEqual(text.count("Deadlift"), 1)
        self.assertIn("80", text)

    def test_case_insensitive_pr_grouping(self):
        sid = self._new_session()
        self._call(sid, "log_workout", {
            "exercise": "barbell row",
            "sets": [{"reps": 5, "weight": 80}],
        })
        self._call(sid, "log_workout", {
            "exercise": "Barbell Row",
            "sets": [{"reps": 5, "weight": 82}],
        })
        prs = self._call(sid, "get_personal_records", {})
        text = prs["content"][0]["text"]
        self.assertEqual(text.lower().count("barbell row"), 1)
        self.assertIn("82", text)
        # history is consistent: one filter finds both
        hist = self._call(sid, "get_history", {"exercise": "BARBELL ROW"})
        self.assertIn("82", hist["content"][0]["text"])
        self.assertIn("80", hist["content"][0]["text"])

    def test_date_must_be_yyyy_mm_dd(self):
        sid = self._new_session()
        res = self._call(sid, "log_workout", {
            "exercise": "Squat",
            "sets": [{"reps": 5, "weight": 100}],
            "date": "yesterday",
        })
        self.assertTrue(res["isError"])
        self.assertIn("YYYY-MM-DD", res["content"][0]["text"])
        res = self._call(sid, "log_protein", {"grams": 30, "date": "tomorrow"})
        self.assertTrue(res["isError"])

    def test_nan_inf_rejected(self):
        sid = self._new_session()
        res = self._call(sid, "log_workout", {
            "exercise": "Deadlift",
            "sets": [{"reps": 5, "weight": 1e309}],  # inf
        })
        self.assertTrue(res["isError"])
        self.assertNotIn("inf", res["content"][0]["text"].lower())
        res = self._call(sid, "log_protein", {"grams": float("nan")})
        self.assertTrue(res["isError"])

    def test_estimated_1rm_uses_best_set(self):
        sid = self._new_session()
        res = self._call(sid, "log_workout", {
            "exercise": "Squat",
            "sets": [{"reps": 1, "weight": 100}, {"reps": 10, "weight": 95}],
        })
        text = res["content"][0]["text"]
        # 95x10 -> Epley ~126.7, higher than 100x1 -> 103.3
        self.assertIn("New estimated 1RM", text)
        self.assertIn("126.7", text)
        prs = self._call(sid, "get_personal_records", {})
        self.assertIn("126.7", prs["content"][0]["text"])

    def test_array_params_gives_32602(self):
        sid = self._new_session()
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": [{"name": "ping"}]},
            session=sid,
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["error"]["code"], -32602)

    def test_batch_rejected(self):
        sid = self._new_session()
        status, _, body = self._post(
            [{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
            session=sid,
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], -32600)

    def test_lb_unit_converted_to_kg(self):
        sid = self._new_session()
        res = self._call(sid, "log_workout", {
            "exercise": "Overhead Press",
            "sets": [{"reps": 5, "weight": 176.37}],  # ~= 80 kg
            "unit": "lb",
        })
        text = res["content"][0]["text"]
        self.assertFalse(res["isError"])
        self.assertIn("lb", text)
        prs = self._call(sid, "get_personal_records", {})
        # stored in kg: 176.37 lb -> 80.0 kg
        self.assertIn("80", prs["content"][0]["text"])

    def test_protein_past_date_label(self):
        sid = self._new_session()
        res = self._call(sid, "log_protein",
                         {"grams": 25, "date": "2026-09-29"})
        text = res["content"][0]["text"]
        self.assertFalse(res["isError"])
        self.assertIn("Total on 2026-09-29", text)
        self.assertNotIn("Total today", text)

    # -- scope enforcement ------------------------------------------------

    def _oauth_token_with_scope(self, scope: str):
        """Run the PKCE flow requesting a specific scope; return the token."""
        import urllib.parse

        jar = self._owner_jar()
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        params = {
            "response_type": "code",
            "client_id": "alexa-plus",
            "redirect_uri": "https://client.example/cb",
            "scope": scope,
            "state": "s1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": self.public_url + "/mcp",
        }
        qs = urllib.parse.urlencode(params)
        status, _, body = self._get("/authorize?" + qs, token=None, headers=jar)
        self.assertEqual(status, 200)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        form = dict(params, approved="yes")
        req = urllib.request.Request(
            self.base + "/authorize",
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **jar},
            method="POST",
        )
        try:
            opener.open(req, timeout=10)
            self.fail("expected 302")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 302)
            loc = e.headers["Location"]
        code = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["code"][0]
        token_form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": verifier,
            "resource": self.public_url + "/mcp",
        }
        req = urllib.request.Request(
            self.base + "/token",
            data=urllib.parse.urlencode(token_form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        self.assertEqual(data["scope"], scope)
        return data["access_token"]

    def test_read_only_token_cannot_write(self):
        token = self._oauth_token_with_scope("fitlog.read")
        sid = self._new_session(token=token)
        # write tool -> JSON-RPC error about insufficient scope
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "save_household_fact",
                        "arguments": {"content": "test", "category": "x",
                                      "visibility": "private", "added_by": "y"}}},
            session=sid, token=token,
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("error", data)
        self.assertIn("fitlog.write", data["error"]["message"])
        # read tool -> works
        res = self._call(sid, "recall_fact", {"query": "test"}, token=token)
        self.assertIn("content", res)

    def test_write_token_can_write(self):
        token = self._oauth_token_with_scope("fitlog.write")
        sid = self._new_session(token=token)
        res = self._call(sid, "save_household_fact", {
            "content": "test fact",
            "category": "test",
            "visibility": "private",
            "added_by": "writer",
        }, token=token)
        self.assertFalse(res["isError"])
        # read tool -> blocked
        status, _, body = self._post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "recall_fact", "arguments": {"query": "test"}}},
            session=sid, token=token,
        )
        data = json.loads(body)
        self.assertIn("error", data)
        self.assertIn("fitlog.read", data["error"]["message"])



class OwnerGateTest(unittest.TestCase):
    """Tests for the FITLOG_OWNER_PASSWORD gate on /authorize."""

    PASSWORD = "s3cret-owner-pw"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls._tmp.name, "gate.db")
        cls.public_url = "https://fitlog.example.com"
        cls.httpd, cls.store = create_server(
            "127.0.0.1", 0, db,
            api_token="test-token", public_url=cls.public_url,
            owner_password=cls.PASSWORD,
        )
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.store.close()
        cls._tmp.cleanup()

    # -- helpers ----------------------------------------------------------

    def _oauth_params(self):
        return {
            "response_type": "code",
            "client_id": "alexa-plus",
            "redirect_uri": "https://client.example/cb",
            "scope": "fitlog.read fitlog.write",
            "state": "s1",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
            "resource": self.public_url + "/mcp",
        }

    def _raw(self, method, path, body=None, headers=None):
        import urllib.parse

        req = urllib.request.Request(
            self.base + path, data=body, headers=headers or {}, method=method
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _hidden_fields(self, html_body):
        import re

        return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', html_body))

    def _sign_in(self, password, client_ip=None, xff=None):
        """POST the login form; return (status, headers, body, params).

        client_ip simulates a distinct remote client behind the tunnel via
        the CF-Connecting-IP header (the socket peer is always 127.0.0.1).
        xff sends a (possibly forged) X-Forwarded-For header with NO
        CF-Connecting-IP, to prove the limiter ignores it.
        """
        import urllib.parse

        status, _, body = self._raw(
            "GET", "/authorize?" + urllib.parse.urlencode(self._oauth_params())
        )
        self.assertEqual(status, 200)
        form = self._hidden_fields(body.decode())
        form["owner_password"] = password
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if client_ip:
            headers["CF-Connecting-IP"] = client_ip
        if xff:
            headers["X-Forwarded-For"] = xff
        return self._raw(
            "POST", "/authorize",
            body=urllib.parse.urlencode(form).encode(),
            headers=headers,
        )

    def _cookie_value(self, headers):
        set_cookie = headers.get("Set-Cookie", "")
        for part in set_cookie.split(";"):
            name, _, v = part.strip().partition("=")
            if name == "fitlog_owner":
                return v.strip()
        return None

    # -- tests ------------------------------------------------------------

    def test_login_page_shown_before_approval(self):
        import urllib.parse

        status, _, body = self._raw(
            "GET", "/authorize?" + urllib.parse.urlencode(self._oauth_params())
        )
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn('name="owner_password"', text)
        self.assertNotIn("Approve", text)

    def test_wrong_password_rejected(self):
        status, _, body = self._sign_in("wrong-pw")
        self.assertEqual(status, 403)
        self.assertIn("Wrong password", body.decode())

    def test_approve_without_signin_rejected(self):
        import urllib.parse

        form = dict(self._oauth_params(), approved="yes")
        status, _, body = self._raw(
            "POST", "/authorize",
            body=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 403)
        self.assertIn("Owner sign-in required", body.decode())

    def test_tampered_cookie_rejected(self):
        import urllib.parse

        status, _, body = self._raw(
            "GET", "/authorize?" + urllib.parse.urlencode(self._oauth_params()),
            headers={"Cookie": "fitlog_owner=12345.deadbeef"},
        )
        self.assertEqual(status, 200)
        self.assertIn('name="owner_password"', body.decode())

    def test_full_gated_flow(self):
        import urllib.parse

        status, headers, _ = self._sign_in(self.PASSWORD)
        self.assertEqual(status, 302)
        cookie = self._cookie_value(headers)
        self.assertTrue(cookie, "sign-in must set the owner cookie")
        jar = {"Cookie": f"fitlog_owner={cookie}"}

        # approval page now renders
        status, _, body = self._raw(
            "GET", "/authorize?" + urllib.parse.urlencode(self._oauth_params()),
            headers=jar,
        )
        self.assertEqual(status, 200)
        self.assertIn("Approve", body.decode())

        # approve with the cookie -> 302 with code
        form = self._hidden_fields(body.decode())
        form["approved"] = "yes"
        status, headers, _ = self._raw(
            "POST", "/authorize",
            body=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **jar},
        )
        self.assertEqual(status, 302)
        q = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers["Location"]).query)
        code = q["code"][0]

        # token exchange
        token_form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://client.example/cb",
            "code_verifier": "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
            "resource": self.public_url + "/mcp",
        }
        status, _, body = self._raw(
            "POST", "/token",
            body=urllib.parse.urlencode(token_form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 200)
        token = json.loads(body)["access_token"]
        self.assertTrue(token)

        # issued token works on /mcp
        status, _, _ = self._raw(
            "POST", "/mcp",
            body=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": PROTOCOL_VERSION,
                           "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}},
            }).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"},
        )
        self.assertEqual(status, 200)

    def test_same_origin_login_post_allowed(self):
        import urllib.parse

        status, _, body = self._raw(
            "GET", "/authorize?" + urllib.parse.urlencode(self._oauth_params())
        )
        form = self._hidden_fields(body.decode())
        form["owner_password"] = self.PASSWORD
        # a browser posting the login form sends Origin: <public url>
        status, _, _ = self._raw(
            "POST", "/authorize",
            body=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Origin": self.public_url},
        )
        self.assertEqual(status, 302)

    def test_cookie_signature_and_expiry(self):
        from mcp import auth as oauth

        good = oauth.owner_cookie_value(self.PASSWORD)
        self.assertTrue(oauth.owner_cookie_valid(good, self.PASSWORD))
        # tampered signature
        ts, sig = good.split(".", 1)
        self.assertFalse(
            oauth.owner_cookie_valid(ts + "." + "0" * len(sig), self.PASSWORD))
        # wrong password
        self.assertFalse(oauth.owner_cookie_valid(good, "other"))
        # expired (older than 2h TTL)
        old = oauth.owner_cookie_value(self.PASSWORD,
                                       now=__import__("time").time() - 3 * 3600)
        self.assertFalse(oauth.owner_cookie_valid(old, self.PASSWORD))
        # garbage / empty
        self.assertFalse(oauth.owner_cookie_valid("garbage", self.PASSWORD))
        self.assertFalse(oauth.owner_cookie_valid(None, self.PASSWORD))
        self.assertFalse(oauth.owner_cookie_valid("", self.PASSWORD))

    def test_public_requires_password_rule(self):
        import os as _os
        from mcp.transport import public_requires_password

        old_pub = _os.environ.get("FITLOG_PUBLIC_URL")
        old_pw = _os.environ.get("FITLOG_OWNER_PASSWORD")
        try:
            _os.environ.pop("FITLOG_PUBLIC_URL", None)
            _os.environ.pop("FITLOG_OWNER_PASSWORD", None)
            self.assertIsNone(public_requires_password())
            _os.environ["FITLOG_PUBLIC_URL"] = "https://x.example"
            self.assertIsNotNone(public_requires_password())
            _os.environ["FITLOG_OWNER_PASSWORD"] = "pw"
            self.assertIsNone(public_requires_password())
        finally:
            if old_pub is None:
                _os.environ.pop("FITLOG_PUBLIC_URL", None)
            else:
                _os.environ["FITLOG_PUBLIC_URL"] = old_pub
            if old_pw is None:
                _os.environ.pop("FITLOG_OWNER_PASSWORD", None)
            else:
                _os.environ["FITLOG_OWNER_PASSWORD"] = old_pw

    def test_rate_limit_after_five_wrong_passwords(self):
        # Five wrong attempts -> 403 each; the sixth is 429 (locked out).
        # The limiter lives on the handler's shared ServerContext; reset it
        # first so this test does not interfere with (or inherit from) others.
        MCPHandler.ctx.login_limiter = LoginRateLimiter()
        try:
            for i in range(5):
                status, _, _ = self._sign_in("wrong-pw-%d" % i)
                self.assertEqual(status, 403, f"attempt {i+1} should be 403")
            status, _, body = self._sign_in("wrong-pw-5")
            self.assertEqual(status, 429)
            self.assertIn("Too many", body.decode())
            # The right password is also blocked during lockout.
            status, _, _ = self._sign_in(self.PASSWORD)
            self.assertEqual(status, 429)
        finally:
            # Reset so later tests are not affected.
            MCPHandler.ctx.login_limiter = LoginRateLimiter()

    def test_rate_limit_keyed_by_real_ip_behind_tunnel(self):
        # Behind the tunnel every socket peer is 127.0.0.1, so the limiter
        # must key on CF-Connecting-IP: an attacker's failures must not
        # lock out the owner coming from a different IP.
        MCPHandler.ctx.login_limiter = LoginRateLimiter()
        try:
            attacker, owner = "203.0.113.7", "198.51.100.42"
            for i in range(5):
                status, _, _ = self._sign_in("wrong-pw-%d" % i, attacker)
                self.assertEqual(status, 403)
            status, _, _ = self._sign_in("wrong-pw-5", attacker)
            self.assertEqual(status, 429, "attacker should be locked out")
            # The owner, on a different IP, is unaffected.
            status, headers, _ = self._sign_in(self.PASSWORD, owner)
            self.assertEqual(status, 302, "owner login should not be locked")
            self.assertIn("fitlog_owner", headers.get("Set-Cookie", ""))
        finally:
            MCPHandler.ctx.login_limiter = LoginRateLimiter()

    def test_rate_limit_ignores_spoofed_x_forwarded_for(self):
        # X-Forwarded-For is client-forgeable and must never feed the rate
        # limiter. With no CF-Connecting-IP present, every attempt keys on
        # the socket peer IP (127.0.0.1 in tests), so rotating forged XFF
        # values across 5 bad attempts still triggers the lockout on the
        # 6th attempt.
        MCPHandler.ctx.login_limiter = LoginRateLimiter()
        try:
            for i in range(5):
                fake_ip = "203.0.113.%d" % (i + 1)
                status, _, _ = self._sign_in("wrong-pw-%d" % i, xff=fake_ip)
                self.assertEqual(status, 403, f"attempt {i+1} should be 403")
            status, _, body = self._sign_in("wrong-pw-5", xff="203.0.113.99")
            self.assertEqual(status, 429,
                             "lockout must key on socket peer, not XFF")
            self.assertIn("Too many", body.decode())
        finally:
            MCPHandler.ctx.login_limiter = LoginRateLimiter()


class NoOwnerPasswordTest(unittest.TestCase):
    """Fail-closed /authorize: with no owner password configured, no login
    page, no approval page, and no codes — even when a public URL is set
    (the tunneled-but-forgot-the-password scenario)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(cls._tmp.name, "nopw.db")
        cls.public_url = "https://fitlog.example.com"
        cls.httpd, cls.store = create_server(
            "127.0.0.1", 0, db,
            api_token="test-token", public_url=cls.public_url,
            owner_password="",
        )
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls._thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.store.close()
        cls._tmp.cleanup()

    def _oauth_params(self):
        return {
            "response_type": "code",
            "client_id": "alexa-plus",
            "redirect_uri": "https://client.example/cb",
            "scope": "fitlog.read fitlog.write",
            "state": "s1",
            "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            "code_challenge_method": "S256",
            "resource": self.public_url + "/mcp",
        }

    def _raw(self, method, path, body=None, headers=None):
        import urllib.parse

        req = urllib.request.Request(
            self.base + path, data=body, headers=headers or {}, method=method
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_get_authorize_refused_without_password(self):
        import urllib.parse

        status, _, body = self._raw(
            "GET", "/authorize?" + urllib.parse.urlencode(self._oauth_params())
        )
        self.assertEqual(status, 503)
        text = body.decode()
        self.assertIn("Authorization unavailable", text)
        self.assertNotIn('name="owner_password"', text)
        self.assertNotIn("Approve", text)

    def test_post_approve_refused_without_password(self):
        import urllib.parse

        form = dict(self._oauth_params(), approved="yes")
        status, headers, _ = self._raw(
            "POST", "/authorize",
            body=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 403)
        # no code issued: must not redirect to the client
        self.assertNotIn("Location", headers)

    def test_post_login_refused_without_password(self):
        import urllib.parse

        form = dict(self._oauth_params(), owner_password="anything")
        status, headers, _ = self._raw(
            "POST", "/authorize",
            body=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(status, 403)
        self.assertNotIn("Set-Cookie", headers)
        self.assertNotIn("Location", headers)


class OAuthPersistenceTest(unittest.TestCase):
    """Codes and tokens survive a restart (SQLite, not memory)."""

    def test_token_survives_new_authstate(self):
        import tempfile

        from mcp.auth import AuthState

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "oauth.db")
            a1 = AuthState(db_path=db)
            code = a1.issue_code(
                challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                redirect_uri="https://client.example/cb",
                resource="https://fitlog.example.com/mcp",
                scope="fitlog.read",
                client_id="alexa-plus",
            )
            token, scope, err = a1.redeem_code(
                code=code,
                verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
                redirect_uri="https://client.example/cb",
                resource="https://fitlog.example.com/mcp",
            )
            self.assertIsNone(err)
            self.assertEqual(scope, "fitlog.read")
            a1.close()

            # "Restart": a brand-new AuthState on the same file.
            a2 = AuthState(db_path=db)
            try:
                self.assertEqual(a2.token_scopes(token), {"fitlog.read"})
            finally:
                a2.close()

    def test_code_single_use_persists(self):
        import tempfile

        from mcp.auth import AuthState

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "oauth2.db")
            a1 = AuthState(db_path=db)
            code = a1.issue_code(
                challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                redirect_uri="https://client.example/cb",
                resource="https://fitlog.example.com/mcp",
                scope="fitlog.read fitlog.write",
                client_id="alexa-plus",
            )
            a1.close()

            a2 = AuthState(db_path=db)
            try:
                token, _, err = a2.redeem_code(
                    code=code,
                    verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
                    redirect_uri="https://client.example/cb",
                    resource="https://fitlog.example.com/mcp",
                )
                self.assertIsNone(err)
                self.assertTrue(token)
                # Second redeem fails: single use.
                _, _, err2 = a2.redeem_code(
                    code=code,
                    verifier="dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
                    redirect_uri="https://client.example/cb",
                    resource="https://fitlog.example.com/mcp",
                )
                self.assertIsNotNone(err2)
            finally:
                a2.close()


if __name__ == "__main__":
    unittest.main()
