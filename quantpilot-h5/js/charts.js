/* ============ QuantPilot H5 · 纯 SVG 图表 ============
 * 不依赖任何 CDN，离线可用。中国习惯：涨红跌绿。
 */
(function (global) {
  'use strict';

  var UP = '#e0392f', DOWN = '#0f9d58', BENCH = '#9aa6bd', GRID = '#eef1f6', TXT = '#97a0b0';

  function buildPath(values, x0, y0, w, h, min, max) {
    var n = values.length;
    var px = function (i) { return x0 + (i / (n - 1)) * w; };
    var py = function (v) { return y0 + h - ((v - min) / (max - min || 1)) * h; };
    var d = 'M' + px(0).toFixed(1) + ' ' + py(values[0]).toFixed(1);
    for (var i = 1; i < n; i++) d += ' L' + px(i).toFixed(1) + ' ' + py(values[i]).toFixed(1);
    return { d: d, px: px, py: py };
  }

  /** 净值曲线（策略 vs 基准），带渐变填充与回撤区间标注
   *  period: 可选 '1M'/'3M'/'1Y'/'ALL'，截取尾部数据（按交易日近似）。
   */
  function navChart(el, result, period) {
    if (!el) return;
    var navPts = result.series.nav;
    var benchPts = result.series.benchmark_nav;
    // 每段区间对应的交易日数（252 交易日/年）
    var windowMap = { '1M': 21, '3M': 63, '1Y': 252, 'ALL': navPts.length };
    var win = windowMap[period] || navPts.length;
    if (win < navPts.length) {
      navPts = navPts.slice(navPts.length - win);
      benchPts = benchPts.slice(benchPts.length - win);
    }
    var nav = navPts.map(function (p) { return p.value; });
    var bench = benchPts.map(function (p) { return p.value; });
    var all = nav.concat(bench);
    var min = Math.min.apply(null, all) * 0.98;
    var max = Math.max.apply(null, all) * 1.02;

    var W = 360, H = 180, x0 = 34, y0 = 12, pw = W - 46, ph = H - 34;
    var navPath = buildPath(nav, x0, y0, pw, ph, min, max);
    var benchPath = buildPath(bench, x0, y0, pw, ph, min, max);
    var areaD = navPath.d + ' L' + (x0 + pw) + ' ' + (y0 + ph) + ' L' + x0 + ' ' + (y0 + ph) + ' Z';

    // 网格与 Y 轴标签
    var grid = '';
    for (var g = 0; g <= 3; g++) {
      var gy = y0 + (g / 3) * ph;
      var val = max - (g / 3) * (max - min);
      grid += '<line x1="' + x0 + '" y1="' + gy.toFixed(1) + '" x2="' + (x0 + pw) + '" y2="' + gy.toFixed(1) + '" stroke="' + GRID + '" stroke-width="1"/>';
      grid += '<text x="' + (x0 - 6) + '" y="' + (gy + 3).toFixed(1) + '" font-size="9" fill="' + TXT + '" text-anchor="end">' + val.toFixed(2) + '</text>';
    }
    // X 轴标注（起止日期）
    var first = navPts[0].date.slice(0, 7);
    var last = navPts[navPts.length - 1].date.slice(0, 7);

    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">'
      + '<defs><linearGradient id="navGrad" x1="0" y1="0" x2="0" y2="1">'
      + '<stop offset="0%" stop-color="' + UP + '" stop-opacity="0.18"/>'
      + '<stop offset="100%" stop-color="' + UP + '" stop-opacity="0"/>'
      + '</linearGradient></defs>'
      + grid
      + '<path d="' + areaD + '" fill="url(#navGrad)"/>'
      + '<path d="' + benchPath.d + '" fill="none" stroke="' + BENCH + '" stroke-width="1.6" stroke-dasharray="4 3"/>'
      + '<path d="' + navPath.d + '" fill="none" stroke="' + UP + '" stroke-width="2.4" stroke-linejoin="round"/>'
      + '<text x="' + x0 + '" y="' + (H - 4) + '" font-size="9" fill="' + TXT + '">' + first + '</text>'
      + '<text x="' + (x0 + pw) + '" y="' + (H - 4) + '" font-size="9" fill="' + TXT + '" text-anchor="end">' + last + '</text>'
      + '</svg>';
    el.innerHTML = svg;
  }

  /** 回撤曲线（水下曲线，红色填充） */
  function drawdownChart(el, result) {
    if (!el) return;
    var dd = result.series.drawdown.map(function (p) { return p.value; });
    var min = Math.min.apply(null, dd);
    var W = 360, H = 104, x0 = 34, y0 = 10, pw = W - 46, ph = H - 24;
    var path = buildPath(dd, x0, y0, pw, ph, min, 0);
    var areaD = path.d + ' L' + (x0 + pw) + ' ' + y0 + ' L' + x0 + ' ' + y0 + ' Z';

    var grid = '<line x1="' + x0 + '" y1="' + y0 + '" x2="' + (x0 + pw) + '" y2="' + y0 + '" stroke="' + GRID + '"/>'
      + '<text x="' + (x0 - 6) + '" y="' + (y0 + 3) + '" font-size="9" fill="' + TXT + '" text-anchor="end">0%</text>'
      + '<text x="' + (x0 - 6) + '" y="' + (y0 + ph + 3).toFixed(1) + '" font-size="9" fill="' + TXT + '" text-anchor="end">' + (min * 100).toFixed(0) + '%</text>'
      + '<line x1="' + x0 + '" y1="' + (y0 + ph) + '" x2="' + (x0 + pw) + '" y2="' + (y0 + ph) + '" stroke="' + GRID + '"/>';

    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">'
      + '<defs><linearGradient id="ddGrad" x1="0" y1="1" x2="0" y2="0">'
      + '<stop offset="0%" stop-color="' + UP + '" stop-opacity="0.28"/>'
      + '<stop offset="100%" stop-color="' + UP + '" stop-opacity="0.02"/>'
      + '</linearGradient></defs>'
      + grid
      + '<path d="' + areaD + '" fill="url(#ddGrad)"/>'
      + '<path d="' + path.d + '" fill="none" stroke="' + UP + '" stroke-width="1.8"/>'
      + '</svg>';
    el.innerHTML = svg;
  }

  /** 多策略净值对比 */
  function compareChart(el, results) {
    if (!el || !results.length) return;
    var colors = ['#e0392f', '#1a56db', '#0f9d58', '#7c3aed', '#ea580c', '#0891b2'];
    var seriesArr = results.map(function (r) { return r.series.nav.map(function (p) { return p.value; }); });
    var all = [].concat.apply([], seriesArr);
    var min = Math.min.apply(null, all) * 0.97, max = Math.max.apply(null, all) * 1.03;
    var W = 360, H = 180, x0 = 34, y0 = 12, pw = W - 46, ph = H - 34;
    var n = seriesArr[0].length;

    var grid = '';
    for (var g = 0; g <= 3; g++) {
      var gy = y0 + (g / 3) * ph;
      var val = max - (g / 3) * (max - min);
      grid += '<line x1="' + x0 + '" y1="' + gy.toFixed(1) + '" x2="' + (x0 + pw) + '" y2="' + gy.toFixed(1) + '" stroke="' + GRID + '"/>';
      grid += '<text x="' + (x0 - 6) + '" y="' + (gy + 3).toFixed(1) + '" font-size="9" fill="' + TXT + '" text-anchor="end">' + val.toFixed(2) + '</text>';
    }

    var paths = '';
    seriesArr.forEach(function (arr, idx) {
      var p = buildPath(arr, x0, y0, pw, ph, min, max);
      paths += '<path d="' + p.d + '" fill="none" stroke="' + colors[idx % colors.length] + '" stroke-width="2" stroke-linejoin="round"/>';
    });

    el.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' + grid + paths + '</svg>';
    return colors;
  }

  global.Charts = {
    navChart: navChart,
    drawdownChart: drawdownChart,
    compareChart: compareChart,
    COLORS: ['#e0392f', '#1a56db', '#0f9d58', '#7c3aed', '#ea580c', '#0891b2']
  };
})(window);
