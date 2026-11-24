/* ============ QuantPilot H5 · 意图语义层 ============
 * 与后端 app/services/intent 一一对应（schemas / rules / recipes / compiler / clarifier）。
 *
 * 这一层存在的意义：用户只说人话，前端也能在没有后端时给出「我理解成了这样」，
 * 让「理解 → 确认 → 回测」这条链路随时可走。
 * 注意：这里**没有因子表达式**。表达式只存在于后端 recipes.py，
 * 前端连看都看不到，也就不会漏到界面上。
 */
(function (global) {
  'use strict';

  var UNIVERSE_CN = { csi300: '沪深300', csi500: '中证500', csi1000: '中证1000', all: '全市场' };
  var REBALANCE_CN = { day: '每个交易日', week: '每周', month: '每月' };
  var DEFAULT_TOPK = 20;
  var DEFAULT_REBALANCE = 'month';
  var DEFAULT_RISK = 'balanced';
  var MAX_SINGLE_WEIGHT = { conservative: 0.05, balanced: 0.10, aggressive: 0.20 };
  var BACKTEST_YEARS = 4;
  var TOPK_CHOICES = [10, 20, 30, 50];

  // 风格配方：只保留用户可见的部分（短名 / 一句人话 / 复述用的说法 / 诚实说明）
  var RECIPES = {
    momentum: {
      label: '追涨', icon: '🚀',
      pitch: '最近 20 个交易日涨得最猛的',
      topic: '最近涨得最猛的',
      hint: '强者恒强，跟着趋势走'
    },
    reversal: {
      label: '抄底', icon: '🩹',
      pitch: '最近 5 个交易日跌得比较多、赌它反弹的',
      topic: '最近跌得比较多、可能反弹的',
      hint: '跌狠了博反弹，波动大'
    },
    low_volatility: {
      label: '求稳', icon: '🛡️',
      pitch: '过去 20 个交易日最平稳、不爱大起大落的',
      topic: '过去一段时间最平稳的',
      hint: '波动小，回撤也小'
    },
    price_low: {
      label: '捡便宜', icon: '💰',
      pitch: '价格比过去一年均价更低的',
      topic: '价格处于相对低位的',
      hint: '买在相对低位',
      caveat: '这里用的是「价格位置」近似，不是市盈率/市净率（暂未接入财务数据）'
    },
    volume: {
      label: '看量', icon: '📈',
      pitch: '最近成交比平时活跃得多的',
      topic: '最近成交最活跃的',
      hint: '有资金进场，成交量先动'
    },
    model: {
      label: '让模型学', icon: '🤖',
      pitch: '让模型自己从历史量价里学规律，再挑股票',
      topic: '由模型自动学习规律来挑的',
      hint: '不靠人定规则，让 AI 自己找',
      exclusive: true
    }
  };

  // 向导与澄清卡片里的顺序（模型放最后，因为它是另一条链路）
  var STYLE_ORDER = ['momentum', 'reversal', 'low_volatility', 'price_low', 'volume', 'model'];
  var CLARIFY_STYLES = ['momentum', 'reversal', 'low_volatility', 'price_low'];

  // 每个风格的关键词。命中即计入，不做互斥（"又便宜又稳"是合法的组合）
  var STYLE_KEYWORDS = {
    momentum: ['涨得猛', '涨得最', '涨幅', '追涨', '又强', '强者', '强势', '动量', '趋势', '新高', '领涨', '涨的', '上涨', '热门'],
    reversal: ['抄底', '超跌', '跌得多', '跌得最', '跌的', '下跌', '反弹', '反转', '回撤大', '跌下来'],
    low_volatility: ['稳', '波动小', '低波', '别大起大落', '平稳', '回撤小', '风险小', '保守', '不刺激'],
    volume: ['放量', '成交活跃', '有量', '量能', '换手', '活跃', '成交量'],
    price_low: ['便宜', '低价', '低位', '估值低', '划算'],
    model: ['机器学习', 'ai', '预测', '智能', '训练', '自动学习', '让模型', '深度学习', '神经网络']
  };

  var NEGATIONS = ['不要', '不用', '别', '避开', '除了'];

  // 当前数据做不到的诉求：如实告知，不拿相近的东西冒充
  var UNSUPPORTED_TERMS = {
    'roe': '净资产收益率', '市盈率': '市盈率', '市净率': '市净率', 'pe': '市盈率', 'pb': '市净率',
    '财务': '财务指标', '营收': '营收', '利润': '利润', '净利润': '净利润', '北向': '北向资金',
    '龙虎榜': '龙虎榜', '新闻': '新闻舆情', '研报': '研报', '情绪': '市场情绪',
    '股息': '股息率', '分红': '分红', '涨停': '涨停板数据'
  };

  // ---------- 工具 ----------
  function negated(text, keyword) {
    var idx = text.indexOf(keyword);
    if (idx <= 0) return false;
    var window_ = text.slice(Math.max(0, idx - 4), idx);
    return NEGATIONS.some(function (n) { return window_.indexOf(n) >= 0; });
  }

  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fmtDate(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }

  function dateRange(today) {
    var end = today || new Date();
    var start = new Date(end.getFullYear() - BACKTEST_YEARS, end.getMonth(), end.getDate());
    return { start: fmtDate(start), end: fmtDate(end) };
  }

  function recipe(style) { return RECIPES[style] || RECIPES.momentum; }

  // ---------- 规则解析 ----------
  function detectUniverse(text) {
    if (text.indexOf('中证1000') >= 0 || text.indexOf('1000') >= 0) return 'csi1000';
    if (text.indexOf('中证500') >= 0 || text.indexOf('500') >= 0) return 'csi500';
    if (text.indexOf('沪深300') >= 0 || text.indexOf('300') >= 0) return 'csi300';
    if (/全市场|所有股票|全部A股|a股/i.test(text)) return 'all';
    return null;
  }

  function detectTopk(text) {
    var patterns = [/(\d+)\s*只/, /前\s*(\d+)/i, /top\s*(\d+)/i, /(\d+)\s*个股票/];
    for (var i = 0; i < patterns.length; i++) {
      var m = text.match(patterns[i]);
      if (m) {
        var v = parseInt(m[1], 10);
        if (v >= 1 && v <= 500) return v;
      }
    }
    return null;
  }

  function detectRebalance(text) {
    if (/每天|每日|日调|天天/.test(text)) return 'day';
    if (/每周|周调|周度|一周/.test(text)) return 'week';
    if (/每月|月度|月调|一个月/.test(text)) return 'month';
    return null;
  }

  function detectRisk(text) {
    if (/激进|猛一点|搏|高风险/.test(text)) return 'aggressive';
    if (/稳|保守|小心|别亏|低风险/.test(text)) return 'conservative';
    return null;
  }

  function detectUnsupported(text) {
    var lowered = (text || '').toLowerCase();
    var found = [];
    Object.keys(UNSUPPORTED_TERMS).forEach(function (term) {
      if (lowered.indexOf(term) >= 0 && found.indexOf(UNSUPPORTED_TERMS[term]) < 0) {
        found.push(UNSUPPORTED_TERMS[term]);
      }
    });
    return found;
  }

  function restate(frame) {
    var parts = [];
    parts.push('在' + UNIVERSE_CN[frame.universe || 'csi300'] + '里');
    if (frame.styles && frame.styles.length) {
      var topics = frame.styles.map(function (s) { return recipe(s).topic; }).join('、');
      topics = topics.replace(/的$/, '');
      parts.push('挑' + topics + '的 ' + (frame.topk || DEFAULT_TOPK) + ' 只');
    } else {
      parts.push('挑一批股票（先按 ' + (frame.topk || DEFAULT_TOPK) + ' 只）');
    }
    parts.push(REBALANCE_CN[frame.rebalance || DEFAULT_REBALANCE] + '换一次');
    return parts.join('，');
  }

  /** 关键词版解析：与后端 rules.heuristic_frame 同逻辑 */
  function heuristicFrame(intent, structured) {
    var text = (intent || '').trim();
    var lowered = text.toLowerCase();

    var styles = Object.keys(STYLE_KEYWORDS).filter(function (style) {
      return STYLE_KEYWORDS[style].some(function (kw) {
        return lowered.indexOf(kw) >= 0 && !negated(lowered, kw);
      });
    });
    if (text.indexOf('稳赚') >= 0) {
      styles = styles.filter(function (s) { return s !== 'low_volatility'; });
    }
    // 模型是独有链路，和其他风格同时命中时以模型为准
    if (styles.indexOf('model') >= 0) styles = ['model'];

    var universe = detectUniverse(text);
    var topk = detectTopk(text);
    var rebalance = detectRebalance(text);
    var risk = detectRisk(text);

    // 结构化参数（引导式表单）优先级最高
    var sp = structured || {};
    if (sp.universe && UNIVERSE_CN[sp.universe]) universe = sp.universe;
    if (typeof sp.topk === 'number' && sp.topk > 0) topk = sp.topk;
    if (sp.rebalance && REBALANCE_CN[sp.rebalance]) rebalance = sp.rebalance;
    if (sp.risk_level) risk = sp.risk_level;
    if (Object.prototype.toString.call(sp.styles) === '[object Array]') {
      styles = sp.styles.filter(function (s) { return !!RECIPES[s]; });
    }

    var missing = [];
    if (!universe) missing.push('universe');
    if (!styles.length) missing.push('styles');

    var confidence = 0.30;
    if (styles.length) confidence += 0.30;
    if (universe) confidence += 0.15;
    if (topk || rebalance) confidence += 0.10;
    if (styles.length === 1) confidence += 0.05;
    confidence = Math.round(Math.min(confidence, 0.90) * 100) / 100;

    var frame = {
      universe: universe, styles: styles, topk: topk, rebalance: rebalance,
      risk_level: risk, need_model: styles.indexOf('model') >= 0,
      confidence: confidence, missing: missing
    };
    frame.restated = restate(frame);
    return frame;
  }

  // ---------- 澄清 ----------
  function styleOption(value) {
    var r = recipe(value);
    return { value: value, label: r.label, hint: r.pitch };
  }

  function questionsFor(frame) {
    if (!frame.styles || !frame.styles.length) {
      return [{
        key: 'styles',
        title: '你想挑什么样的股票？',
        options: CLARIFY_STYLES.map(styleOption),
        recommended: 'momentum',
        blocking: true
      }];
    }
    var qs = [];
    if (frame.confidence < 0.6) {
      qs.push({
        key: 'confirm',
        title: '我理解你想的是：' + restate(frame) + '。对吗？',
        options: [
          { value: 'yes', label: '对，就这样', hint: '直接开始回测' },
          { value: 'no', label: '不太对', hint: '我重新说一遍' }
        ],
        recommended: 'yes',
        blocking: true
      });
    }
    if (!frame.universe) {
      qs.push({
        key: 'universe',
        title: '从哪个池子里挑？',
        options: Object.keys(UNIVERSE_CN).map(function (k) {
          return { value: k, label: UNIVERSE_CN[k], hint: '' };
        }),
        recommended: 'csi300',
        blocking: false
      });
    }
    return qs.slice(0, 2);
  }

  function isReady(frame) {
    if (!frame.styles || !frame.styles.length) return false;
    return !questionsFor(frame).some(function (q) { return q.blocking; });
  }

  // ---------- 编译：IntentFrame → 用户可见的参数卡 ----------
  function compile(frame) {
    if (!frame.styles || !frame.styles.length) return null;
    var assumptions = [];
    var caveats = [];

    var universe = frame.universe || 'csi300';
    if (!frame.universe) assumptions.push('你没说从哪个池子选，我按' + UNIVERSE_CN[universe]);

    var topk = frame.topk || DEFAULT_TOPK;
    if (!frame.topk) assumptions.push('你没说买多少只，我按 ' + topk + ' 只');

    var rebalance = frame.rebalance || DEFAULT_REBALANCE;
    if (!frame.rebalance) assumptions.push('你没说多久换一次，我按' + REBALANCE_CN[rebalance] + '换');

    var risk = frame.risk_level || DEFAULT_RISK;
    var range = dateRange();
    assumptions.push('回测区间用最近 ' + BACKTEST_YEARS + ' 年（' + range.start + ' ~ ' + range.end + '）');

    frame.styles.forEach(function (s) {
      var c = recipe(s).caveat;
      if (c && caveats.indexOf(c) < 0) caveats.push(c);
    });

    var labels = frame.styles.map(function (s) { return recipe(s).label; });
    var name = UNIVERSE_CN[universe] + labels.join(' + ') + topk + '只·' + REBALANCE_CN[rebalance] + '调';

    var card = {
      '标的': UNIVERSE_CN[universe],
      '挑什么': frame.styles.map(function (s) { return recipe(s).pitch; }).join('、'),
      '买多少': topk + ' 只，等权买入',
      '多久换': REBALANCE_CN[rebalance],
      '回测区间': range.start + ' ~ ' + range.end,
      '风控': '单只不超过 ' + Math.round(MAX_SINGLE_WEIGHT[risk] * 100) + '%'
    };

    return { card: card, assumptions: assumptions, caveats: caveats, name: name.slice(0, 60) };
  }

  /** 编译结果 → demo 生成器 / 进度页所需的扁平 cfg */
  function toCfg(frame, intent) {
    var compiled = compile(frame);
    var universe = UNIVERSE_CN[frame.universe || 'csi300'];
    var freq = frame.rebalance === 'week' ? '每周' : frame.rebalance === 'day' ? '每天' : '每月';
    var isModel = !!frame.need_model || (frame.styles || []).indexOf('model') >= 0;
    var signal = isModel ? 'AI预测模型'
      : (frame.styles || []).map(function (s) { return recipe(s).label; }).join('+');
    var hash = 0, seedText = JSON.stringify(frame);
    for (var i = 0; i < seedText.length; i++) hash = ((hash << 5) - hash + seedText.charCodeAt(i)) | 0;
    return {
      seed: Math.abs(hash) % 97,
      kind: isModel ? 'model' : 'expression',
      modelInfo: isModel ? 'AI 预测模型（自动学规律）' : null,
      name: compiled ? compiled.name : '量化策略',
      intent: intent || frame.restated || '',
      spec: { universe: universe, hold: frame.topk || DEFAULT_TOPK, freq: freq, signal: signal, direction: 'desc' },
      plan: compiled
    };
  }

  /** 提交给后端的干净帧：只带 IntentFrame 认的字段（后端 extra=forbid） */
  function clean(frame) {
    return {
      universe: frame.universe || null,
      styles: (frame.styles || []).slice(),
      topk: frame.topk || null,
      rebalance: frame.rebalance || null,
      risk_level: frame.risk_level || null,
      need_model: !!frame.need_model,
      restated: frame.restated || '',
      confidence: typeof frame.confidence === 'number' ? frame.confidence : 0,
      missing: frame.missing || []
    };
  }

  function toggleStyle(frame, style) {
    var styles = (frame.styles || []).slice();
    var idx = styles.indexOf(style);
    if (idx >= 0) styles.splice(idx, 1);
    else if (RECIPES[style] && RECIPES[style].exclusive) styles = [style];
    else {
      styles = styles.filter(function (s) { return !RECIPES[s].exclusive; });
      styles.push(style);
    }
    var next = {
      universe: frame.universe, styles: styles, topk: frame.topk,
      rebalance: frame.rebalance, risk_level: frame.risk_level,
      need_model: styles.indexOf('model') >= 0, confidence: frame.confidence, missing: []
    };
    next.restated = restate(next);
    return next;
  }

  global.QPIntent = {
    UNIVERSE_CN: UNIVERSE_CN,
    REBALANCE_CN: REBALANCE_CN,
    RECIPES: RECIPES,
    STYLE_ORDER: STYLE_ORDER,
    CLARIFY_STYLES: CLARIFY_STYLES,
    TOPK_CHOICES: TOPK_CHOICES,
    recipe: recipe,
    heuristicFrame: heuristicFrame,
    restate: restate,
    questionsFor: questionsFor,
    isReady: isReady,
    compile: compile,
    toCfg: toCfg,
    clean: clean,
    toggleStyle: toggleStyle,
    detectUnsupported: detectUnsupported
  };
})(window);
