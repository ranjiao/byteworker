import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "viewer" / "index.html"


class ViewerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise RuntimeError("node is required for viewer runtime tests")

    def run_node(self, source):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            script = Path(temporary) / "viewer-test.cjs"
            script.write_text(source, encoding="utf-8")
            return subprocess.run(
                ["node", str(script)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

    def test_full_inline_script_is_valid_javascript(self):
        completed = self.run_node(
            textwrap.dedent(
                f"""\
                const fs = require('fs');
                const vm = require('vm');
                const html = fs.readFileSync({json.dumps(str(VIEWER))}, 'utf8');
                const match = html.match(/<script>\\s*([\\s\\S]*?)<\\/script>/);
                if (!match) throw new Error('inline viewer script not found');
                new vm.Script(match[1], {{ filename: 'viewer/index.html' }});
                process.stdout.write('syntax-ok');
                """
            )
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("syntax-ok", completed.stdout)

    def test_todo_list_parses_existing_format_and_keeps_deadlines_separate_from_reminders(self):
        completed = self.run_node(
            textwrap.dedent(
                f"""\
                const fs = require('fs');
                const vm = require('vm');
                const assert = require('node:assert/strict');
                const html = fs.readFileSync({json.dumps(str(VIEWER))}, 'utf8');
                const template = fs.readFileSync('templates/todo.md', 'utf8');
                const helpers = html.slice(html.indexOf('function esc('), html.indexOf('function nodeDir('));
                const todo = html.slice(html.indexOf('const TODO_STATUS'), html.indexOf('async function renderReaderTodos'));
                const context = vm.createContext({{
                  assert, template,
                  icon: () => '',
                  Date: class extends Date {{ static now() {{ return Date.parse('2026-11-09T12:00+08:00'); }} }},
                }});
                vm.runInContext(helpers + todo + String.raw`
                  assert.equal(parseTodoMarkdown(template).length, 0);
                  const sample = [
                    '## Active',
                    '### [ ] T-20261109-001 · 跟进进度',
                    '- kind: follow_up',
                    '- status: waiting',
                    '- due_at: 2026-11-09T10:00+08:00',
                    '- remind_at: 2026-11-08T09:00+08:00',
                    '- snoozed_until: 2026-11-10T09:00+08:00',
                    '### [ ] T-20261109-002 · <img onerror=alert(1)>',
                    '- remind_at: 2026-11-08T09:00+08:00',
                    '- note: regression',
                    '## Completed',
                    '### [X] T-20261109-003 · 已交付',
                    '- status: done',
                    '- due_at: 2026-11-01T18:00+08:00',
                    '### [x] T-20261109-004 · 历史计划',
                    '- status: cancelled',
                  ].join('\\n');
                  const items = parseTodoMarkdown(sample.replaceAll('\\n', '\\r\\n'));
                  assert.equal(items.length, 4);
                  assert.equal(items[1].status, 'open');
                  const active = renderTodoList(items, 'active', '');
                  assert.match(active, /todo-group overdue[\\s\\S]*跟进进度[\\s\\S]*等待中 · 跟进/);
                  assert.match(active, /截止<\\/span><time[^>]*>2026-11-09 10:00/);
                  assert.match(active, /暂停至<\\/span><time[^>]*>2026-11-10 09:00/);
                  assert.match(active, /todo-group unscheduled[\\s\\S]*&lt;img[\\s\\S]*提醒<\\/span><time/);
                  assert.doesNotMatch(active, /<img|已交付|历史计划/);
                  assert.match(renderTodoList(items, 'all', ' REGRESSION '), /&lt;img/);
                  const archived = renderTodoList(items, 'archived', '');
                  assert.match(archived, /已完成/);
                  assert.match(archived, /已废弃/);
                  assert.doesNotMatch(archived, /todo-group overdue/);
                  assert.throws(() => parseTodoMarkdown('# TODO'), /Active/);
                  assert.throws(() => parseTodoMarkdown(sample + '\\n### [ ] T-20261109-001 · 重复'), /重复/);
                `, context);
                """
            )
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_thinking_nodes_are_parsed_routed_and_linkable(self):
        completed = self.run_node(
            textwrap.dedent(
                f"""\
                const fs = require('fs');
                const vm = require('vm');
                const html = fs.readFileSync({json.dumps(str(VIEWER))}, 'utf8');
                const constants = html.slice(
                  html.indexOf('const DIR ='),
                  html.indexOf('const TODAY')
                );
                const nodeDir = html.slice(
                  html.indexOf('function nodeDir('),
                  html.indexOf('\\nfunction kbUrl(', html.indexOf('function nodeDir('))
                );
                const parseIndex = fs.readFileSync({json.dumps(str(ROOT / "viewer/index-parser.js"))}, 'utf8');
                const source = constants + '\\n' + nodeDir + '\\n' + parseIndex;
                const context = {{}};
                vm.createContext(context);
                vm.runInContext(source, context);
                const index = [
                  '## 思考 (thinking)',
                  '',
                  '| id | title | tldr | status | updated |',
                  '|---|---|---|---|---|',
                  '| thinking-content-safety-org-design | 组织设计思考 | 当前推演 | effective | 2026-08-04 |',
                ].join('\\n');
                const result = vm.runInContext(`JSON.stringify({{
                  nodes: parseIndex(${{JSON.stringify(index)}}),
                  dir: nodeDir('thinking-content-safety-org-design'),
                  typeOrder: TYPE_ORDER,
                  label: TYPE_LABEL.thinking,
                  help: TYPE_HELP.thinking,
                  linkedIds: '关联 thinking-content-safety-org-design'.match(ID_RE),
                }})`, context);
                process.stdout.write(result);
                """
            )
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            [
                {
                    "id": "thinking-content-safety-org-design",
                    "title": "组织设计思考",
                    "tldr": "当前推演",
                    "status": "effective",
                    "last_verified": "2026-08-04",
                    "type": "thinking",
                }
            ],
            result["nodes"],
        )
        self.assertEqual("thinkings", result["dir"])
        self.assertIn("thinking", result["typeOrder"])
        self.assertEqual("思考", result["label"])
        self.assertIn("自然语言认知", result["help"])
        self.assertEqual(
            ["thinking-content-safety-org-design"], result["linkedIds"]
        )

    def test_viewer_boots_against_minimal_dom_and_empty_index(self):
        completed = self.run_node(
            textwrap.dedent(
                f"""\
                const fs = require('fs');
                const vm = require('vm');
                const html = fs.readFileSync({json.dumps(str(VIEWER))}, 'utf8');
                const match = html.match(/<script>\\s*([\\s\\S]*?)<\\/script>/);
                if (!match) throw new Error('inline viewer script not found');
                const source = match[1].replace(
                  /\\nboot\\(\\);\\s*$/,
                  '\\nglobalThis.__viewerBoot = boot;'
                );

                const elements = new Map();
                function element(id = '') {{
                  const value = {{
                    id,
                    dataset: {{}},
                    style: {{ setProperty() {{}} }},
                    classList: {{
                      add() {{}}, remove() {{}}, toggle() {{}}, contains() {{ return false; }}
                    }},
                    addEventListener() {{}},
                    removeEventListener() {{}},
                    querySelectorAll() {{ return []; }},
                    querySelector() {{ return null; }},
                    appendChild() {{}},
                    replaceWith() {{}},
                    setAttribute() {{}},
                    getAttribute() {{ return null; }},
                    focus() {{}},
                    select() {{}},
                    scrollIntoView() {{}},
                    closest() {{ return null; }},
                    matches() {{ return false; }},
                    getBoundingClientRect() {{ return {{ left: 0, width: 244 }}; }},
                    innerHTML: '',
                    textContent: '',
                    hidden: false,
                    disabled: false,
                    value: '',
                    parentElement: null,
                  }};
                  return new Proxy(value, {{
                    get(target, key) {{
                      if (key in target) return target[key];
                      return () => undefined;
                    }},
                    set(target, key, next) {{
                      target[key] = next;
                      return true;
                    }},
                  }});
                }}
                function getElement(id) {{
                  if (!elements.has(id)) elements.set(id, element(id));
                  return elements.get(id);
                }}

                const warnings = [];
                const errors = [];
                const document = {{
                  documentElement: element('html'),
                  body: element('body'),
                  getElementById: getElement,
                  querySelectorAll() {{ return []; }},
                  querySelector() {{ return null; }},
                  addEventListener() {{}},
                  createElement: element,
                  createTextNode(text) {{ return {{ textContent: text }}; }},
                  createDocumentFragment() {{ return element('fragment'); }},
                  createTreeWalker() {{ return {{ nextNode() {{ return false; }} }}; }},
                }};
                const context = {{
                  console: {{
                    log() {{}},
                    warn(...args) {{ warnings.push(args.join(' ')); }},
                    error(...args) {{ errors.push(args.join(' ')); }},
                  }},
                  document,
                  localStorage: {{
                    getItem() {{ return null; }},
                    setItem() {{}},
                  }},
                  location: {{ hash: '' }},
                  history: {{ pushState() {{}}, replaceState() {{}} }},
                  navigator: {{ clipboard: {{ writeText: async () => undefined }} }},
                  fetch: async () => ({{
                    ok: true,
                    status: 200,
                    text: async () => '| id | title | type |\\n|---|---|---|\\n',
                  }}),
                  DOMParser: class {{
                    parseFromString() {{ return {{ querySelectorAll() {{ return []; }} }}; }}
                  }},
                  NodeFilter: {{ SHOW_TEXT: 4 }},
                  setTimeout,
                  clearTimeout,
                  requestAnimationFrame(callback) {{ callback(0); }},
                  decodeURIComponent,
                  encodeURIComponent,
                }};
                context.window = context;
                context.window.open = () => undefined;
                context.window.addEventListener = () => undefined;
                context.window.setTimeout = setTimeout;
                context.window.markdownit = () => ({{
                  render(value) {{ return String(value || ''); }},
                  renderInline(value) {{ return String(value || ''); }},
                }});
                vm.createContext(context);

                (async () => {{
                  vm.runInContext(fs.readFileSync({json.dumps(str(ROOT / 'viewer/index-parser.js'))}, 'utf8'), context);
                  vm.runInContext(source, context, {{ filename: 'viewer/index.html' }});
                  await vm.runInContext('__viewerBoot()', context);
                  await new Promise(resolve => setTimeout(resolve, 0));
                  const result = {{
                    home: getElement('reader-body').innerHTML,
                    brand: getElement('brand-sub').textContent,
                    scanStatus: getElement('scan-status').textContent,
                    theme: document.documentElement.dataset.theme,
                    density: document.documentElement.dataset.density,
                    errors,
                    warnings,
                  }};
                  process.stdout.write(JSON.stringify(result));
                }})().catch(error => {{
                  console.error(error);
                  process.exitCode = 1;
                }});
                """
            )
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertIn("开始使用知识库", result["home"])
        self.assertIn("本地 Markdown", result["home"])
        self.assertEqual("0 个节点 · 知识内容只读", result["brand"])
        self.assertEqual("0 个节点 · 索引就绪", result["scanStatus"])
        self.assertEqual("light", result["theme"])
        self.assertEqual("normal", result["density"])
        self.assertEqual([], result["errors"])
        self.assertEqual([], result["warnings"])


if __name__ == "__main__":
    unittest.main()
