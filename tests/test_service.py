"""End-to-end tests against the in-process mock Syncthing."""

import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import mock_syncthing  # noqa: E402

from app.config import Config  # noqa: E402
from app.service import Service, ServiceError  # noqa: E402
from app.syncthing import Syncthing  # noqa: E402

GB = mock_syncthing.GB


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="syncpick-test-")
        mock_syncthing.mock = mock_syncthing.Mock(cls.root)
        cls.httpd = mock_syncthing.Server(("127.0.0.1", 0), mock_syncthing.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cfg = Config({"SYNCTHING_URL": f"http://127.0.0.1:{cls.port}", "SYNCTHING_API_KEY": mock_syncthing.API_KEY})
        cls.svc = Service(cfg, Syncthing(cfg.syncthing_url, cfg.api_key))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.root, ignore_errors=True)

    def listing(self, folder):
        path = mock_syncthing.mock.folders[folder]["path"]
        return sorted(d for d in os.listdir(path) if not d.startswith("."))

    def test_films_full_cycle(self):
        with self.assertRaises(ServiceError):
            self.svc.plan("films", [])
        r = self.svc.enable("films")
        self.assertEqual(r["selected"], ["Arrival (2016)", "Heat (1995)", "Old Rip (2001)", "Whiplash (2014)"])

        t = self.svc.tree("films")
        by = {i["name"]: i for i in t["items"]}
        self.assertEqual(by["Heat (1995)"]["state"], "partial")
        self.assertEqual(by["Old Rip (2001)"]["state"], "local-only")
        self.assertEqual(by["Dune (2021) [Bluray-2160p]"]["state"], "absent")
        self.assertTrue(t["mount"]["ok"])

        new = ["Arrival (2016)", "Dune (2021) [Bluray-2160p]", "The Thing {1982}"]
        p = self.svc.plan("films", new)
        self.assertEqual([a["path"] for a in p["additions"]], ["Dune (2021) [Bluray-2160p]", "The Thing {1982}"])
        self.assertEqual([d["path"] for d in p["deletions"]], ["Heat (1995)", "Old Rip (2001)", "Whiplash (2014)"])
        self.assertIn("!/Dune (2021) \\[Bluray-2160p\\]", p["patterns"])
        self.assertIn("!/The Thing \\{1982\\}", p["patterns"])
        self.assertEqual(p["patterns"][-2], "*")

        with self.assertRaises(ServiceError):
            self.svc.plan("films", ["../etc"])
        with self.assertRaises(ServiceError):
            self.svc.plan("films", ["Not In Catalogue (1999)"])

        rep = self.svc.apply("films", new)
        self.assertTrue(rep["wrote_patterns"])
        self.assertEqual([d["path"] for d in rep["deleted"]], ["Heat (1995)", "Old Rip (2001)", "Whiplash (2014)"])
        self.assertEqual(self.listing("films"), ["Arrival (2016)"])
        with open(os.path.join(mock_syncthing.mock.folders["films"]["path"], ".stignore")) as fh:
            self.assertIn("!/Arrival (2016)", fh.read())

        # Re-applying the same selection is a no-op.
        rep = self.svc.apply("films", new)
        self.assertFalse(rep["wrote_patterns"])
        self.assertEqual(rep["deleted"], [])

    def test_tv_seasons(self):
        self.svc.enable("tv")
        new = ["Severance", "The Bear/Season 02", "The Bear/Season 03", "Slow Horses/Season 01"]
        p = self.svc.plan("tv", new)
        self.assertEqual([d["path"] for d in p["deletions"]], ["The Bear/Season 01"])
        # Seasons already covered by the whole-show entry do not count as additions.
        self.assertEqual([a["path"] for a in p["additions"]], ["Slow Horses/Season 01"])
        with self.assertRaises(ServiceError):
            self.svc.plan("tv", ["The Bear/Season 02/x.mkv"])
        rep = self.svc.apply("tv", new)
        self.assertEqual([d["path"] for d in rep["deleted"]], ["The Bear/Season 01"])
        tv = mock_syncthing.mock.folders["tv"]["path"]
        self.assertEqual(sorted(os.listdir(os.path.join(tv, "The Bear"))), ["Season 02", "Season 03"])

    def test_mount_guard_refuses_deletion(self):
        self.svc.enable("tv")
        real = mock_syncthing.mock.folders["tv"]["path"]
        fake = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(fake, ".stfolder"))
            os.makedirs(os.path.join(fake, "Severance", "Season 01"))
            with open(os.path.join(fake, "Severance", "Season 01", "ep.mkv"), "w") as fh:
                fh.write("x")
            with open(os.path.join(fake, ".stignore"), "w") as fh:
                fh.write("*\n")
            cfg = Config({"SYNCTHING_URL": f"http://127.0.0.1:{self.port}", "SYNCTHING_API_KEY": mock_syncthing.API_KEY, "PATH_MAP": f"{real}:{fake}"})
            svc = Service(cfg, Syncthing(cfg.syncthing_url, cfg.api_key))
            before = svc.st.ignores("tv")
            with self.assertRaises(ServiceError) as ctx:
                svc.apply("tv", [])
            self.assertEqual(ctx.exception.status, 409)
            self.assertEqual(svc.st.ignores("tv"), before)
            self.assertTrue(os.path.exists(os.path.join(fake, "Severance", "Season 01", "ep.mkv")))
        finally:
            shutil.rmtree(fake, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
