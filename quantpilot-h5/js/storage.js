/**
 * 本地持久化：把回测历史留在浏览器里，刷新不丢。
 *
 * 设计约束：
 * 1. localStorage 不是可靠的——隐私模式、file:// 限制、配额满都可能抛异常。
 *    所有读写都包 try/catch，失败时静默退回内存，页面绝不因为存储挂掉。
 * 2. 每条结果带 3 × 1220 个净值点，直接 JSON.stringify 约 165 KB/条。
 *    只存 value（date 由共享交易日历重建），压到约 30 KB/条，20 条 < 600 KB。
 * 3. 坏数据不能让历史页崩。反序列化时逐条校验，坏的一条丢掉，其余照常显示。
 * 4. key 带版本号。将来结构变了换 v2，老数据自然作废，不用写迁移。
 */
(function (global) {
  'use strict';

  var KEY = 'qp.h5.results.v1';
  var MAX_ITEMS = 20;
  var VERSION = 1;

  /** 共享交易日历：所有 mock 结果用同一组日期，无须逐条存 date。 */
  function tradingDates(n) {
    var dates = [];
    var d = new Date(2020, 0, 2);
    while (dates.length < n) {
      d.setDate(d.getDate() + 1);
      if (d.getDay() !== 0 && d.getDay() !== 6) dates.push(fmtDate(d));
    }
    return dates;
  }

  function fmtDate(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }

  function store() {
    try {
      return global.localStorage || null;
    } catch (e) {
      return null;
    }
  }

  var _dates = null;
  function sharedDates() {
    if (!_dates) _dates = tradingDates(1220);
    return _dates;
  }

  function round(v) {
    var x = Number(v);
    return isFinite(x) ? Math.round(x * 1e8) / 1e8 : 0;
  }

  /**
   * 只有日期确实等于共享日历时才压成 value[]。
   * 真实 API 结果的日期区间可能不同——那种情况必须原样存 {date,value}，
   * 否则刷新后日期会被套上 mock 日历，净值曲线就对不上了。
   */
  function packSeries(series) {
    if (!series) return { mode: 'shared', nav: [], benchmark_nav: [], drawdown: [] };
    var nav = series.nav;
    var cal = sharedDates();
    var shared = Array.isArray(nav) && nav.length > 0 && nav[0] && nav[0].date === cal[0];
    if (shared) {
      var last = nav[nav.length - 1];
      if (!last || last.date !== cal[Math.min(nav.length, cal.length) - 1]) shared = false;
    }
    if (!shared) {
      return {
        mode: 'inline',
        nav: series.nav || [],
        benchmark_nav: series.benchmark_nav || [],
        drawdown: series.drawdown || []
      };
    }
    function vals(arr) {
      if (!Array.isArray(arr)) return [];
      return arr.map(function (p) { return round(p && p.value); });
    }
    return {
      mode: 'shared',
      nav: vals(nav),
      benchmark_nav: vals(series.benchmark_nav),
      drawdown: vals(series.drawdown)
    };
  }

  function pack(r) {
    return {
      v: VERSION,
      id: String(r.id || ''),
      name: String(r.name || '量化策略'),
      intent: String(r.intent || ''),
      created: r.created instanceof Date ? r.created.toISOString()
        : (r.created ? String(r.created) : new Date().toISOString()),
      favorite: !!r.favorite,
      demo: !!r.demo,
      demoReason: String(r.demoReason || ''),
      kind: String(r.kind || 'expression'),
      modelInfo: r.modelInfo || null,
      plan: r.plan || null,
      task_id: r.task_id || null,
      metrics: r.metrics || {},
      split: r.split || {},
      contributions: r.contributions || [],
      holdings: r.holdings || [],
      spec: r.spec || {},
      values: packSeries(r.series)
    };
  }

  function unpackSeries(values, dates) {
    if (!values || !Array.isArray(values.nav)) {
      return { nav: [], benchmark_nav: [], drawdown: [] };
    }
    if (values.mode === 'inline') {
      return {
        nav: values.nav || [],
        benchmark_nav: values.benchmark_nav || [],
        drawdown: values.drawdown || []
      };
    }
    function rebuild(arr) {
      if (!Array.isArray(arr)) return [];
      var out = [];
      var n = Math.min(arr.length, dates.length);
      for (var i = 0; i < n; i++) out.push({ date: dates[i], value: arr[i] });
      return out;
    }
    return {
      nav: rebuild(values.nav),
      benchmark_nav: rebuild(values.benchmark_nav),
      drawdown: rebuild(values.drawdown)
    };
  }

  function unpack(item, dates) {
    if (!item || !item.id) return null;
    var d = new Date(item.created);
    if (isNaN(d.getTime())) d = new Date();
    return {
      id: String(item.id),
      name: item.name || '量化策略',
      intent: item.intent || '',
      created: d,
      favorite: !!item.favorite,
      demo: !!item.demo,
      demoReason: item.demoReason || '',
      kind: item.kind || 'expression',
      modelInfo: item.modelInfo || null,
      plan: item.plan || null,
      task_id: item.task_id || null,
      metrics: item.metrics || {},
      split: item.split || {},
      contributions: item.contributions || [],
      holdings: item.holdings || [],
      spec: item.spec || {},
      series: unpackSeries(item.values, dates)
    };
  }

  /**
   * 读回历史。返回 null 表示"从未存过"（用于决定是否播撒 seed）；
   * 返回 [] 表示存过但已清空。坏数据逐条跳过，不抛异常。
   */
  function load(dates) {
    var ls = store();
    if (!ls) return null;
    var raw;
    try {
      raw = ls.getItem(KEY);
    } catch (e) {
      return null;
    }
    if (raw === null || raw === undefined || raw === '') return null;

    var items;
    try {
      items = JSON.parse(raw);
    } catch (e) {
      console.warn('[QP] 本地历史损坏，已忽略:', e);
      return null;
    }
    if (!Array.isArray(items)) return null;

    var out = [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      if (!item || item.v !== VERSION) continue;       // 版本不对，丢弃
      var r = unpack(item, dates || []);
      if (r) out.push(r);
    }
    return out;
  }

  /** 写回历史。成功返回实际写入条数；失败返回 -1（调用方继续用内存，不打断用户）。 */
  function save(list) {
    var ls = store();
    if (!ls) return -1;
    var items = (list || []).slice(0, MAX_ITEMS);
    while (items.length > 0) {
      try {
        ls.setItem(KEY, JSON.stringify(items.map(pack)));
        return items.length;
      } catch (e) {
        // 配额满：丢掉最老的一条再试。极端情况下退到 0 条，但页面不崩。
        items.shift();
      }
    }
    console.warn('[QP] 本地历史写入失败（存储空间不足或不可写）');
    return -1;
  }

  function clear() {
    var ls = store();
    if (!ls) return;
    try { ls.removeItem(KEY); } catch (e) { /* 忽略 */ }
  }

  global.QPStorage = {
    KEY: KEY,
    MAX_ITEMS: MAX_ITEMS,
    tradingDates: tradingDates,
    load: load,
    save: save,
    clear: clear,
    pack: pack,
    unpack: unpack
  };
})(typeof window !== 'undefined' ? window : globalThis);
