/* ============ QuantPilot H5 · 应用主逻辑 ============ */
(function () {
  'use strict';

  var $ = function (sel) { return document.querySelector(sel); };
  var $$ = function (sel) { return Array.prototype.slice.call(document.querySelectorAll(sel)); };
  var I = function () { return window.QPIntent; };

  var state = {
    current: null,
    compareSet: [],
    lastResult: null,
    // 确认卡
    confirmFrame: null,
    confirmIntent: '',
    confirmSource: 'text',
    confirmMore: false,
    confirmRev: 0,
    // 三问向导
    wizStep: 1,
    wizFrame: null
  };

  // ---------- 工具 ----------
  function pct(v, digits) { return ((v || 0) * 100).toFixed(digits === undefined ? 1 : digits) + '%'; }
  function num(v, d) { return (v || 0).toFixed(d === undefined ? 2 : d); }
  function esc(s) {
    return String(s === undefined || s === null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function toast(msg) {
    var t = $('#toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._timer); t._timer = setTimeout(function () { t.classList.remove('show'); }, 1800);
  }
  function colorClass(v) { return v >= 0 ? 'up' : 'down'; }

  // ---------- 页面导航 ----------
  function go(page) {
    $$('.page').forEach(function (p) { p.classList.toggle('active', p.id === 'page-' + page); });
    $$('.tab').forEach(function (t) {
      t.classList.toggle('active', t.dataset.page === page && !t.classList.contains('tab-center'));
    });
    window.scrollTo(0, 0);
  }

  // ============================================================
  // 入口一：一句话 → 解析 → 确认卡
  // ============================================================

  /** 本地/远端统一的解析入口。远端不可用时用同逻辑的本地实现兜底。 */
  function parseIntentPayload(intent, frame) {
    if (window.QPApi && window.QPApi.enabled()) {
      return window.QPApi.parseIntent(intent, frame ? I().clean(frame) : null)
        .then(function (res) {
          if (!res || !res.frame) throw new Error('解析结果为空');
          return { frame: res.frame, unsupported: res.unsupported || [] };
        })
        .catch(function () {
          return localPayload(intent, frame);
        });
    }
    return Promise.resolve(localPayload(intent, frame));
  }

  function localPayload(intent, frame) {
    var f = frame ? I().heuristicFrame(intent || '', frame) : I().heuristicFrame(intent || '');
    return { frame: f, unsupported: I().detectUnsupported(intent || '') };
  }

  /**
   * 打开确认卡。三种入口都走这里：
   * - 自由输入：intent 有值、frame 为空（后台先解析）
   * - 模板 / 因子：frame 有值，立刻出卡，不消耗理解步骤
   *
   * 自由输入不阻塞：本地规则先秒出结果，后端 LLM 的解析回来后再校正。
   * 若用户已经开始手改（rev 变了），就不覆盖，避免改到一半被回滚。
   */
  function askConfirm(intent, frame, source) {
    state.confirmIntent = intent || '';
    state.confirmSource = source || 'text';
    state.confirmFrame = frame ? I().heuristicFrame(intent || '', frame) : I().heuristicFrame(intent || '');
    state.confirmMore = false;
    state.confirmRev++;
    openSheet('#confirm-sheet');
    renderConfirm();

    if (frame) return;  // 模板/因子自带帧，不需要再问后台

    var rev = state.confirmRev;
    parseIntentPayload(state.confirmIntent, null).then(function (payload) {
      // 用户已经改过参数，或者换了另一句话，就丢弃这次返回
      if (rev !== state.confirmRev) return;
      state.confirmFrame = payload.frame;
      renderConfirm();
    });
  }

  function openSheet(sel) { $(sel).classList.add('show'); }
  function closeSheet(sel) { $(sel).classList.remove('show'); }

  /** 确认卡渲染：只出现人话，不出现表达式 / 代码 / IC 这类词 */
  function renderConfirm() {
    var body = $('#confirm-body');
    var goBtn = $('#confirm-go');

    var frame = state.confirmFrame || { styles: [], universe: null, topk: null, rebalance: null };
    var styles = frame.styles || [];
    var unsupported = I().detectUnsupported(state.confirmIntent);
    var html = '';

    if (!styles.length) {
      // 没听懂要挑什么：不硬猜，给四个选择
      html += '<p class="confirm-say">我还没听出你想挑什么样的股票。<em>选一个</em>，或者关掉重新说。</p>';
      html += stylePicker(styles, 'clarify');
      if (unsupported.length) html += unsupportedWarn(unsupported, false);
      body.innerHTML = html;
      goBtn.disabled = true;
      bindConfirmPicks();  // 这四张卡必须能点，否则用户被卡死在这一步
      return;
    }

    var compiled = I().compile(frame);
    html += '<p class="confirm-say">' + esc(I().restate(frame)) + '</p>';
    html += '<div class="confirm-card">' + cardRows(compiled.card) + '</div>';

    if (unsupported.length) html += unsupportedWarn(unsupported, true);
    if (compiled.caveats.length) {
      html += '<div class="confirm-warn"><span>⚠️</span><div>' + compiled.caveats.map(esc).join('<br>') + '</div></div>';
    }

    if (compiled.assumptions.length) {
      html += '<div class="confirm-assume"><span class="ca-icon">✍️</span><div>'
        + '<strong>这些我替你定了：</strong><br>' + compiled.assumptions.map(esc).join('<br>')
        + '</div></div>';
    }

    html += '<div class="confirm-ask"><p class="confirm-ask-title">想换一种挑法？</p>'
      + stylePicker(styles, 'chips') + '</div>';

    if (!frame.universe) {
      html += '<div class="confirm-ask"><p class="confirm-ask-title">从哪个池子里挑？</p>'
        + '<div class="opt-chips">' + Object.keys(I().UNIVERSE_CN).map(function (k) {
          return '<button class="opt-chip' + (frame.universe === k ? ' on' : '') + '" data-universe="' + k + '">' + I().UNIVERSE_CN[k] + '</button>';
        }).join('') + '</div></div>';
    }

    // 数量和频率：默认收起，但确实能改（不能写着"都能改"却不给改）
    html += '<button class="confirm-more" id="confirm-more">'
      + (state.confirmMore ? '收起数量与频率' : '还要改数量 / 多久换一次？') + ' ›</button>';
    if (state.confirmMore) {
      html += '<div class="confirm-more-panel">'
        + optGroup('买多少只', '买得多更分散', 'topk',
            I().TOPK_CHOICES.map(function (n) { return { v: n, label: n + ' 只' }; }), frame.topk || 20)
        + optGroup('多久换一次', '换得勤成本高', 'rebalance',
            [{ v: 'week', label: '每周' }, { v: 'month', label: '每月' }, { v: 'day', label: '每天' }], frame.rebalance || 'month')
        + '</div>';
    }

    html += '<p class="confirm-edit-note">改完点「就按这个跑」，全程模拟，不碰真钱。</p>';
    body.innerHTML = html;
    goBtn.disabled = false;
    bindConfirmPicks();
  }

  /**
   * 风格选择器三种形态：
   * - clarify：没听懂时给 4 个最常见选项（卡片）
   * - full：向导第一步，6 个全给（卡片）
   * - chips：确认卡里「换一种挑法」的小胶囊
   */
  function stylePicker(selected, mode) {
    var gridMode = mode !== 'chips';
    var list = mode === 'clarify' ? I().CLARIFY_STYLES : I().STYLE_ORDER;
    if (gridMode) {
      return '<div class="pick-grid">' + list.map(function (s) {
        var r = I().recipe(s);
        return '<button class="pick-card' + (selected.indexOf(s) >= 0 ? ' on' : '') + '" data-style="' + s + '">'
          + '<span class="pick-check">✓</span>'
          + '<span class="pick-icon">' + r.icon + '</span>'
          + '<span class="pick-name">' + r.label + '</span>'
          + '<span class="pick-hint">' + r.pitch + '</span>'
          + '</button>';
      }).join('') + '</div>';
    }
    return '<div class="opt-chips">' + list.map(function (s) {
      var r = I().recipe(s);
      return '<button class="opt-chip' + (selected.indexOf(s) >= 0 ? ' on' : '') + '" data-style="' + s + '">'
        + r.label + '</button>';
    }).join('') + '</div>';
  }

  function cardRows(card) {
    return Object.keys(card).map(function (k) {
      return '<div class="confirm-row"><span class="k">' + esc(k) + '</span><span class="v">' + esc(card[k]) + '</span></div>';
    }).join('');
  }

  function unsupportedWarn(terms, canRun) {
    var tail = canRun
      ? '我暂时看不了，下面这套是按量价给你配的，别指望它真按这个选。'
      : '这需要财报或资金类数据，我暂时看不了。换个说法我再试。';
    return '<div class="confirm-warn"><span>🚫</span><div>你提到<strong>' + esc(terms.join('、'))
      + '</strong>，' + tail + '</div></div>';
  }

  function bindConfirmPicks() {
    $$('#confirm-body [data-style]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var next = I().toggleStyle(state.confirmFrame, btn.dataset.style);
        if (next.styles.length > 2) next.styles = next.styles.slice(-2);
        next.restated = I().restate(next);
        state.confirmFrame = next;
        state.confirmRev++;
        renderConfirm();
      });
    });
    $$('#confirm-body [data-universe]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var next = Object.assign({}, state.confirmFrame, { universe: btn.dataset.universe });
        next.restated = I().restate(next);
        state.confirmFrame = next;
        state.confirmRev++;
        renderConfirm();
      });
    });
    $$('#confirm-body [data-group]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.dataset.group, raw = btn.dataset.value, patch = {};
        patch[key] = key === 'topk' ? parseInt(raw, 10) : raw;
        var next = Object.assign({}, state.confirmFrame, patch);
        next.restated = I().restate(next);
        state.confirmFrame = next;
        state.confirmMore = true;
        state.confirmRev++;
        renderConfirm();
      });
    });
    var more = $('#confirm-more');
    if (more) {
      more.addEventListener('click', function () {
        state.confirmMore = !state.confirmMore;
        renderConfirm();
      });
    }
  }

  // ============================================================
  // 入口二：三问向导
  // ============================================================
  function openWizard() {
    state.wizStep = 1;
    state.wizFrame = {
      universe: 'csi300', styles: [], topk: 20, rebalance: 'month',
      risk_level: null, need_model: false, confidence: 0.8, missing: [], restated: ''
    };
    go('wizard');
    renderWizard();
  }

  function wizPatch(patch) {
    var next = Object.assign({}, state.wizFrame, patch);
    next.need_model = (next.styles || []).indexOf('model') >= 0;
    next.restated = I().restate(next);
    state.wizFrame = next;
    renderWizard();
  }

  function renderWizard() {
    var f = state.wizFrame, step = state.wizStep;
    $$('#page-wizard .wiz-dot').forEach(function (d, i) {
      d.classList.toggle('on', i === step - 1);
      d.classList.toggle('done', i < step - 1);
    });
    $('#wiz-step-label').textContent = '第 ' + step + ' 步 / 共 3 步';
    $('#wiz-prev').style.visibility = step === 1 ? 'hidden' : 'visible';
    $('#wiz-next').textContent = step === 3 ? '开始回测' : '下一步';
    $('#wiz-next').disabled = step === 1 && !f.styles.length;

    var body = $('#wizard-body');
    if (step === 1) {
      body.innerHTML = '<h1 class="wiz-title">你想挑什么样的股票？</h1>'
        + '<p class="wiz-sub">可以选两个，比如「追涨 + 求稳」就是又强又不疯。</p>'
        + stylePicker(f.styles, 'full');
    } else if (step === 2) {
      body.innerHTML = '<h1 class="wiz-title">从哪儿挑，买多少？</h1>'
        + '<p class="wiz-sub">这些我先按常见的填好，你不改也能直接跑。</p>'
        + optGroup('从哪个池子里挑', '选股范围', 'universe',
            Object.keys(I().UNIVERSE_CN).map(function (k) { return { v: k, label: I().UNIVERSE_CN[k] }; }), f.universe)
        + optGroup('买多少只', '买得多更分散', 'topk',
            I().TOPK_CHOICES.map(function (n) { return { v: n, label: n + ' 只' }; }), f.topk)
        + optGroup('多久换一次', '换得勤成本高', 'rebalance',
            [{ v: 'week', label: '每周' }, { v: 'month', label: '每月' }, { v: 'day', label: '每天' }], f.rebalance)
        + '<div class="wiz-live"><span>你现在的选择</span><p>' + esc(I().restate(f)) + '</p></div>';
    } else {
      var compiled = I().compile(f);
      var html = '<h1 class="wiz-title">就这样？</h1>'
        + '<p class="wiz-sub">点「开始回测」我就按这个跑，全程模拟。</p>'
        + '<p class="confirm-say">' + esc(I().restate(f)) + '</p>';
      if (compiled) {
        html += '<div class="confirm-card">' + cardRows(compiled.card) + '</div>';
        if (compiled.caveats.length) {
          html += '<div class="confirm-warn"><span>⚠️</span><div>' + compiled.caveats.map(esc).join('<br>') + '</div></div>';
        }
      }
      body.innerHTML = html;
    }
    bindWizardPicks();
  }

  function optGroup(title, note, key, options, current) {
    return '<div class="opt-group"><div class="opt-group-head"><strong>' + title + '</strong><span>' + note + '</span></div>'
      + '<div class="opt-chips">' + options.map(function (o) {
        return '<button class="opt-chip' + (o.v === current ? ' on' : '') + '" data-group="' + key + '" data-value="' + o.v + '">'
          + o.label + '</button>';
      }).join('') + '</div></div>';
  }

  function bindWizardPicks() {
    $$('#wizard-body [data-style]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var next = I().toggleStyle(state.wizFrame, btn.dataset.style);
        if (next.styles.length > 2) next.styles = next.styles.slice(-2);
        wizPatch({ styles: next.styles });
      });
    });
    $$('#wizard-body [data-group]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var key = btn.dataset.group, raw = btn.dataset.value, patch = {};
        patch[key] = key === 'topk' ? parseInt(raw, 10) : raw;
        wizPatch(patch);
      });
    });
  }

  // ============================================================
  // 提交：确认卡 / 向导 → 进度页 → 结果页
  // ============================================================
  var STAGES_EXPR = [
    { label: '理解你的想法…', desc: '正在把你的话翻译成选股规则' },
    { label: '整理选股规则…', desc: '后台自动补全你没说的参数' },
    { label: '安全检查中…', desc: '确认没有风险操作' },
    { label: '正在回测…', desc: '用历史数据模拟买卖' },
    { label: '完成！', desc: '结果已经准备好了' }
  ];
  var STAGES_MODEL = [
    { label: '理解你的想法…', desc: '正在把你的话翻译成选股规则' },
    { label: '准备数据…', desc: '整理历史行情数据' },
    { label: '让模型学规律…', desc: '从过去的数据里找规律' },
    { label: '模型预测并回测…', desc: '用没见过的数据验证效果' },
    { label: '完成！', desc: '结果已经准备好了' }
  ];

  // 后端标准化结果 → 前端渲染所需的形状（真实 spec 嵌套深，这里拍平）
  function normalizeResult(raw, fallbackCfg) {
    var spec = raw.spec || {};
    var sig = spec.signal || {};
    var model = sig.model || {};
    return {
      id: raw.strategy_id || raw.task_id,
      name: (spec.meta && spec.meta.name) || (fallbackCfg && fallbackCfg.name) || '量化策略',
      intent: (spec.meta && spec.meta.intent) || (fallbackCfg && fallbackCfg.intent) || '',
      kind: raw.strategy_type || (sig.type === 'model' ? 'model' : 'expression'),
      modelInfo: model.model_id ? (model.model_id + ' · ' + (model.features || 'alpha158')) : null,
      metrics: raw.metrics || {},
      series: raw.series || { nav: [], benchmark_nav: [], drawdown: [] },
      contributions: raw.contributions || [],
      holdings: raw.holdings || [],
      split: raw.split || {},
      spec: {
        universe: (spec.universe && (spec.universe.name || 'csi300')) || '沪深300',
        hold: (spec.portfolio && spec.portfolio.topk) || 20,
        freq: (spec.rebalance && spec.rebalance.freq === 'month' ? '每月'
               : spec.rebalance && spec.rebalance.freq === 'week' ? '每周' : '每月'),
        signal: sig.type === 'model' ? 'AI预测模型' : (fallbackCfg && fallbackCfg.spec && fallbackCfg.spec.signal) || '选股策略',
        direction: sig.direction || 'desc',
      },
      plan: raw.plan || (fallbackCfg && fallbackCfg.plan) || null,
      demo: !!raw._demo,
      created: new Date(),
      favorite: false,
      task_id: raw.task_id,
    };
  }

  function finishWithResult(result) {
    // 真实 API 返回的结果以前只在内存里转一圈，历史页根本看不到；
    // 这里统一入库。demo 结果已由 create 存过，命中就不重复写盘。
    if (!MockData.get(result.id)) MockData.put(result);
    window.__dbg = { name: result.name, ann: result.metrics && result.metrics.annual_return, tid: result.task_id };
    state.current = result;
    state.lastResult = result;
    renderResult(result);
    go('result');
    toast('回测完成，快看看结果');
  }

  function makeProgressUI(stages) {
    var pctEl = $('#progress-pct'), ring = $('.pulse-ring');
    var labelEl = $('#progress-stage-label'), descEl = $('#progress-stage-desc');
    var steps = $$('#step-list li');
    return function (progress) {
      if (progress > 100) progress = 100;
      pctEl.textContent = Math.round(progress) + '%';
      ring.style.setProperty('--pct', progress + '%');
      var stageIdx = Math.min(stages.length - 1, Math.floor(progress / 100 * stages.length));
      labelEl.textContent = stages[stageIdx].label;
      descEl.textContent = stages[stageIdx].desc;
      steps.forEach(function (li, i) {
        li.classList.toggle('done', i < stageIdx);
        li.classList.toggle('active', i === stageIdx);
        var dot = li.querySelector('.step-dot');
        if (i < stageIdx) dot.textContent = '✓';
      });
    };
  }

  function prepareTaskPage(cfg) {
    var stages = cfg.kind === 'model' ? STAGES_MODEL : STAGES_EXPR;
    go('task');
    renderSpecPreview(cfg);
    return makeProgressUI(stages);
  }

  /** 唯一执行入口：拿着已确认的语义帧开跑 */
  function runStrategy(frame, intent) {
    var cfg = I().toCfg(frame, intent);
    cfg.intent = (intent || frame.restated || '').trim();
    var updateProgress = prepareTaskPage(cfg);
    var submittedFrame = I().clean(frame);

    // ---- 真实 API 分支 ----
    if (window.QPApi && window.QPApi.enabled()) {
      updateProgress(10);
      window.QPApi.submit(cfg.intent, { frame: submittedFrame }).then(function (submitted) {
        toast('任务已提交，正在跑回测…');
        return window.QPApi.poll(submitted.task_id, function (t) {
          var map = { intent: 15, spec: 35, code: 55, backtest: 80, result: 95 };
          updateProgress(map[t.stage] || t.progress || 10);
          if (t.status === 'failed') throw new Error(t.error || '回测失败');
        });
      }).then(function (task) {
        if (task.status !== 'succeeded') throw new Error(task.error || '任务未成功');
        updateProgress(100);
        return window.QPApi.getResult(task.task_id);
      }).then(function (raw) {
        finishWithResult(normalizeResult(raw, cfg));
      }).catch(function (err) {
        // 失败不静默：结果页顶部会挂横幅说明原因，用户知道这不是真实回测
        var reason = plainReason((err && err.message) || '');
        console.warn('[QP] 真实链路失败，切换演示数据:', err);
        toast('回测没跑成：' + reason);
        setTimeout(function () { finishWithResult(demoResult(cfg, reason)); }, 500);
      });
      return;
    }

    // ---- 离线 mock 分支 ----
    var progress = 0;
    var timer = setInterval(function () {
      progress += 2 + Math.random() * 3;
      updateProgress(progress);
      if (progress >= 100) {
        clearInterval(timer);
        setTimeout(function () { finishWithResult(demoResult(cfg, '当前是纯前端离线模式')); }, 400);
      }
    }, 90);
  }

  function demoResult(cfg, reason) {
    var r = MockData.create(cfg);
    r.demo = true;
    r.demoReason = reason || '后端没有连上';
    return r;
  }

  /** 技术错误 → 用户能看懂的一句话（原文进 console，不糊在界面上） */
  function plainReason(msg) {
    if (!msg) return '后端没有连上';
    if (/125|podman|容器|docker|sandbox|执行器/i.test(msg)) return '回测服务在这台机器上没跑起来（执行环境不可用）';
    if (/轮询超时|timeout/i.test(msg)) return '等太久了，任务可能卡住了';
    if (/HTTP 5\d\d/.test(msg)) return '后端出错了，服务暂时不可用';
    if (/HTTP 4\d\d/.test(msg)) return '请求被拒绝（HTTP ' + msg.replace(/\D/g, '') + '）';
    if (/[\u4e00-\u9fa5]/.test(msg)) return msg.length > 40 ? msg.slice(0, 40) + '…' : msg;
    return '后端没有连上';
  }

  function renderSpecPreview(cfg) {
    var s = cfg.spec;
    var rows = [
      ['选哪个市场', s.universe],
      ['买多少只', s.hold + ' 只'],
      ['多久换一次', s.freq],
      ['选股思路', s.signal]
    ];
    $('#spec-preview-body').innerHTML = rows.map(function (r) {
      return '<div class="spec-row"><span class="k">' + esc(r[0]) + '</span><span class="v">' + esc(r[1]) + '</span></div>';
    }).join('');
  }

  // ---------- 首页模板卡：点一下直接出确认卡 ----------
  function renderTemplates() {
    var grid = $('#template-grid');
    grid.innerHTML = MockData.TEMPLATES.map(function (t, i) {
      var badge = (t.frame && t.frame.styles && t.frame.styles[0] === 'model') ? '<span class="tpl-badge">AI 模型</span>' : '';
      return '<div class="template-card" data-idx="' + i + '">'
        + '<span class="tpl-arrow">›</span>' + badge
        + '<span class="tpl-icon" style="color:' + t.color + '">' + t.icon + '</span>'
        + '<div class="tpl-name">' + t.name + '</div>'
        + '<div class="tpl-desc">' + t.desc + '</div>'
        + '</div>';
    }).join('');
    $$('.template-card').forEach(function (card) {
      card.addEventListener('click', function () {
        var t = MockData.TEMPLATES[+card.dataset.idx];
        askConfirm(t.intent, t.frame, 'template');
      });
    });
  }

  // ---------- 结果页 ----------
  function plainVerdict(r) {
    var m = r.metrics || {};
    var ann = m.annual_return || 0;
    var dd = Math.abs(m.max_drawdown || 0);
    var tone = ann >= 0.25 ? '表现相当亮眼' : ann >= 0.12 ? '表现稳健不错' : ann < 0 ? '出现亏损' : '收益比较温和';
    var bench = r.series && r.series.benchmark_nav;
    var benchTxt = (bench && bench.length) ? ('，同期沪深300约 ' + pct(bench[bench.length - 1].value - 1)) : '';
    return '这个策略在模拟的这几年里' + tone + '：平均每年约赚 <b>' + pct(ann) + '</b>' + benchTxt
      + '。过程中最多亏过 <b>' + pct(dd) + '</b>（回撤），风险收益比 ' + num(m.calmar || 0, 2) + '。'
      + (m.sharpe > 1 ? ' 收益的稳定性也比较好。' : ' 波动稍大，适合能扛得住起伏的人。');
  }

  var currentPeriod = 'ALL';

  function renderContributions(r) {
    var list = $('#contrib-list');
    if (!list) return;
    var items = r.contributions || [];
    list.innerHTML = items.map(function (c) {
      var up = c.ret >= 0;
      var w = Math.min(100, Math.abs(c.ret) / 0.15 * 100);
      return '<div class="contrib-row">'
        + '<span class="contrib-name">' + esc(c.name) + '</span>'
        + '<div class="contrib-bar-wrap"><i class="contrib-bar ' + (up ? 'pos' : 'neg') + '" style="width:' + w + '%"></i></div>'
        + '<span class="contrib-val ' + colorClass(c.ret) + '">' + (up ? '+' : '') + pct(c.ret, 1) + '</span>'
        + '</div>';
    }).join('');
  }

  function renderHoldings(r) {
    var list = $('#holdings-list');
    if (!list) return;
    var items = r.holdings || [];
    $('#hold-count').textContent = '前 ' + items.length + ' 大';
    list.innerHTML = items.map(function (h) {
      var w = Math.min(100, h.weight / 0.12 * 100);
      return '<div class="holding-row">'
        + '<div class="holding-top"><span class="holding-name">' + esc(h.name) + '</span>'
        + '<span class="holding-pct">' + pct(h.weight, 1) + '</span>'
        + '<span class="holding-ret ' + colorClass(h.ret) + '">' + (h.ret >= 0 ? '+' : '') + pct(h.ret, 1) + '</span></div>'
        + '<div class="holding-bar-wrap"><i class="holding-bar" style="width:' + w + '%"></i></div>'
        + '</div>';
    }).join('');
  }

  function renderResult(r) {
    $('#result-name').textContent = r.name;
    $('#result-intent').textContent = r.intent || '自定义策略';

    var banner = $('#demo-banner');
    if (banner) {
      banner.classList.toggle('hidden', !r.demo);
      if (r.demo) {
        banner.innerHTML = '以下不是真实回测：' + esc(r.demoReason || '后端没有连上')
          + '。为保证体验先展示演示数据，别据此做任何决策。';
      }
    }

    // 策略类型徽章：选股规则 vs AI 预测模型
    var headTop = document.querySelector('.result-head-top');
    var oldBadge = document.getElementById('kind-badge');
    if (oldBadge) oldBadge.remove();
    if (r.kind === 'model') {
      var badge = document.createElement('span');
      badge.id = 'kind-badge';
      badge.className = 'status-pill status-model';
      badge.innerHTML = '🤖 AI 预测模型';
      headTop.insertBefore(badge, headTop.firstChild);
    }
    $('#verdict-text').innerHTML = plainVerdict(r);

    var m = r.metrics;
    var metrics = [
      { label: '年化收益', value: pct(m.annual_return, 1), cls: colorClass(m.annual_return), sub: '每年平均', primary: true },
      { label: '夏普比率', value: num(m.sharpe, 2), cls: '', sub: '越高越稳' },
      { label: '最大回撤', value: pct(m.max_drawdown, 1), cls: 'down', sub: '最多亏这么多' },
      { label: '胜率', value: pct(m.win_rate, 0), cls: '', sub: '赚钱天数占比' },
      { label: '换手率', value: pct(m.turnover, 0), cls: '', sub: '调仓频繁度' },
      { label: '卡玛比率', value: num(m.calmar, 2), cls: '', sub: '收益/风险' }
    ];
    $('#metric-grid').innerHTML = metrics.map(function (x) {
      return '<div class="metric-card' + (x.primary ? ' primary' : '') + '">'
        + '<div class="metric-label">' + x.label + '</div>'
        + '<div class="metric-value ' + x.cls + '">' + x.value + '</div>'
        + '<div class="metric-sub">' + x.sub + '</div>'
        + '</div>';
    }).join('');

    currentPeriod = 'ALL';
    $$('#period-switch button').forEach(function (b) { b.classList.toggle('active', b.dataset.period === 'ALL'); });
    $('#nav-period-label').textContent = '完整回测区间';
    Charts.navChart($('#nav-chart'), r, currentPeriod);
    Charts.drawdownChart($('#drawdown-chart'), r);
    renderContributions(r);
    renderHoldings(r);

    var sp = r.split || {};
    var inS = sp.in_sample || {}, outS = sp.out_sample || {};
    $('#split-metrics').innerHTML =
      '<div class="split-box"><span class="split-label">平时练习 <span class="split-tag">样本内</span></span>'
      + '<div class="split-ret ' + colorClass(inS.annual_return) + '">' + pct(inS.annual_return, 1) + '</div></div>'
      + '<div class="split-box"><span class="split-label">闭卷考试 <span class="split-tag test">样本外</span></span>'
      + '<div class="split-ret ' + colorClass(outS.annual_return) + '">' + pct(outS.annual_return, 1) + '</div></div>';

    // 「我是这么理解的」：优先用后台编译出的参数卡，保证与用户确认时看到的一致
    var plan = r.plan || {};
    var s = r.spec;
    var rows;
    if (plan.card) {
      rows = Object.keys(plan.card).map(function (k) { return [k, plan.card[k]]; });
    } else {
      rows = [
        ['投资市场', s.universe || '—'],
        ['持仓数量', (s.hold || '—') + ' 只'],
        ['调仓频率', s.freq || '—'],
        ['选股思路', s.signal || '—']
      ];
    }
    if (r.kind === 'model') rows.push(['模型', r.modelInfo || 'AI 预测模型']);
    $('#spec-detail').innerHTML = rows.map(function (d) {
      return '<div class="spec-detail-row"><span class="sk">' + esc(d[0]) + '</span><span class="sv">' + esc(d[1]) + '</span></div>';
    }).join('');

    var assumptions = plan.assumptions || [];
    $('#plan-assumptions').innerHTML = assumptions.length
      ? '<b>这些我替你定了：</b>' + assumptions.map(esc).join('；')
      : '参数已按常见默认值补全，想改直接说一句就行。';

    var caveatBox = $('#plan-caveats');
    if (caveatBox) {
      caveatBox.innerHTML = (plan.caveats || []).map(function (c) {
        return '<p class="plan-caveat">' + esc(c) + '</p>';
      }).join('');
    }

    var favBtn = $('#fav-btn');
    favBtn.classList.toggle('faved', !!r.favorite);
    $('#fav-path').setAttribute('fill', r.favorite ? '#f5a623' : 'none');
  }

  // ---------- 因子中心 ----------
  function renderFactors() {
    var list = $('#factor-list');
    if (!list) return;
    var factors = MockData.FACTORS.slice().sort(function (a, b) { return b.ir - a.ir; });
    if (window.QPApi && window.QPApi.enabled()) {
      window.QPApi.factors().then(function (data) {
        var arr = (data.items || []).map(function (x) {
          var p = x.performance || {};
          var code = (x.asset_id || '').replace('factor:', '');
          var known = MockData.FACTORS.filter(function (f) { return f.code === code; })[0];
          return {
            name: x.name, code: code, style: known ? known.style : 'momentum',
            ic: p.ic_mean || 0, ir: p.ir || 0, dir: x.direction || 'desc', tags: x.tags || [],
          };
        });
        if (arr.length) paintFactors(list, arr);
      }).catch(function () { paintFactors(list, factors); });
    } else {
      paintFactors(list, factors);
    }
  }

  function paintFactors(list, factors) {
    list.innerHTML = factors.map(function (f, i) {
      var tags = (f.tags || []).map(function (t) { return '<span class="factor-tag">' + esc(t) + '</span>'; }).join('');
      var rankCls = i === 0 ? ' top' : i === 1 ? ' second' : i === 2 ? ' third' : '';
      return '<div class="factor-card">'
        + '<div class="factor-card-head">'
        + '<span class="factor-rank' + rankCls + '">' + (i + 1) + '</span>'
        + '<div class="factor-title"><strong>' + esc(f.name) + '</strong></div>'
        + '<span class="factor-ir">历史有效性 <b>' + num(f.ir, 2) + '</b></span>'
        + '</div>'
        + '<div class="factor-metrics">'
        + '<div><span>适合</span><b>' + esc((f.tags && f.tags[0]) || '通用') + '</b></div>'
        + '<div><span>选股方式</span><b>' + (f.dir === 'desc' ? '分数越高越优先' : '分数越低越优先') + '</b></div>'
        + '<div><span>类别</span><b>' + esc((f.tags && f.tags[1]) || '量价') + '</b></div>'
        + '</div>'
        + '<div class="factor-card-foot"><div class="factor-tags">' + tags + '</div>'
        + '<button class="factor-use" data-style="' + esc(f.style || 'momentum') + '" data-name="' + esc(f.name) + '">用它做策略 ›</button></div>'
        + '</div>';
    }).join('');

    $$('.factor-use').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var style = btn.dataset.style;
        var r = I().recipe(style);
        var frame = { universe: 'csi300', styles: [style], topk: 20, rebalance: 'month' };
        askConfirm('用「' + btn.dataset.name + '」挑股票，' + r.pitch, frame, 'factor');
      });
    });
  }

  // ---------- 历史页 ----------
  function renderHistory() {
    var list = $('#history-list');
    var all = MockData.all();
    if (!all.length) {
      list.innerHTML = '<div class="empty-state"><span class="empty-emoji">📭</span>'
        + '<strong>还没有回测记录</strong><p>去首页做第一个策略吧</p>'
        + '<button class="btn-primary" style="max-width:200px;margin:auto" onclick="document.querySelector(\'[data-page=home]\').click()">去创建</button></div>';
      return;
    }
    list.innerHTML = all.map(function (r) {
      var ret = r.metrics.annual_return;
      return '<div class="history-item" data-id="' + r.id + '">'
        + '<div class="hist-icon">📈</div>'
        + '<div class="hist-body"><strong>' + esc(r.name) + '</strong><p>' + r.id + ' · ' + (r.created.getMonth() + 1) + '/' + r.created.getDate() + ' · ' + esc(r.spec.universe || '') + '</p></div>'
        + '<div class="hist-ret"><span class="v ' + colorClass(ret) + '">' + pct(ret, 1) + '</span><span class="k">年化</span></div>'
        + '<button class="hist-star' + (r.favorite ? ' on' : '') + '" data-star="' + r.id + '">★</button>'
        + '</div>';
    }).join('');

    $$('.history-item').forEach(function (item) {
      item.addEventListener('click', function (e) {
        if (e.target.dataset.star) return;
        var r = MockData.get(item.dataset.id);
        if (r) { state.current = r; renderResult(r); go('result'); }
      });
    });
    $$('.hist-star').forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        var r = MockData.toggleFavorite(btn.dataset.star);
        if (!r) return;
        btn.classList.toggle('on', r.favorite);
        toast(r.favorite ? '已收藏' : '取消收藏');
      });
    });
  }

  // ---------- 对比 ----------
  function openCompare() {
    var all = MockData.all();
    if (all.length < 2) { toast('至少要有 2 个策略才能对比'); return; }
    var picked = all.slice(0, Math.min(5, all.length));
    var colors = Charts.compareChart($('#compare-chart'), picked);
    $('#compare-legend').innerHTML = picked.map(function (r, i) {
      return '<span><i style="background:' + colors[i] + '"></i>' + esc(r.name) + ' · ' + pct(r.metrics.annual_return, 1) + '</span>';
    }).join('');
    openSheet('#compare-sheet');
  }

  // ---------- 事件绑定 ----------
  function bind() {
    $$('.tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        var page = tab.dataset.page;
        if (page === 'history') renderHistory();
        if (page === 'factors') renderFactors();
        go(page);
      });
    });
    $('#tab-create').addEventListener('click', function () { go('home'); $('#intent-input').focus(); });

    // 净值周期切换
    $$('#period-switch button').forEach(function (b) {
      b.addEventListener('click', function () {
        currentPeriod = b.dataset.period;
        $$('#period-switch button').forEach(function (x) { x.classList.toggle('active', x === b); });
        var labelMap = { '1M': '近 1 个月', '3M': '近 3 个月', '1Y': '近 1 年', 'ALL': '完整回测区间' };
        $('#nav-period-label').textContent = labelMap[currentPeriod] || '';
        if (state.current) Charts.navChart($('#nav-chart'), state.current, currentPeriod);
      });
    });

    // 一句话入口
    $$('.chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        $('#intent-input').value = chip.dataset.example;
        toast('已填入示例，点「生成策略」试试');
      });
    });
    $('#submit-intent').addEventListener('click', function () {
      var text = $('#intent-input').value.trim();
      if (!text) { toast('先说说你想要什么样的策略'); $('#intent-input').focus(); return; }
      askConfirm(text, null, 'text');
    });

    // 向导入口
    $('#open-wizard').addEventListener('click', openWizard);
    $('#wiz-close').addEventListener('click', function () { go('home'); });
    $('#wiz-prev').addEventListener('click', function () {
      if (state.wizStep > 1) { state.wizStep--; renderWizard(); }
    });
    $('#wiz-next').addEventListener('click', function () {
      if (state.wizStep < 3) { state.wizStep++; renderWizard(); return; }
      var frame = I().heuristicFrame('', state.wizFrame);
      closeSheet('#confirm-sheet');
      runStrategy(frame, frame.restated);
    });

    // 确认卡
    $('#close-confirm').addEventListener('click', function () { closeSheet('#confirm-sheet'); });
    $('#confirm-sheet').addEventListener('click', function (e) {
      if (e.target === this) this.classList.remove('show');
    });
    $('#confirm-edit').addEventListener('click', function () {
      closeSheet('#confirm-sheet');
      if (state.confirmSource === 'wizard') { go('wizard'); return; }
      go('home');
      var input = $('#intent-input');
      if (state.confirmIntent) input.value = state.confirmIntent;
      input.focus();
    });
    $('#confirm-go').addEventListener('click', function () {
      if (!state.confirmFrame || !state.confirmFrame.styles.length) return;
      closeSheet('#confirm-sheet');
      runStrategy(state.confirmFrame, state.confirmIntent || state.confirmFrame.restated);
    });

    $('#fav-btn').addEventListener('click', function () {
      if (!state.current) return;
      var r = MockData.toggleFavorite(state.current.id);
      if (!r) return;
      state.current = r;
      this.classList.toggle('faved', r.favorite);
      $('#fav-path').setAttribute('fill', r.favorite ? '#f5a623' : 'none');
      toast(r.favorite ? '已收藏' : '取消收藏');
    });

    $('#btn-back-home').addEventListener('click', function () { go('home'); });
    $('#btn-compare').addEventListener('click', function () {
      toast('已加入对比，去「回测」页可查看');
    });
    $('#btn-compare-mode').addEventListener('click', openCompare);
    $('#close-sheet').addEventListener('click', function () { closeSheet('#compare-sheet'); });
    $('#compare-sheet').addEventListener('click', function (e) {
      if (e.target === this) this.classList.remove('show');
    });
    $('#btn-manage').addEventListener('click', function () { toast('Demo：长按可删除（示意）'); });

    $$('.menu-item').forEach(function (m) {
      m.addEventListener('click', function () { toast('Demo 版本，功能开发中'); });
    });
  }

  // ---------- 初始化 ----------
  function init() {
    renderTemplates();
    bind();
    go('home');
    // 只在第一次打开时预置 4 条历史。恢复过本地历史就不再播撒，
    // 否则每次刷新都会多出 4 条重复的演示记录。
    if (!MockData.restored) {
      // 这些都是本地演示数据，点开时结果页会明确挂横幅，不冒充真实回测。
      function seed(cfg) {
        var r = MockData.create(cfg);
        r.demo = true;
        r.demoReason = '本地演示数据';
        return r;
      }
      seed({ seed: 95, name: 'AI预测·沪深300 Top20', intent: '用机器学习模型预测沪深300未来收益，选预测最强的20只',
        spec: { universe: '沪深300', hold: 20, freq: '每月', signal: 'AI预测模型', direction: 'desc' }, signal: 'AI预测模型', direction: 'desc', kind: 'model', modelInfo: 'AI 预测模型（自动学规律）' });
      seed({ seed: 11, name: '沪深300追涨20只·每月调', intent: '沪深300里动量最强的20只，每月调仓',
        spec: { universe: '沪深300', hold: 20, freq: '每月', signal: '追涨', direction: 'desc' }, signal: '追涨', direction: 'desc' });
      seed({ seed: 37, name: '沪深300追涨 + 求稳30只', intent: '又强又稳的30只股票',
        spec: { universe: '沪深300', hold: 30, freq: '每月', signal: '追涨+求稳', direction: 'desc' }, signal: '追涨+求稳', direction: 'desc' });
      seed({ seed: 67, name: '中证500求稳20只', intent: '中证500里波动最小的20只，每月调仓',
        spec: { universe: '中证500', hold: 20, freq: '每月', signal: '求稳', direction: 'desc' }, signal: '求稳', direction: 'desc' });
      MockData.persist();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
