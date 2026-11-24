/* ============ QuantPilot H5 · Mock 数据层 ============
 * 前端 demo 用：生成看起来真实的回测结果与净值曲线。
 * 后端接通后，这一层会被 API 返回的标准化结果替换（schema 见 docs/contracts.md）。
 * 所有数据纯前端生成，不联网、不涉及真实资金。
 */
(function (global) {
  'use strict';

  var Storage = global.QPStorage;
  var DAYS = 1220;                                   // 约 5 年交易日
  var DATES = Storage ? Storage.tradingDates(DAYS) : [];

  // 7 个模板：6 个选股规则 + 1 个 AI 预测模型。
  // frame 是「点一下就走」的语义帧，点卡片直接进确认页，不需要用户再打字。
  var TEMPLATES = [
    { icon: '🤖', name: 'AI 智能预测', desc: '机器学习自动找规律，预测未来涨跌', color: '#1a56db', hot: true,
      intent: '用机器学习模型预测沪深300未来收益，选预测最强的20只，每月调仓',
      frame: { universe: 'csi300', styles: ['model'], topk: 20, rebalance: 'month' },
      seed: 95 },
    { icon: '🚀', name: '追涨强势股', desc: '找最近涨得最猛的，强者恒强', color: '#e0392f',
      intent: '沪深300里，最近一个月涨得最猛的20只股票，每月换一次',
      frame: { universe: 'csi300', styles: ['momentum'], topk: 20, rebalance: 'month' },
      seed: 11 },
    { icon: '🩹', name: '抄底超跌股', desc: '跌得最狠的博反弹，每周调仓', color: '#0f9d58',
      intent: '中证500里，最近跌得最狠的30只股票，做超跌反弹，每周调仓',
      frame: { universe: 'csi500', styles: ['reversal'], topk: 30, rebalance: 'week' },
      seed: 23 },
    { icon: '🎯', name: '又强又稳', desc: '高动量+低波动，强而不疯', color: '#1a56db',
      intent: '沪深300里，又强又稳的30只股票，高动量低波动，按月调仓',
      frame: { universe: 'csi300', styles: ['momentum', 'low_volatility'], topk: 30, rebalance: 'month' },
      seed: 37 },
    { icon: '🔥', name: '强势又有量', desc: '在涨的里面挑成交最活跃的', color: '#7c3aed',
      intent: '沪深300里，最近既在涨、成交又明显放大的20只股票，每月调仓',
      frame: { universe: 'csi300', styles: ['momentum', 'volume'], topk: 20, rebalance: 'month' },
      seed: 51 },
    { icon: '🛡️', name: '低波动防守', desc: '选最稳的，波动小睡得着', color: '#0891b2',
      intent: '中证500里波动最小的20只股票，每月调仓',
      frame: { universe: 'csi500', styles: ['low_volatility'], topk: 20, rebalance: 'month' },
      seed: 67 },
    { icon: '📈', name: '放量突破', desc: '成交量突然放大的强势股', color: '#ea580c',
      intent: '沪深300里最近成交量明显放大的20只股票，每周调仓',
      frame: { universe: 'csi300', styles: ['volume'], topk: 20, rebalance: 'week' },
      seed: 83 }
  ];

  // 种子随机数（可复现），保证同一策略每次生成相同曲线
  function mulberry32(seed) {
    return function () {
      seed |= 0; seed = seed + 0x6D2B79F5 | 0;
      var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }

  /**
   * 生成一条回测结果。返回结构与后端标准化 schema 对齐：
   * { name, intent, metrics, series:{nav, drawdown, benchmark_nav}, spec, code, split }
   */
  function generateBacktest(cfg) {
    var rnd = mulberry32(cfg.seed || 42);
    var dates = DATES;
    var n = dates.length;

    // 两段系统性回撤区间（模拟熊市），策略与基准都会经历
    function inBear(i) { return (i > 300 && i < 390) || (i > 820 && i < 900); }

    // 基准：温和上涨 + 波动 + 熊市下跌
    var bench = [1.0];
    for (var b = 1; b < n; b++) {
      var drift = 0.00042;
      var shock = (rnd() - 0.5) * 0.020;
      var correction = inBear(b) ? -0.0022 : 0;
      bench.push(Math.max(0.5, bench[b - 1] * (1 + drift + shock + correction)));
    }

    // 策略：根据方向给不同的超额收益特征；低波策略波动更小、更抗跌
    var isLowVol = cfg.signal && (cfg.signal.indexOf('低波') >= 0 || cfg.signal.indexOf('稳') >= 0);
    var annualBoost = isLowVol ? 0.00022 : (cfg.direction === 'asc' ? 0.00016 : 0.00030);
    var vol = isLowVol ? 0.008 : 0.015;
    // 低波策略跟随基准的比例更低（更独立、更少随大盘波动）
    var beta = isLowVol ? 0.16 : 0.32;
    var nav = [1.0];
    for (var s = 1; s < n; s++) {
      var alpha = annualBoost + (rnd() - 0.5) * vol;
      // 策略在熊市中比基准抗跌，低波策略防御性更强
      var defense = inBear(s) ? (isLowVol ? 0.0016 : 0.0009) : 0;
      nav.push(Math.max(0.5, nav[s - 1] * (1 + alpha + defense + (bench[s] - bench[s - 1]) * beta)));
    }

    // 归一化到合理的 5 年收益区间（年化约 9%~19%）
    var targetFinal = 1.55 + (cfg.seed % 7) * 0.075; // 1.55 ~ 2.00（5年）
    var scale = targetFinal / nav[n - 1];
    for (var k = 0; k < n; k++) nav[k] = nav[k] * Math.pow(scale, k / n);
    var targetBenchFinal = 1.28 + (cfg.seed % 5) * 0.045; // 5年约 6%~10%/年
    var bscale = targetBenchFinal / bench[n - 1];
    for (var k2 = 0; k2 < n; k2++) bench[k2] = bench[k2] * Math.pow(bscale, k2 / n);

    // 回撤序列
    var peak = nav[0], drawdown = [];
    for (var j = 0; j < n; j++) {
      if (nav[j] > peak) peak = nav[j];
      drawdown.push(nav[j] / peak - 1);
    }

    // 指标（用数组算，保证与曲线一致）
    var totalRet = nav[n - 1] - 1;
    var years = n / 244;
    var annual = Math.pow(1 + totalRet, 1 / years) - 1;
    var rets = [];
    for (var r = 1; r < n; r++) rets.push(nav[r] / nav[r - 1] - 1);
    var mean = rets.reduce(function (a, x) { return a + x; }, 0) / rets.length;
    var variance = rets.reduce(function (a, x) { return a + (x - mean) * (x - mean); }, 0) / rets.length;
    var sharpe = mean / Math.sqrt(variance) * Math.sqrt(244);
    var maxDD = Math.min.apply(null, drawdown);
    var wins = rets.filter(function (x) { return x > 0; }).length;
    var winRate = wins / rets.length;
    var turnover = 0.28 + (cfg.seed % 5) * 0.03;
    var calmar = annual / Math.abs(maxDD);

    return {
      name: cfg.name || '我的量化策略',
      intent: cfg.intent || '',
      metrics: {
        annual_return: annual,
        sharpe: sharpe,
        max_drawdown: maxDD,
        win_rate: winRate,
        turnover: turnover,
        calmar: calmar
      },
      series: {
        nav: nav.map(function (v, i2) { return { date: dates[i2], value: v }; }),
        benchmark_nav: bench.map(function (v, i3) { return { date: dates[i3], value: v }; }),
        drawdown: drawdown.map(function (v, i4) { return { date: dates[i4], value: v }; })
      },
      split: {
        in_sample: { annual_return: annual * (1.08 + (cfg.seed % 3) * 0.04), sharpe: sharpe * 1.12 },
        out_sample: { annual_return: annual * (0.78 + (cfg.seed % 3) * 0.05), sharpe: sharpe * 0.86 }
      },
      // 收益贡献分解（对标 PandaAI「正向贡献/负向贡献」）
      contributions: buildContributions(rnd, cfg),
      // 当前持仓分布（对标 PandaAI「持仓合约分布」）
      holdings: buildHoldings(rnd, cfg),
      spec: cfg.spec || {}
    };
  }

  // 收益贡献：几只代表个股的盈亏（红涨绿跌）
  var STOCK_NAMES = ['贵州茅台', '宁德时代', '招商银行', '中芯国际', '五粮液', '比亚迪',
    '中国平安', '立讯精密', '紫金矿业', '长江电力', '恒瑞医药', '中信证券'];
  function buildContributions(rnd, cfg) {
    var n = 5 + Math.floor(rnd() * 3);
    var used = {}, out = [];
    for (var i = 0; i < n; i++) {
      var idx = Math.floor(rnd() * STOCK_NAMES.length);
      while (used[idx]) idx = (idx + 1) % STOCK_NAMES.length;
      used[idx] = 1;
      var positive = rnd() > 0.35;
      var ret = positive ? (0.02 + rnd() * 0.14) : -(0.01 + rnd() * 0.09);
      out.push({ name: STOCK_NAMES[idx], ret: ret, amount: Math.round(ret * 1e6) });
    }
    return out.sort(function (a, b) { return b.ret - a.ret; });
  }

  // 持仓分布：前几大持仓及占比
  function buildHoldings(rnd, cfg) {
    var total = Math.min(cfg.hold || 20, 6);
    var used = {}, out = [], weight = 0;
    for (var i = 0; i < total; i++) {
      var idx = Math.floor(rnd() * STOCK_NAMES.length);
      while (used[idx]) idx = (idx + 1) % STOCK_NAMES.length;
      used[idx] = 1;
      var w = (0.04 + rnd() * 0.06);
      weight += w;
      var ret = (rnd() - 0.4) * 0.2;
      out.push({ name: STOCK_NAMES[idx], weight: w, ret: ret });
    }
    return out.sort(function (a, b) { return b.weight - a.weight; });
  }

  // 因子中心数据（对标 PandaAI 因子库 / IC 榜）——与后端 DataFoundry 种子资产对齐
  // style 用于「用它做策略」：点一下直接编译成策略帧，用户不需要理解因子本身
  var FACTORS = [
    { name: '20日动量', code: 'momentum_20d', style: 'momentum', ic: 0.041, ir: 0.50, dir: 'desc', tags: ['动量', '量价'] },
    { name: '一年价格低位', code: 'low_position_1y', style: 'price_low', ic: 0.024, ir: 0.35, dir: 'desc', tags: ['低位', '量价'] },
    { name: '60日动量', code: 'momentum_60d', style: 'momentum', ic: 0.035, ir: 0.39, dir: 'desc', tags: ['动量', '量价'] },
    { name: '5日反转', code: 'reversal_5d', style: 'reversal', ic: 0.028, ir: 0.37, dir: 'desc', tags: ['反转', '量价'] },
    { name: '20日波动率', code: 'volatility_20d', style: 'low_volatility', ic: 0.022, ir: 0.37, dir: 'asc', tags: ['低波', '风险'] },
    { name: '量比', code: 'volume_ratio', style: 'volume', ic: 0.019, ir: 0.27, dir: 'desc', tags: ['成交量', '量价'] }
  ];

  // 首次进入从 localStorage 恢复；storage 不可用（隐私模式等）时退化为纯内存。
  // restored === null 表示"从没存过"，app.js 据此决定是否播撒 seed 数据。
  var restored = Storage ? Storage.load(DATES) : null;
  var store = restored || [];

  function get(id) {
    return store.find(function (x) { return x.id === id; }) || null;
  }

  function persist() {
    return Storage ? Storage.save(store) : -1;
  }

  function nextId() {
    var id, tries = 0;
    do {
      id = 'STR-' + String(Date.now()).slice(-6)
        + ('0' + Math.floor(Math.random() * 100)).slice(-2);
      tries++;
    } while (store.some(function (x) { return x.id === id; }) && tries < 12);
    return id;
  }

  /** 任意标准化结果入库——mock 生成的和真实 API 返回的都走这里 */
  function put(result) {
    if (!result) return result;
    if (!result.id) result.id = nextId();
    if (!(result.created instanceof Date)) {
      result.created = new Date(result.created || Date.now());
    }
    if (typeof result.favorite !== 'boolean') result.favorite = false;
    var idx = -1;
    for (var i = 0; i < store.length; i++) {
      if (store[i].id === result.id) { idx = i; break; }
    }
    if (idx >= 0) store[idx] = result; else store.unshift(result);
    persist();
    return result;
  }

  function create(cfg) {
    var result = generateBacktest(cfg);
    result.kind = cfg.kind || 'expression';
    result.modelInfo = cfg.modelInfo || null;
    // 编译出的参数卡与「替你做的假设」，结果页「我是这么理解的」用
    result.plan = cfg.plan || null;
    return put(result);
  }

  function toggleFavorite(id) {
    var r = get(id);
    if (!r) return null;
    r.favorite = !r.favorite;
    persist();
    return r;
  }

  global.MockData = {
    TEMPLATES: TEMPLATES,
    FACTORS: FACTORS,
    generateBacktest: generateBacktest,
    create: create,
    put: put,
    toggleFavorite: toggleFavorite,
    persist: persist,
    restored: restored !== null,
    store: store,
    get: get,
    all: function () { return store.slice(); }
  };
})(window);
