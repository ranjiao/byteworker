import importlib.util
import json
import subprocess
import tempfile
import unittest
import urllib.request
from functools import partial
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('viewer_server', ROOT / 'bin/viewer-server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class ViewerAccessTests(unittest.TestCase):
    def test_todo_status_api_archives_transactionally_and_rejects_stale_or_foreign_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            todo = root / 'todo.md'
            initial = (
                '# TODO\n\n## Active\n\n'
                '### [ ] T-20261109-001 · 第一项\n- status: open\n\n'
                '### [ ] T-20261109-002 · 第二项\n- status: waiting\n\n'
                '## Completed\n'
            )
            todo.write_text(initial, encoding='utf-8')

            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()

            git('init', '-q')
            git('config', 'user.name', 'Viewer Test')
            git('config', 'user.email', 'viewer@example.test')
            git('add', 'todo.md')
            git('commit', '-qm', 'init')

            class Handler(server.ViewerHandler):
                kb = root
                auth_required = True
                api_token = 'secret'

            with server.ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root))) as httpd:
                worker = Thread(target=httpd.serve_forever)
                worker.start()
                try:
                    base = f'http://127.0.0.1:{httpd.server_port}'

                    def send(todo_id, status, *, token='secret', origin=base, content_type='application/json'):
                        request = urllib.request.Request(
                            base + '/api/todo/status', method='POST',
                            data=json.dumps({'id': todo_id, 'status': status}).encode(),
                            headers={'Content-Type': content_type, 'X-Byteworker-Token': token, 'Origin': origin},
                        )
                        with urllib.request.urlopen(request, timeout=5) as response:
                            return json.load(response)

                    first, second = 'T-20261109-001', 'T-20261109-002'
                    for status, options, code in (
                        ('done', {'token': ''}, 403),
                        ('done', {'origin': 'http://other.test'}, 403),
                        ('done', {'content_type': 'text/plain'}, 400),
                        ('waiting', {}, 400),
                    ):
                        with self.assertRaises(HTTPError) as error:
                            send(first, status, **options)
                        self.assertEqual(code, error.exception.code)
                    self.assertEqual(initial, todo.read_text())

                    completed = send(first, 'done')
                    self.assertEqual('committed', completed['transaction']['status'])
                    commit = git('rev-parse', 'HEAD')
                    self.assertEqual(commit, completed['transaction']['commit'])
                    self.assertEqual('unchanged', send(first, 'done')['transaction']['status'])
                    with self.assertRaises(HTTPError) as error:
                        send(first, 'cancelled')
                    self.assertEqual(409, error.exception.code)
                    self.assertEqual(commit, git('rev-parse', 'HEAD'))

                    discarded = send(second, 'cancelled')
                    self.assertEqual('committed', discarded['transaction']['status'])
                    archived = todo.read_text().split('## Completed')[1]
                    self.assertIn(f'### [x] {first} · 第一项\n- kind: task\n- status: done', archived)
                    self.assertIn(f'### [x] {second} · 第二项\n- kind: task\n- status: cancelled', archived)
                    paths = git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').splitlines()
                    self.assertEqual(2, len(paths))
                    self.assertIn('todo.md', paths)
                    self.assertTrue(any(path.startswith('journal/') for path in paths))
                    self.assertEqual('', git('status', '--porcelain'))
                finally:
                    httpd.shutdown()
                    worker.join()

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

    def test_viewer_boot_page_and_versioned_parser_require_cache_revalidation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'app').symlink_to(ROOT / 'viewer', target_is_directory=True)

            class Handler(server.ViewerHandler):
                kb = root

            factory = partial(Handler, directory=str(root))
            with server.ThreadingHTTPServer(('127.0.0.1', 0), factory) as httpd:
                worker = Thread(target=httpd.serve_forever)
                worker.start()
                try:
                    base = f'http://127.0.0.1:{httpd.server_port}/app/'
                    with urllib.request.urlopen(base + 'index.html?launch=test', timeout=5) as page:
                        self.assertEqual('no-cache', page.headers['Cache-Control'])
                        self.assertRegex(page.read(), rb'<script src="index-parser\.js\?v=\d+"></script>')
                    with urllib.request.urlopen(base + 'index-parser.js?v=2', timeout=5) as parser:
                        self.assertEqual('no-cache', parser.headers['Cache-Control'])
                        self.assertIn(b'function inspectIndex', parser.read())
                finally:
                    httpd.shutdown()
                    worker.join()

    def test_dashboard_endpoint_requires_token_and_uses_shared_projection(self):
        handler = object.__new__(server.ViewerHandler)
        handler.path = "/api/dashboard"
        handler.auth_required = True
        handler.api_token = "secret"
        handler.headers = {}
        handler.kb = ROOT
        handler._reject_unauthorized = mock.Mock()
        handler._send_json = mock.Mock()
        view = {"markdown": "# 工作看板"}
        with mock.patch.object(server, "dashboard_view", return_value=view) as render:
            handler.do_GET()
            handler._reject_unauthorized.assert_called_once()
            render.assert_not_called()
            handler.headers = {"X-Byteworker-Token": "secret"}
            handler.do_GET()
            render.assert_called_once_with(ROOT)
            handler._send_json.assert_called_once_with(view)

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
