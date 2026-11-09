import json
from pathlib import Path
import subprocess
import unittest
ROOT = Path(__file__).resolve().parents[1]

class ViewerIndexParserTests(unittest.TestCase):
    def inspect_indexes(self, cases):
        parser = (ROOT / 'viewer/index-parser.js').read_text()
        source = parser + '\nconsole.log(JSON.stringify(' + json.dumps(cases) + '.map(inspectIndex)))'
        result = subprocess.run(['node', '-e', source], text=True, capture_output=True, check=True)
        return json.loads(result.stdout)

    def test_empty_valid_incompatible_and_partial(self):
        valid = '## 项目 (project)\n| id | title | tldr | status | updated |\n|---|---|---|---|---|\n| project-demo | Demo | demo | current | 2026-11-03 |'
        cases = ['## Unknown', '## Unknown (unknown)\ntext', valid.replace('\n', '\r\n'), '', '| id | title |\n|---|---|', 'not an index', valid, valid+'\n## Unknown\n| bad | bad |']
        rows = self.inspect_indexes(cases)
        self.assertEqual(['INDEX 格式不兼容', 'INDEX 格式不兼容', '', '', '', 'INDEX 格式不兼容', '', 'INDEX 部分内容无法解析'], [row['issue'] for row in rows])
        self.assertEqual('project-demo', rows[6]['nodes'][0]['id'])

    def test_auxiliary_tables_do_not_count_as_unparsed_nodes(self):
        empty = (
            '## 项目 (project)\n'
            '| id | 标题 | TL;DR | status | last_verified |\n'
            '|----|------|-------|--------|----------------|\n'
        )
        valid = empty + '| project-demo | Demo | Summary | current | 2026-11-09 |\n'
        auxiliary = (
            '\n## 定期摄取清单 (routine digest — 会定期更新、需周期性复查的源)\n'
            '| 源 | 类型 | cadence | 上次摄取 | 关联节点 |\n'
            '|----|------|---------|----------|----------|\n'
            '| Demo | 文档 | weekly | 2026-W46 | project-demo |\n'
            '\n## 群聊摄取进度 (feishu_chat 增量高水位)\n'
            '| 群名 | chat_id | 已摄取至 | 最近 raw_id |\n'
            '|------|---------|----------|-------------|\n'
            '| Demo | oc_demo | 2026-11-09T00:00:00+08:00 | raw-demo |\n'
        )
        rows = self.inspect_indexes([
            empty + auxiliary,
            valid + auxiliary,
            auxiliary + '\n' + valid + '| project-broken | Missing columns |\n',
        ])
        self.assertEqual(['', '', 'INDEX 部分内容无法解析'], [row['issue'] for row in rows])
        self.assertEqual([[], ['project-demo'], ['project-demo']],
                         [[node['id'] for node in row['nodes']] for row in rows])
