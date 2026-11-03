import json
from pathlib import Path
import subprocess
import unittest
ROOT = Path(__file__).resolve().parents[1]

class ViewerIndexParserTests(unittest.TestCase):
    def test_empty_valid_incompatible_and_partial(self):
        parser = (ROOT / 'viewer/index-parser.js').read_text()
        valid = '## 项目 (project)\n| id | title | tldr | status | updated |\n|---|---|---|---|---|\n| project-demo | Demo | demo | current | 2026-11-03 |'
        cases = ['## Unknown', '## Unknown (unknown)\ntext', valid.replace('\n', '\r\n'), '', '| id | title |\n|---|---|', 'not an index', valid, valid+'\n## Unknown\n| bad | bad |']
        source = parser + '\nconsole.log(JSON.stringify(' + json.dumps(cases) + '.map(inspectIndex)))'
        result = subprocess.run(['node', '-e', source], text=True, capture_output=True, check=True)
        rows = json.loads(result.stdout)
        self.assertEqual(['INDEX 格式不兼容', 'INDEX 格式不兼容', '', '', '', 'INDEX 格式不兼容', '', 'INDEX 部分内容无法解析'], [row['issue'] for row in rows])
        self.assertEqual('project-demo', rows[6]['nodes'][0]['id'])
