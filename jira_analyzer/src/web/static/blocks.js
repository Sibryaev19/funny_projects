/* Рендереры блоков метрик — ОДИН код для web-интерфейса и HTML-отчёта.
 *
 * API: blocks.renderAll(rootEl, payload)
 *   payload = {project, results: [{metric_id, label, description, params_label,
 *                                  blocks: [...]}], errors: [{metric_id, error}]}
 *
 * Каждый блок метрики — карточка с заголовком и кнопкой сворачивания до одного
 * названия (для длинных таблиц). Отдельный блок может стартовать свёрнутым
 * (collapsed: true в спеке). Графики — echarts (ленивая инициализация при
 * разворачивании), таблицы/списки — чистый DOM без innerHTML для данных.
 */
(function () {
  'use strict';

  let charts = [];
  let resizeBound = false;

  const CSS = `
.blocks-root{display:flex;flex-direction:column;gap:14px}
.metric-card{background:#fff;border:1px solid #d5dbe7;border-radius:8px;overflow:hidden}
.metric-head{display:flex;align-items:center;gap:10px;padding:10px 14px;cursor:pointer;user-select:none}
.metric-head:hover{background:#f6f8fc}
.metric-head h3{margin:0;font-size:1.05em;color:#2c3e6b}
.metric-params{color:#999;font-size:.85em;font-style:italic;white-space:nowrap}
.metric-desc{color:#666;font-size:.85em}
.toggle{margin-left:auto;flex:none;border:1px solid #cfd6e4;background:#eef2fa;border-radius:6px;
  width:26px;height:26px;cursor:pointer;font-size:.9em;color:#2c3e6b;padding:0}
.metric-body{padding:2px 14px 14px;display:flex;flex-direction:column;gap:10px}
.metric-card.collapsed .metric-body{display:none}
.block.collapsed>.block-content{display:none}
.block-title{margin:8px 0 2px;font-weight:600;color:#1a1a2e}
.block.collapsed>.block-head{cursor:pointer}
.block.collapsed>.block-head>.block-title::before{content:'▸ ';color:#888}
.block>.block-head>.block-title::before{content:'▾ ';color:#888}
.block-note{color:#888;font-size:.82em;margin:0 0 6px}
.chart{width:100%;height:340px}
.table-wrap{max-height:480px;overflow:auto;border:1px solid #d5dbe7;border-radius:6px}
table.blocks{border-collapse:collapse;font-size:.9em;min-width:60%}
table.blocks th,table.blocks td{border:1px solid #cfd6e4;padding:4px 9px;text-align:left;white-space:nowrap}
table.blocks th{background:#eef2fa;position:sticky;top:0}
table.blocks tr:nth-child(even) td{background:#f6f8fc}
td.num{text-align:right;font-variant-numeric:tabular-nums}
ul.blocks{margin:4px 0;padding-left:22px}
.err{color:#c0392b;font-size:.9em}
`;

  function ensureStyles() {
    if (!document.getElementById('blocks-css')) {
      const s = document.createElement('style');
      s.id = 'blocks-css';
      s.textContent = CSS;
      document.head.appendChild(s);
    }
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function disposeCharts() {
    charts.forEach(c => { try { c.dispose(); } catch (e) { /* фигура уже снята */ } });
    charts = [];
  }

  // ---------------- карточка метрики ----------------

  function renderMetric(res) {
    const card = el('section', 'metric-card');
    const head = el('div', 'metric-head');
    head.appendChild(el('h3', null, res.label || res.metric_id));
    if (res.params_label) head.appendChild(el('span', 'metric-params', res.params_label));
    if (res.description) head.appendChild(el('span', 'metric-desc', res.description));
    const btn = el('button', 'toggle');
    const body = el('div', 'metric-body');
    (res.blocks || []).forEach(b => body.appendChild(renderBlock(b)));

    const sync = () => { btn.textContent = card.classList.contains('collapsed') ? '▸' : '▾'; };
    const toggle = () => {
      card.classList.toggle('collapsed');
      sync();
      if (!card.classList.contains('collapsed')) initChartsIn(card);
    };
    btn.addEventListener('click', ev => { ev.stopPropagation(); toggle(); });
    head.addEventListener('click', toggle);
    sync();
    head.appendChild(btn);
    card.appendChild(head);
    card.appendChild(body);
    requestAnimationFrame(() => {
      if (!card.classList.contains('collapsed')) initChartsIn(card);
    });
    return card;
  }

  // ---------------- отдельный блок ----------------

  function renderBlock(b) {
    const wrap = el('div', 'block' + (b.collapsed ? ' collapsed' : ''));
    const head = el('div', 'block-head');
    if (b.title) head.appendChild(el('div', 'block-title', b.title));
    if (b.note) head.appendChild(el('div', 'block-note', b.note));
    if (head.childNodes.length) {
      if (b.title) {  // клик по заголовку сворачивает/разворачивает блок
        head.addEventListener('click', () => {
          wrap.classList.toggle('collapsed');
          if (!wrap.classList.contains('collapsed')) initChartsIn(wrap);
        });
      }
      wrap.appendChild(head);
    }
    const content = el('div', 'block-content');
    if (b.type === 'table') content.appendChild(renderTable(b));
    else if (b.type === 'list') content.appendChild(renderList(b));
    else if (b.type === 'bar' || b.type === 'line' || b.type === 'pie')
      content.appendChild(renderChart(b));
    wrap.appendChild(content);
    return wrap;
  }

  function renderTable(b) {
    const wrap = el('div', 'table-wrap');
    const t = el('table', 'blocks');
    const thead = el('thead'), trh = el('tr');
    (b.columns || []).forEach(c => trh.appendChild(el('th', null, c)));
    thead.appendChild(trh);
    t.appendChild(thead);
    const tbody = el('tbody');
    const aligns = b.align || [];
    const ncols = (b.columns || []).length;
    (b.rows || []).forEach(r => {
      const tr = el('tr');
      for (let i = 0; i < ncols; i++) {
        const v = r[i] === undefined ? null : r[i];
        const numeric = typeof v === 'number';
        tr.appendChild(el('td', (numeric || aligns[i] === 'right') ? 'num' : null,
                          v === null ? '' : v));
      }
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    wrap.appendChild(t);
    return wrap;
  }

  function renderList(b) {
    const ul = el('ul', 'blocks');
    (b.items || []).forEach(it => ul.appendChild(el('li', null, it)));
    return ul;
  }

  // ---------------- графики (echarts) ----------------

  function renderChart(b) {
    const div = el('div', 'chart');
    div.style.height = chartHeight(b) + 'px';
    div.dataset.pending = '1';
    div._option = chartOption(b);
    return div;
  }

  function initChartsIn(root) {
    root.querySelectorAll('.chart[data-pending]').forEach(div => {
      delete div.dataset.pending;
      if (typeof echarts === 'undefined') {
        div.textContent = 'echarts не загружен';
        return;
      }
      const ch = echarts.init(div);
      ch.setOption(div._option);
      charts.push(ch);
    });
  }

  function chartHeight(b) {
    if (b.type === 'pie') return 360;
    const n = (b.labels || b.x || []).length;
    if (b.type === 'bar' && b.horizontal) return Math.min(720, Math.max(220, 34 * n + 70));
    return 340;
  }

  const GRID = { left: 8, right: 24, top: 32, bottom: 8, containLabel: true };
  const AXIS_TIP = { trigger: 'axis', axisPointer: { type: 'shadow' } };

  function chartOption(b) {
    if (b.type === 'pie') {
      return {
        tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
        legend: { type: 'scroll', top: 0 },
        series: [{
          type: 'pie', radius: '62%', center: ['50%', '56%'],
          data: (b.items || []).map(i => ({ name: i.name, value: i.value })),
          label: { formatter: '{b}: {d}%' },
        }],
      };
    }
    const series = (b.series || []).map(sr => ({
      name: sr.name, type: b.type, data: sr.values,
      stack: b.type === 'bar' && b.stacked ? 'one' : undefined,
      smooth: b.type === 'line' ? false : undefined,
    }));
    if (b.type === 'line') {
      return {
        tooltip: AXIS_TIP, legend: { top: 0 }, grid: GRID,
        xAxis: { type: 'category', data: b.x,
                 axisLabel: { hideOverlap: true, width: 90, overflow: 'truncate' } },
        yAxis: { type: 'value' },
        series,
      };
    }
    if (b.horizontal) {
      return {
        tooltip: AXIS_TIP, legend: { top: 0 }, grid: GRID,
        xAxis: { type: 'value' },
        yAxis: { type: 'category', data: b.labels, inverse: true,
                 axisLabel: { width: 150, overflow: 'truncate' } },
        series,
      };
    }
    return {
      tooltip: AXIS_TIP, legend: { top: 0 }, grid: GRID,
      xAxis: { type: 'category', data: b.labels,
               axisLabel: { rotate: (b.labels || []).length > 8 ? 30 : 0,
                            hideOverlap: true, width: 100, overflow: 'truncate' } },
      yAxis: { type: 'value' },
      series,
    };
  }

  // ---------------- публичный API ----------------

  function renderAll(root, payload) {
    ensureStyles();
    disposeCharts();
    root.classList.add('blocks-root');
    (payload.results || []).forEach(res => root.appendChild(renderMetric(res)));
    (payload.errors || []).forEach(e =>
      root.appendChild(el('div', 'err',
        'Метрика «' + (e.metric_id || '?') +
        (e.params_label ? ' · ' + e.params_label : '') +
        '» не построилась: ' + (e.error || ''))));
    if (!resizeBound) {
      resizeBound = true;
      window.addEventListener('resize', () => charts.forEach(c => c.resize()));
    }
  }

  window.blocks = { renderAll };
})();
