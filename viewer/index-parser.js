/* Pure INDEX parser; no KB writes or network access. */
function parseIndex(text) {
  const out = [];
  let cur = null;
  const seen = new Set();
  for (const raw of text.replace(/\r\n/g, '\n').split('\n')) {
    const h = raw.match(/^##\s+.*\(([a-z]+)\)\s*$/);
    if (h && ["person", "project", "area", "org", "event", "decision", "reading", "thinking"].includes(h[1])) { cur = h[1]; continue; }
    if (raw.startsWith('## ')) { cur = null; continue; }
    if (!cur) continue;
    const m = raw.match(/^\|(.+)\|$/);
    if (!m) continue;
    const c = m[1].split('|').map(s => s.trim());
    if (c[0].toLowerCase() === 'id' || /^:?-+:?$/.test(c[0])) continue;
    if (!c[0] || c.length < 5 || !c[0].startsWith(cur + '-') || seen.has(c[0])) continue;
    seen.add(c[0]);
    out.push({
      id: c[0],
      title: c[1] || c[0],
      tldr: c[2] || '',
      status: c[3] || 'current',
      last_verified: c[4] || '',
      type: cur,
    });
  }
  return out;
}


function inspectIndex(text) {
  const nodes = parseIndex(text);
  const rows = text.split(/\r?\n/).filter(line => /^\|/.test(line) && !/^\|\s*(?:id\s*\||[-: ]+\|)/i.test(line));
  const recognized = /^##\s+.*\((?:person|project|area|org|event|decision|reading|thinking)\)\s*$/m.test(text);
  const issue = rows.length > nodes.length ? 'INDEX 部分内容无法解析' : (!nodes.length && text.split(/\r?\n/).some(line => line.trim() && !/^\s*\|/.test(line)) && !recognized ? 'INDEX 格式不兼容' : '');
  return { nodes, issue };
}
