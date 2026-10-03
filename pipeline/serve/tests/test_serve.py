"""Server tests: routes, gzip, cache headers, SPA fallback, placeholder and path traversal.
Run: python -m unittest discover pipeline/serve/tests"""
import gzip
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import serve  # noqa: E402

MANIFEST = {"schema": "swarm-bundle/1", "generated": "2030-01-05T00:00:00Z",
            "counts": {"agents": 3, "rooms": 1, "entities": 0},
            "range": {"first_day": 1, "last_day": 4, "first_date": "2030-01-01", "last_date": "2030-01-04"}}


class ServeBase(unittest.TestCase):
    with_dist = False

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.data = os.path.join(cls.tmp, "bundle", "data")
        cls.dist = os.path.join(cls.tmp, "web-dist")
        os.makedirs(os.path.join(cls.data, "sources"))
        with open(os.path.join(cls.data, "bundle.json"), "w") as f:
            json.dump({"manifest": MANIFEST, "agents": [{"id": "agent-alpha"}] * 50}, f)
        with open(os.path.join(cls.data, "sources", "1.json"), "w") as f:
            json.dump({"chat:x": {"text": "fake"}}, f)
        with open(os.path.join(cls.tmp, "secret.txt"), "w") as f:
            f.write("outside")
        os.symlink(os.path.join(cls.tmp, "secret.txt"), os.path.join(cls.data, "link.json"))
        if cls.with_dist:
            os.makedirs(os.path.join(cls.dist, "assets"))
            with open(os.path.join(cls.dist, "index.html"), "w") as f:
                f.write("<!doctype html><title>app</title>")
            with open(os.path.join(cls.dist, "assets", "app-abc123.js"), "w") as f:
                f.write("console.log('fake app');" * 20)
        cls.srv = serve.make_server("127.0.0.1", 0, cls.data, cls.dist)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp)

    def get(self, path, method="GET", headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest(method, path, skip_accept_encoding=True)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        c.endheaders()
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, dict(r.getheaders()), body


class NoDist(ServeBase):
    def test_placeholder(self):
        status, h, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Data: AI Digest / AI Village", body)
        self.assertIn(b"agents", body)
        self.assertEqual(self.get("/some/route")[0], 404)

    def test_health(self):
        status, h, body = self.get("/api/health")
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertEqual(doc["ok"], True)
        self.assertEqual(doc["generated"], "2030-01-05T00:00:00Z")
        self.assertEqual(doc["counts"]["agents"], 3)
        self.assertEqual(doc["dist"], False)

    def test_data_json_and_gzip(self):
        status, h, body = self.get("/data/bundle.json")
        self.assertEqual(status, 200)
        self.assertEqual(h["Cache-Control"], "public, max-age=60")
        self.assertTrue(h["Content-Type"].startswith("application/json"))
        plain = body
        status, h, body = self.get("/data/bundle.json", headers={"Accept-Encoding": "gzip, br"})
        self.assertEqual(h.get("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), plain)
        status, h, body = self.get("/data/bundle.json", method="HEAD")
        self.assertEqual((status, body), (200, b""))

    def test_on_demand_file(self):
        self.assertEqual(self.get("/data/sources/1.json")[0], 200)
        self.assertEqual(self.get("/data/sources/2.json")[0], 404)
        self.assertEqual(self.get("/data/")[0], 404)
        self.assertEqual(self.get("/data")[0], 404)

    def test_path_traversal(self):
        for path in ("/data/../secret.txt", "/data/%2e%2e/secret.txt", "/data/..%2fsecret.txt",
                     "/data/sources/../../secret.txt", "/data/%2e%2e%2f%2e%2e%2fserve.py", "/data//etc/passwd",
                     "/data/link.json", "/data/%00bundle.json", "/data/..%5csecret.txt", "/data/%ff%fe",
                     "/../secret.txt", "/%2e%2e/secret.txt", "//etc/passwd"):
            status, h, body = self.get(path)
            self.assertIn(status, (400, 404), path)
            self.assertNotIn(b"outside", body, path)

    def test_methods(self):
        self.assertEqual(self.get("/data/bundle.json", method="POST")[0], 405)
        self.assertEqual(self.get("/api/health", method="DELETE")[0], 405)

    def test_not_modified(self):
        status, h, _ = self.get("/data/bundle.json")
        status, h2, body = self.get("/data/bundle.json", headers={"If-Modified-Since": h["Last-Modified"]})
        self.assertEqual((status, body), (304, b""))


class WithDist(ServeBase):
    with_dist = True

    def test_index_and_spa_fallback(self):
        status, h, body = self.get("/")
        self.assertEqual((status, body), (200, b"<!doctype html><title>app</title>"))
        self.assertEqual(h["Cache-Control"], "no-cache")
        status, h, body = self.get("/agents/agent-alpha")
        self.assertEqual((status, body), (200, b"<!doctype html><title>app</title>"))
        self.assertEqual(self.get("/assets/missing.js")[0], 404)

    def test_assets(self):
        status, h, body = self.get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertIn("immutable", h["Cache-Control"])
        self.assertEqual(h["Content-Encoding"], "gzip")
        self.assertTrue(h["Content-Type"].startswith("text/javascript"))

    def test_traversal_from_dist(self):
        for path in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"):
            status, h, body = self.get(path)
            self.assertNotIn(b"outside", body, path)

    def test_health_sees_dist(self):
        self.assertEqual(json.loads(self.get("/api/health")[2])["dist"], True)


class SymlinkedIndexes(unittest.TestCase):
    """index.html files and bundle.json that are symlinks to files outside their folder are never read."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.outside = os.path.join(self.tmp, "secret.txt")
        with open(self.outside, "w") as f:
            f.write("outside")
        self.data = os.path.join(self.tmp, "bundle", "data")
        self.dist = os.path.join(self.tmp, "web-dist")
        os.makedirs(self.data)
        os.makedirs(os.path.join(self.dist, "nested"))
        os.makedirs(os.path.join(self.dist, "real"))
        with open(os.path.join(self.dist, "real", "index.html"), "w") as f:
            f.write("<!doctype html><title>real</title>")
        os.symlink(self.outside, os.path.join(self.dist, "nested", "index.html"))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp)

    def start(self):
        self.srv = serve.make_server("127.0.0.1", 0, self.data, self.dist)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        port = self.srv.server_address[1]

        def get(path):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", path)
            r = c.getresponse()
            body = r.read()
            c.close()
            return r.status, body
        return get

    def test_symlinked_root_index_and_manifest(self):
        os.symlink(self.outside, os.path.join(self.dist, "index.html"))
        manifest = os.path.join(self.tmp, "manifest.json")
        with open(manifest, "w") as f:
            json.dump({"manifest": {"generated": "outside", "counts": {"x": 1}}}, f)
        os.symlink(manifest, os.path.join(self.data, "bundle.json"))
        get = self.start()
        for path in ("/", "/unknown-spa-route", "/nested/", "/nested", "/index.html"):
            status, body = get(path)
            self.assertNotIn(b"outside", body, path)
        health = json.loads(get("/api/health")[1])
        self.assertEqual((health["ok"], health["generated"], health["dist"]), (False, None, False))

    def test_symlinked_nested_index(self):
        with open(os.path.join(self.dist, "index.html"), "w") as f:
            f.write("<!doctype html><title>app</title>")
        get = self.start()
        status, body = get("/nested/")
        self.assertEqual(status, 404)
        self.assertNotIn(b"outside", body)
        self.assertEqual(get("/real/"), (200, b"<!doctype html><title>real</title>"))
        self.assertEqual(get("/spa/route"), (200, b"<!doctype html><title>app</title>"))

    def test_data_symlink_swap(self):
        gens = os.path.join(self.tmp, "bundle", "gen")
        for g in ("a", "b"):
            os.makedirs(os.path.join(gens, g))
            with open(os.path.join(gens, g, "bundle.json"), "w") as f:
                json.dump({"manifest": {"generated": g, "counts": {}}}, f)
        os.rmdir(self.data)
        os.symlink("gen/a", self.data)
        get = self.start()
        self.assertEqual(json.loads(get("/api/health")[1])["generated"], "a")
        os.symlink("gen/b", self.data + ".new")
        os.replace(self.data + ".new", self.data)
        self.assertEqual(json.loads(get("/api/health")[1])["generated"], "b")
        self.assertEqual(json.loads(get("/data/bundle.json")[1])["manifest"]["generated"], "b")


class SafePath(unittest.TestCase):
    def test_safe_path(self):
        root = tempfile.mkdtemp()
        try:
            self.assertEqual(serve.safe_path(root, "/a/b.json"), os.path.join(os.path.realpath(root), "a", "b.json"))
            self.assertEqual(serve.safe_path(root, "/../x"), os.path.join(os.path.realpath(root), "x"))
            self.assertIsNone(serve.safe_path(root, "/a\x00"))
            self.assertIsNone(serve.safe_path(root, "/..\\x"))
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
