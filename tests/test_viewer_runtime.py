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
                  vm.runInContext(source, context, {{ filename: 'viewer/index.html' }});
                  await vm.runInContext('__viewerBoot()', context);
                  await new Promise(resolve => setTimeout(resolve, 0));
                  const result = {{
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
        self.assertEqual("0 个节点 · 只读", result["brand"])
        self.assertEqual("0 个节点 · 索引就绪", result["scanStatus"])
        self.assertEqual("light", result["theme"])
        self.assertEqual("normal", result["density"])
        self.assertEqual([], result["errors"])
        self.assertEqual([], result["warnings"])


if __name__ == "__main__":
    unittest.main()
