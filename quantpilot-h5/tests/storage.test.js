/**
 * 本地历史持久化回归测试。零依赖：node --test tests/storage.test.js
 *
 * 测的是"刷新页面记录还在"这件事，以及存储不可用时页面不能崩。
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

function fakeWindow(options = {}) {
  const mem = new Map();
  const ls = {
    getItem(k) {
      if (options.throwOnRead) throw new Error('SecurityError');
      return mem.has(k) ? mem.get(k) : null;
    },
    setItem(k, v) {
      if (options.throwOnWrite) throw new Error('QuotaExceededError');
      if (options.quotaBytes && String(v).length > options.quotaBytes) {
        throw new Error('QuotaExceededError');
      }
      mem.set(k, String(v));
    },
    removeItem(k) { mem.delete(k); },
  };
  return { win: { localStorage: ls }, mem, ls };
}

function loadScript(rel, win) {
  global.window = win;
  const abs = path.join(ROOT, rel);
  delete require.cache[require.resolve(abs)];
  require(abs);
}

function freshStorage(options) {
  const ctx = fakeWindow(options);
  loadScript('js/storage.js', ctx.win);
  return { ...ctx, S: ctx.win.QPStorage };
}

function sampleResult(id = 'STR-TEST01') {
  return {
    id,
    name: '测试策略',
    intent: '追涨',
    created: new Date('2026-11-24T10:00:00.000Z'),
    favorite: false,
    demo: true,
    demoReason: '本地演示数据',
    kind: 'expression',
    metrics: { annual_return: 0.123456789, sharpe: 1.2, max_drawdown: -0.2 },
    series: {
      nav: [{ date: '2020-01-03', value: 1 }, { date: '2020-01-06', value: 1.123456789 }],
      benchmark_nav: [{ date: '2020-01-03', value: 1 }, { date: '2020-01-06', value: 1.05 }],
      drawdown: [{ date: '2020-01-03', value: 0 }, { date: '2020-01-06', value: -0.03 }],
    },
    spec: { universe: '沪深300' },
  };
}

test('交易日序列跳过周末，起点固定', () => {
  const { S } = freshStorage();
  const dates = S.tradingDates(10);
  assert.equal(dates.length, 10);
  assert.equal(dates[0], '2020-01-03'); // 2020-01-01 元旦、01-02 初始化后第一根
  for (const d of dates) {
    const day = new Date(d + 'T00:00:00').getDay();
    assert.ok(day !== 0 && day !== 6, `${d} 不该是周末`);
  }
});

test('无存储与坏 JSON 都返回 null，不抛异常', () => {
  const empty = freshStorage();
  assert.equal(empty.S.load([]), null);

  const bad = freshStorage();
  bad.mem.set(bad.S.KEY, '{not json');
  assert.equal(bad.S.load([]), null);
});

test('save → load 往返：数值、日期、收藏、字段都保住', () => {
  const { S, mem } = freshStorage();
  const r = sampleResult();
  r.favorite = true;
  r.plan = { card: { '标的': '沪深300' } };
  assert.equal(S.save([r]), 1);

  // 证明确实存盘了，而且是压缩格式（只存 value，不存 date）
  const raw = JSON.parse(mem.get(S.KEY));
  assert.equal(raw[0].values.nav.length, 2);
  assert.equal(raw[0].values.nav[0], 1);
  assert.equal(raw[0].date, undefined);
  assert.ok(mem.get(S.KEY).length < 2000);

  const dates = ['2020-01-03', '2020-01-06'];
  const back = S.load(dates)[0];
  assert.equal(back.id, r.id);
  assert.equal(back.favorite, true);
  assert.ok(back.created instanceof Date);
  assert.equal(back.created.toISOString(), r.created.toISOString());
  assert.deepEqual(back.series.nav, [
    { date: '2020-01-03', value: 1 },
    { date: '2020-01-06', value: 1.12345679 },
  ]);
  assert.deepEqual(back.plan, r.plan);
  assert.equal(back.metrics.annual_return, r.metrics.annual_return);
});

test('真实结果的日期不等于 mock 日历时，原样保留 date', () => {
  const { S, mem } = freshStorage();
  const r = sampleResult('REAL-1');
  r.series = {
    nav: [{ date: '2024-03-01', value: 1 }, { date: '2024-03-04', value: 1.2 }],
    benchmark_nav: [{ date: '2024-03-01', value: 1 }, { date: '2024-03-04', value: 1.1 }],
    drawdown: [{ date: '2024-03-01', value: 0 }, { date: '2024-03-04', value: -0.01 }],
  };
  S.save([r]);
  const raw = JSON.parse(mem.get(S.KEY));
  assert.equal(raw[0].values.mode, 'inline');
  assert.equal(raw[0].values.nav[0].date, '2024-03-01');

  const back = S.load(['2020-01-03', '2020-01-06'])[0];
  assert.equal(back.series.nav[0].date, '2024-03-01', '不能被套上 mock 日历');
  assert.equal(back.series.nav[1].value, 1.2);
});

test('超过上限只留最新的 20 条', () => {
  const { S, mem } = freshStorage();
  const list = Array.from({ length: 35 }, (_, i) => sampleResult('STR-' + i));
  assert.equal(S.save(list), S.MAX_ITEMS);
  const raw = JSON.parse(mem.get(S.KEY));
  assert.equal(raw.length, 20);
  assert.equal(raw[0].id, 'STR-0');       // 保留最新的
  assert.equal(raw[19].id, 'STR-19');
});

test('坏记录逐条跳过，其余仍可读', () => {
  const { S, mem } = freshStorage();
  mem.set(S.KEY, JSON.stringify([
    { v: 1, id: 'GOOD', name: '好的', values: { nav: [1] } },
    { v: 99, id: 'OLD', name: '旧版本' },
    null,
    { v: 1 },                              // 没 id，跳过
  ]));
  assert.equal(S.load(['D0']).length, 1);
  assert.equal(S.load(['D0'])[0].id, 'GOOD');
});

test('localStorage 不可读写时静默降级：load=null，save=-1', () => {
  const blockedRead = freshStorage({ throwOnRead: true });
  assert.equal(blockedRead.S.load([]), null);

  const blockedWrite = freshStorage({ throwOnWrite: true });
  assert.equal(blockedWrite.S.save([sampleResult()]), -1);
});

test('配额满时逐条丢弃最老的，仍能存下部分', () => {
  // 每条压缩后约 300 字节，限制 500 字节 → 只能容下 1 条
  const { S, mem } = freshStorage({ quotaBytes: 500 });
  const list = [sampleResult('A'), sampleResult('B'), sampleResult('C')];
  const n = S.save(list);
  assert.ok(n > 0 && n < 3, `实际写入 ${n} 条`);
  assert.equal(JSON.parse(mem.get(S.KEY)).length, n);
});

test('MockData：创建后模拟刷新，历史、收藏都在，且不会重复播撒', () => {
  const { win } = freshStorage();
  loadScript('js/storage.js', win);
  loadScript('js/mock.js', win);
  const M1 = win.MockData;
  assert.equal(M1.restored, false);

  const r = M1.create({
    seed: 11, name: '我的策略', intent: '追涨',
    spec: { universe: '沪深300' }, signal: '追涨', direction: 'desc',
  });
  assert.equal(M1.all().length, 1);
  M1.toggleFavorite(r.id);

  // 重新加载两个脚本 = 模拟一次页面刷新
  loadScript('js/mock.js', win);
  const M2 = win.MockData;
  assert.equal(M2.restored, true);
  assert.equal(M2.all().length, 1, '刷新后记录还在');
  assert.equal(M2.get(r.id).favorite, true, '收藏状态也在');
  assert.equal(M2.get(r.id).series.nav.length, 1220, '净值曲线完整重建');
  assert.equal(M2.get(r.id).series.nav[0].date, '2020-01-03');

  M2.put({ id: r.id, name: '改过的名字', created: new Date(), series: M2.get(r.id).series });
  loadScript('js/mock.js', win);
  assert.equal(win.MockData.all().length, 1, '同 id 覆盖而非追加');
  assert.equal(win.MockData.get(r.id).name, '改过的名字');
});

test('存储不可用时 MockData 仍在内存里正常工作', () => {
  const { win } = fakeWindow({ throwOnRead: true, throwOnWrite: true });
  loadScript('js/storage.js', win);
  loadScript('js/mock.js', win);
  const M = win.MockData;
  const r = M.create({ seed: 1, name: '离线策略' });
  assert.equal(M.all().length, 1);
  assert.equal(M.toggleFavorite(r.id).favorite, true);
});
