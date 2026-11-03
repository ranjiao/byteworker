"""Synthetic, deterministic retrieval baseline; no private KB or LLM claims."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from kb_query import _search_records


def records():
    result = {}
    for i, title in enumerate(('素材生成项目', '会议纪要归档', '渠道预算分配', '设备维护计划',
                               '客户反馈统计', '版本发布流程', '权限申请审批', '数据质量检查')):
        key = f'project-{i}'
        result[key] = dict(id=key, title=title, tags=['demo'], type='project',
                           status='current', tldr='', body=title, links=[], sources=['raw-demo'], path=key+'.md')
    return result


class QueryBaselineTests(unittest.TestCase):
    def test_32_queries_and_negative_controls(self):
        data = records()
        for key, row in data.items():
            title = row['title']
            for query in (title, f'请问{title}最近怎么样', f'我想了解{title}的情况', key):
                with self.subTest(query=query):
                    result = _search_records(data, query, graph_depth=0)
                    self.assertEqual(key, result['candidates'][0]['id'])
                    self.assertEqual(['raw-demo'], result['candidates'][0]['sources'])
        for query in ('不存在的宇宙航线', 'xyz-no-match', '预算', '你好'):
            result = _search_records(data, query, graph_depth=0)
            if query != '预算':
                self.assertEqual([], result['candidates'])
        # A graph neighbor is a candidate with graph reason, never a text match.
        data['project-0']['links'] = ['project-1']
        result = _search_records(data, 'project-0')
        self.assertEqual(['graph:project-0'], result['candidates'][1]['reasons'])

    def test_exact_body_match_precedes_partial_title(self):
        data = records()
        data['project-1']['body'] = '请问素材生成项目最近怎么样'
        result = _search_records(data, '请问素材生成项目最近怎么样', graph_depth=0)
        self.assertEqual('project-1', result['candidates'][0]['id'])

    def test_bounded_scale_and_inactive_thinking(self):
        data = records()
        base = data['project-0']
        for i in range(1000):
            key = f'noise-{i}'
            data[key] = dict(base, id=key, title='无关资料', body='无关资料')
        data['old'] = dict(base, id='old', type='thinking', status='inactive')
        started = time.perf_counter()
        result = _search_records(data, '素材生成项目怎么样', max_nodes=5)
        self.assertNotIn('old', [row['id'] for row in result['candidates']])
        self.assertLessEqual(len(result['candidates']), 5)
        self.assertLess(time.perf_counter()-started, 5)
