import importlib.util
import tempfile
import unittest
import urllib.request
from functools import partial
from pathlib import Path
from threading import Thread
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('viewer_server', ROOT / 'bin/viewer-server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class ViewerAccessTests(unittest.TestCase):
    def test_favicon_is_public_for_get_and_head(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'app').symlink_to(ROOT / 'viewer', target_is_directory=True)

            class Handler(server.ViewerHandler):
                kb = root
                auth_required = True
                api_token = 'secret'

            factory = partial(Handler, directory=str(root))
            with server.ThreadingHTTPServer(('127.0.0.1', 0), factory) as httpd:
                worker = Thread(target=httpd.serve_forever)
                worker.start()
                try:
                    url = f'http://127.0.0.1:{httpd.server_port}/favicon.ico'
                    icon = (ROOT / 'viewer' / 'favicon.ico').read_bytes()
                    self.assertEqual(b'\x00\x00\x01\x00', icon[:4])
                    for method, body in (('GET', icon), ('HEAD', b'')):
                        with self.subTest(method=method):
                            request = urllib.request.Request(url, method=method)
                            with urllib.request.urlopen(request, timeout=5) as response:
                                self.assertEqual(200, response.status)
                                self.assertEqual(url, response.geturl())
                                self.assertIn(response.headers.get_content_type(),
                                              {'image/x-icon', 'image/vnd.microsoft.icon'})
                                self.assertEqual(len(icon), int(response.headers['Content-Length']))
                                self.assertEqual(body, response.read())
                finally:
                    httpd.shutdown()
                    worker.join()

    def test_private_reads_and_path_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kb = root / 'data'
            kb.mkdir()
            (root / 'kb').symlink_to(kb, target_is_directory=True)
            (root / 'app').mkdir()
            (kb / 'escape').symlink_to(root.parent, target_is_directory=True)
            (kb / 'alias').symlink_to(kb / 'state', target_is_directory=True)
            handler = object.__new__(server.ViewerHandler)
            handler.directory = str(root)
            handler.kb = kb
            handler.api_token = 'secret'
            handler.auth_required = True
            handler.headers = {}
            handler.send_error = mock.Mock()
            handler._reject_unauthorized = mock.Mock()
            handler.path = '/kb/INDEX.md'
            self.assertFalse(handler._static_allowed())
            handler.headers = {'X-Byteworker-Token': 'secret'}
            self.assertTrue(handler._static_allowed())
            handler.path = '/kb/state/dreaming/reports/morning-2026-08-06/artifacts/report.html'
            self.assertTrue(handler._static_allowed())
            handler.headers = {}
            self.assertFalse(handler._static_allowed())
            handler.path = '/kb/INDEX.md?api_token=secret'
            self.assertTrue(handler._static_allowed())
            for auth in (True, False):
                handler.auth_required = auth
                for path in ('/kb/.git/config', '/kb/%2e%2e/private', '/kb/state/viewer.json',
                             '/kb/escape/secret', '/kb/alias/viewer.json', '/kb/passwords.txt', '/kb/', '/other', '/app/.env'):
                    handler.path = path
                    self.assertFalse(handler._static_allowed(), path)
            handler.auth_required = True
            handler.path = '/api/dreaming/runs'
            with mock.patch.object(server, 'list_runs') as runs:
                handler._handle_dreaming_runs_get()
                runs.assert_not_called()
            handler.path = '/app/index.html'
            self.assertTrue(handler._static_allowed())
