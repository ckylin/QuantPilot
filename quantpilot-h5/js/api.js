/* ============ QuantPilot H5 · API 客户端 ============
 * 封装真实后端 API（Phase 1）。默认关闭（纯静态 demo 离线可用），
 * 通过 window.QP_CONFIG.apiBase 开启后，提交/轮询/结果/目录走真实接口。
 * 任何网络失败都返回 rejected，由 app.js 决定是否回退 mock。
 */
(function (global) {
  'use strict';

  // 默认配置：可通过页面内 <script>window.QP_CONFIG = {...}</script> 覆盖
  var CONFIG = global.QP_CONFIG || {};
  var API_BASE = (CONFIG.apiBase || '').replace(/\/$/, '');
  var ENABLED = !!API_BASE;  // 配了 apiBase 就启用真实 API

  function request(path, options) {
    if (!ENABLED) return Promise.reject(new Error('API 未启用'));
    var opts = options || {};
    var headers = { 'Content-Type': 'application/json' };
    if (CONFIG.deviceId) headers['X-Device-Id'] = CONFIG.deviceId;
    return fetch(API_BASE + path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    });
  }

  var API = {
    enabled: function () { return ENABLED; },
    base: function () { return API_BASE; },

    health: function () { return request('/api/v1/health'); },

    // 零代码入口：一句话 → 理解结果 + 中文参数卡 + 澄清问题（响应中不含表达式/代码）
    parseIntent: function (intent, frame) {
      return request('/api/v1/intents/parse', {
        method: 'POST',
        body: { intent: intent || '', structured_params: {}, frame: frame || null },
      });
    },

    // 提交策略。intent 与 frame 至少给一个：
    // - 自由输入：只给 intent，后端自己做 LLM 抽槽
    // - 引导式确认后：给 frame，后端确定性编译，不调 LLM，结果可复现
    submit: function (intent, opts) {
      return request('/api/v1/strategies', {
        method: 'POST',
        body: {
          intent: intent || '',
          frame: (opts && opts.frame) || null,
          structured_params: (opts && opts.structured_params) || {},
          dataset_ref: (opts && opts.dataset_ref) || null,
          model_id: (opts && opts.model_id) || null,
          client: { device_id: CONFIG.deviceId || 'h5-demo', platform: 'h5' },
        },
      });
    },

    // 查询任务状态
    getTask: function (taskId) { return request('/api/v1/tasks/' + encodeURIComponent(taskId)); },

    // 获取结果（任务成功后）
    getResult: function (taskId) { return request('/api/v1/tasks/' + encodeURIComponent(taskId) + '/result'); },

    // 资产目录
    factors: function (tag) { return request('/api/v1/factors' + (tag ? '?tag=' + encodeURIComponent(tag) : '')); },
    datasets: function () { return request('/api/v1/datasets'); },
    models: function () { return request('/api/v1/models'); },

    // 历史
    strategies: function (favoriteOnly) {
      return request('/api/v1/strategies' + (favoriteOnly ? '?favorite_only=true' : ''));
    },
    favorite: function (id, fav) {
      return request('/api/v1/strategies/' + encodeURIComponent(id) + '/favorite', {
        method: 'POST', body: { favorite: !!fav },
      });
    },

    // 轮询任务直到终态（succeeded/failed），带超时与回调
    poll: function (taskId, onProgress, maxMs) {
      var deadline = Date.now() + (maxMs || 180000);
      function tick() {
        return API.getTask(taskId).then(function (t) {
          if (onProgress) onProgress(t);
          if (t.status === 'succeeded' || t.status === 'failed' || t.status === 'archived') {
            return t;
          }
          if (Date.now() > deadline) throw new Error('轮询超时');
          return new Promise(function (resolve) { setTimeout(resolve, 600); }).then(tick);
        });
      }
      return tick();
    },
  };

  global.QPApi = API;
})(window);
