# -*- coding: utf-8 -*-
"""
A股复盘 v4 渲染器 —— 模型只输出 report JSON，本脚本负责：
  1. 算术：T5~T8 重锚、间距校验、R21 矩阵落格、R24 仓位封顶、档位→仓位硬映射
  2. 状态流转：阈值继承、规则库继承（昨日「新增」→「生效」、合并退役、新规则编号）
  3. 标记：tref 占位符 {T5} → 完整 span；stack / data-label / wide 自动判定
  4. 产出：html（今日报告）+ state（明日输入）+ problems（校验问题）
仅依赖 Python 标准库，可直接放进 Coze 代码节点。
"""
RENDER_VERSION = 'v4.35'      # 每次改动递增；产出的 HTML 页脚与注释里都会带上，一眼可见是哪一版渲染的
import json, re, math, glob, os, html as _h

CSS = '\n:root{\n  --bg:#EFF1F4; --surface:#FFFFFF; --surface2:#F7F8FA;\n  --ink:#171C22; --muted:#5C6773; --faint:#8A939E;\n  --rule:#D6DBE1; --rule2:#E8EBEF;\n  --up:#C0392B;      /* A股惯例：红涨 */\n  --down:#16875A;    /* 绿跌 */\n  --accent:#23405E;  /* 靛蓝：结构线 */\n  --warn:#B26A00;\n  --pin-h:46px;\n}\n@media (prefers-color-scheme:dark){\n  :root{\n    --bg:#12161B; --surface:#1A1F26; --surface2:#20262E;\n    --ink:#E6EAEF; --muted:#9AA5B1; --faint:#6E7A87;\n    --rule:#2C333C; --rule2:#242A32;\n    --up:#E4645A; --down:#3FB98A; --accent:#7FA8D4; --warn:#D9A040;\n  }\n}\n*{box-sizing:border-box}\nhtml{-webkit-text-size-adjust:100%}\nbody{\n  margin:0; background:var(--bg); color:var(--ink);\n  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans SC","Helvetica Neue",sans-serif;\n  font-size:15px; line-height:1.62;\n  font-variant-numeric:tabular-nums; -moz-font-feature-settings:"tnum";\n  padding-bottom:calc(var(--pin-h) + env(safe-area-inset-bottom) + 12px);\n}\n.wrap{max-width:760px;margin:0 auto;padding:0 14px}\n\n/* ─── 顶栏 ─── */\nheader.bar{\n  position:sticky; top:0; z-index:40; background:var(--surface);\n  border-bottom:1px solid var(--rule);\n  padding-top:env(safe-area-inset-top);\n}\n.bar-in{display:flex;align-items:center;gap:10px;max-width:760px;margin:0 auto;padding:8px 14px}\n.bar-date{font-weight:700;font-size:14px;white-space:nowrap}\n.bar-gear{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}\n.bar-btn{\n  margin-left:auto;flex:none;border:1px solid var(--rule);background:var(--surface2);\n  color:var(--ink);border-radius:7px;padding:5px 11px;font-size:13px;font-family:inherit;cursor:pointer\n}\n.bar-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}\n\nnav#toc{display:none;background:var(--surface);border-bottom:1px solid var(--rule);\n  position:sticky;top:0;z-index:39}\nnav#toc.open{display:block}\nnav#toc ol{list-style:none;margin:0 auto;padding:6px 14px 12px;max-width:760px;\n  display:grid;grid-template-columns:1fr 1fr;gap:2px 10px}\nnav#toc a{display:block;padding:6px 2px;color:var(--ink);text-decoration:none;font-size:13.5px;\n  border-bottom:1px solid var(--rule2)}\nnav#toc a b{color:var(--accent);font-weight:600;margin-right:5px}\n\n/* ─── 通用块 ─── */\nsection{margin:18px 0}\nh2{\n  font-size:16px;margin:0 0 8px;padding-left:9px;\n  border-left:3px solid var(--accent);line-height:1.35;scroll-margin-top:60px\n}\nh2 small{display:block;font-size:12px;font-weight:400;color:var(--muted);margin-top:2px}\nh3{font-size:14px;margin:14px 0 6px;color:var(--accent)}\np{margin:8px 0}\n.lead{color:var(--muted);font-size:13.5px;margin:2px 0 10px}\n.card{background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:12px 13px;margin:10px 0}\n.card.warn{border-color:var(--warn);background:color-mix(in srgb,var(--warn) 7%,var(--surface))}\n.card.ok{border-color:var(--down);background:color-mix(in srgb,var(--down) 7%,var(--surface))}\n.card.bad{border-color:var(--up);background:color-mix(in srgb,var(--up) 7%,var(--surface))}\nul{margin:6px 0;padding-left:19px}\nli{margin:4px 0}\n.fill{color:var(--faint)}          /* 待填占位 */\n.na{color:var(--warn);font-size:12.5px}  /* 未取到 */\n.up{color:var(--up);font-weight:600}\n.down{color:var(--down);font-weight:600}\n\n/* ─── 0 · 摘要四格 ─── */\n.hero{background:var(--surface);border:1px solid var(--rule);border-radius:12px;overflow:hidden;margin:14px 0}\n.hero-top{padding:13px 14px 11px;border-bottom:1px solid var(--rule2)}\n.hero-top h1{font-size:19px;line-height:1.35;margin:0 0 5px}\n.hero-top .sub{font-size:13px;color:var(--muted);margin:0}\n.quad{display:grid;grid-template-columns:1fr 1fr}\n.quad div{padding:11px 13px;border-top:1px solid var(--rule2)}\n.quad div:nth-child(odd){border-right:1px solid var(--rule2)}\n.quad .k{font-size:11.5px;color:var(--faint);margin-bottom:1px}\n.quad .v{font-size:17px;font-weight:700;line-height:1.3}\n.quad .v .tref{font-size:13px;font-weight:600}\n.quad .n{font-size:12px;color:var(--muted);margin-top:2px}\n.hero-note{padding:11px 14px;border-top:1px solid var(--rule2);font-size:13.5px;background:var(--surface2)}\n\n/* ─── 表格 ─── */\n.tbwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--rule);\n  border-radius:9px;background:var(--surface);margin:10px 0}\n/* 嵌套在 details / card 里的表格做负边距出血，与顶层表格左右对齐，消除宽窄不一 */\n.det-body>.tbwrap,.card>.tbwrap{\n  margin-left:-13px;margin-right:-13px;width:auto;\n  border-left:none;border-right:none;border-radius:0\n}\n.det-body>.tbwrap:last-child,.card>.tbwrap:last-child{margin-bottom:-12px;border-bottom:none}\ntable{border-collapse:collapse;width:100%;font-size:13px;table-layout:auto}\n/* 列宽基线：编号/向/重锚/数值列定宽不换行，说明列吃剩余空间 */\nth:first-child,td:first-child{width:1%;white-space:nowrap}\ntd.tid,td.dir,td.re,td.g,th.num,td.num{width:1%;white-space:nowrap}\nth,td{overflow-wrap:anywhere}\ntable.wide{min-width:560px}   /* 列数 ≥5 且各格都短的表：窄屏横滑 */\n\n/* 长文本表：窄屏转为堆叠卡片，彻底不占横向空间。td 须带 data-label */\n@media (max-width:560px){\n  table.stack{min-width:0;display:block}\n  table.stack thead{display:none}\n  table.stack tbody{display:block}\n  table.stack tr{display:block;padding:11px 12px;border-bottom:1px solid var(--rule2)}\n  table.stack tr:last-child{border-bottom:none}\n  table.stack td{display:block;width:auto;border:none;padding:3px 0;white-space:normal;text-align:left}\n  table.stack td:empty{display:none}\n  table.stack td[data-label]::before{\n    content:attr(data-label);display:block;font-size:11px;color:var(--faint);\n    letter-spacing:.02em;margin-bottom:1px}\n  table.stack td.tid,table.stack td.dir,table.stack td.re,table.stack td.g,table.stack td.num{\n    width:auto;white-space:normal}\n  table.stack td.tid{font-size:14.5px;font-weight:700;padding-bottom:5px}\n  table.stack td.tid::before{display:none}\n  /* 标签类短字段并排一行 */\n  table.stack td.meta{display:inline-block;margin-right:14px;font-size:12px;color:var(--muted)}\n  table.stack caption{display:block;padding:8px 12px 2px}\n  /* 判分表：# 与 维度 直接显示值，不再占一行标签 */\n  table.stack td[data-label="#"]::before,table.stack td[data-label="维度"]::before{display:none}\n  table.stack td[data-label="#"]{font-weight:700;color:var(--accent);margin-right:6px}\n  table.stack td[data-label="维度"]{font-weight:600;font-size:14px}\n  table.stack td[data-label="推演 → 实际"]::before{margin-top:4px}\n}\ncaption{text-align:left;font-size:12.5px;color:var(--muted);padding:8px 11px 0;caption-side:top}\nth,td{padding:7px 10px;border-bottom:1px solid var(--rule2);text-align:left;vertical-align:top}\nth{background:var(--surface2);font-weight:600;font-size:12.5px;white-space:nowrap;color:var(--muted)}\ntr:last-child td{border-bottom:none}\ntd.num{text-align:right;white-space:nowrap}\n.src{font-size:11.5px;color:var(--faint);margin:-4px 0 10px;padding:0 2px}\n\n/* ─── 折叠 ─── */\ndetails{background:var(--surface);border:1px solid var(--rule);border-radius:10px;margin:10px 0}\ndetails>summary{\n  padding:10px 13px;cursor:pointer;font-size:14px;font-weight:600;list-style:none;\n  display:flex;align-items:center;gap:7px\n}\ndetails>summary::-webkit-details-marker{display:none}\ndetails>summary::before{content:"▸";color:var(--accent);font-size:12px;transition:transform .18s}\ndetails[open]>summary::before{transform:rotate(90deg)}\n@media (prefers-reduced-motion:reduce){details>summary::before{transition:none}}\ndetails>summary span{font-weight:400;font-size:12.5px;color:var(--muted);margin-left:auto}\n.det-body{padding:0 13px 12px;border-top:1px solid var(--rule2)}\n.det-body>.tbwrap:first-child{margin-top:11px}\n\n/* ─── 阈值系统 ─── */\ntd.tid{font-weight:700;white-space:nowrap}\ntr[data-dir="down"] td.tid{color:var(--down)}\ntr[data-dir="up"] td.tid{color:var(--up)}\ntd.dir{white-space:nowrap;font-size:12px}\ntr[data-dir="down"] td.dir{color:var(--down)}\ntr[data-dir="up"] td.dir{color:var(--up)}\n.tref{\n  display:inline-block;padding:1px 6px;border:1px solid var(--accent);border-radius:5px;\n  font-size:12px;color:var(--accent);white-space:nowrap;line-height:1.5\n}\n.tref.miss{border-color:var(--up);color:#fff;background:var(--up);font-weight:600}\n.tref.undef{border-style:dashed;border-color:var(--warn);color:var(--warn)}\n#tcheck{display:none;margin:10px 0}\n#tcheck.show{display:block}\n#pinbar{\n  position:fixed;left:0;right:0;bottom:0;z-index:50;height:calc(var(--pin-h) + env(safe-area-inset-bottom));\n  background:var(--surface);border-top:1px solid var(--rule);\n  padding-bottom:env(safe-area-inset-bottom);\n  display:flex;align-items:center;overflow-x:auto;-webkit-overflow-scrolling:touch;gap:7px;\n  padding-left:12px;padding-right:12px\n}\n#pinbar .chip{\n  flex:none;font-size:12px;padding:4px 9px;border-radius:6px;\n  border:1px solid var(--rule);background:var(--surface2);white-space:nowrap\n}\n#pinbar .chip b{margin-right:4px}\n#pinbar .chip.d b{color:var(--down)}\n#pinbar .chip.u b{color:var(--up)}\n#pinbar .lbl{flex:none;font-size:11px;color:var(--faint);margin-right:1px}\n\n/* ─── 已触发 / 重锚 ─── */\ntr[data-fired="1"]{background:color-mix(in srgb,var(--warn) 9%,transparent)}\ntr[data-fired="1"] td.tid::after{content:"已触发";display:inline-block;margin-left:5px;font-size:10px;\n  font-weight:600;color:#fff;background:var(--warn);border-radius:3px;padding:0 4px;vertical-align:1px}\n/* 正向阈值达成 ≠ 警报，用靛蓝「已达成」 */\ntr[data-dir="up"][data-fired="1"]{background:color-mix(in srgb,var(--accent) 9%,transparent)}\ntr[data-dir="up"][data-fired="1"] td.tid::after{content:"已达成";background:var(--accent)}\ntr[data-retired="1"]{opacity:.55}\ntr[data-retired="1"] td[data-label="规则"]{text-decoration:line-through;text-decoration-color:var(--faint)}\ntd.re{white-space:nowrap;font-size:11.5px;color:var(--faint)}\ntr[data-reanchor="daily"] td.re{color:var(--accent);font-weight:600}\n\n/* ─── 判分表：推演→实际 合并列 ─── */\n.pv,.ac{display:block}\n.pv::before{content:"推演";display:inline-block;font-size:10.5px;font-weight:600;color:#fff;\n  background:var(--accent);border-radius:3px;padding:0 5px;margin-right:6px;vertical-align:1px}\n.ac::before{content:"实际";display:inline-block;font-size:10.5px;font-weight:600;color:#fff;\n  background:var(--muted);border-radius:3px;padding:0 5px;margin-right:6px;vertical-align:1px}\n.pvsep{display:block;color:var(--faint);font-size:11px;line-height:1.2;margin:3px 0 2px}\n\n/* ─── 判分 ─── */\ntd.g{white-space:nowrap;font-weight:600}\ntd.g[data-g="N"]{color:var(--faint);font-weight:400}\ntd.g[data-g="A"]{color:var(--up)}\ntd.g[data-g="B"]{color:var(--up);opacity:.85}\ntd.g[data-g="C"]{color:var(--warn)}\ntd.g[data-g="D"]{color:var(--down)}\n.scorebar{display:flex;height:9px;border-radius:5px;overflow:hidden;margin:9px 0 5px;background:var(--rule2)}\n.scorebar i{display:block}\n.scorekey{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--muted)}\n.scorekey s{text-decoration:none;display:inline-flex;align-items:center;gap:4px}\n.dot{width:8px;height:8px;border-radius:2px;display:inline-block}\n\n/* ─── 情绪档位条 ─── */\n.gears{display:flex;gap:3px;margin:10px 0 4px}\n.gears i{flex:1;height:6px;border-radius:3px;background:var(--rule2)}\n.gears i.on{background:var(--accent)}\n.gearlab{display:flex;justify-content:space-between;font-size:11px;color:var(--faint)}\n\nfooter{margin:26px 0 10px;font-size:12px;color:var(--faint);line-height:1.7}\nhr.sep{border:none;border-top:1px solid var(--rule);margin:22px 0}\n' + '''
/* v4：长 tref 允许折行，避免窄屏横向溢出 */
.tref{white-space:normal;display:inline;line-height:1.95;-webkit-box-decoration-break:clone;box-decoration-break:clone}
.quad .v .tref{display:inline}
/* v4：四格内层 div 不继承格子边框 */
.quad div div{padding:0;border:none}
/* v4.18：摘要卡改两行——第 1 行 档位｜仓位；第 2 行起「明日动作」「最近失效线」各占整行，长文字不再挤在半格里 */
.quad>div:nth-child(n+3){grid-column:1/-1;border-right:none}
.quad>div:nth-child(n+3) .v{font-size:15px;font-weight:600;line-height:1.55}
'''
JS = '\n(function(){\n  "use strict";\n\n  var meta = {};\n  try { meta = JSON.parse(document.getElementById(\'meta\').textContent); } catch(e){}\n\n  var GEARS = [\'冰点\',\'修复\',\'发酵\',\'高潮\',\'退潮1\',\'退潮2\',\'退潮3\'];\n  var POSMAP = [0.10,0.30,0.50,0.30,0.30,0.25,0.10];\n\n  function txt(id,v){ var el=document.getElementById(id); if(el&&v!=null) el.textContent=v; }\n  function norm(s){ return String(s).replace(/\\s+/g,\' \').trim(); }\n\n  /* ── 顶栏 / 摘要 ── */\n  if(meta.date) txt(\'hDate\', meta.date + (meta.weekday? \' \'+meta.weekday : \'\'));\n  var gi = (typeof meta.gear_index===\'number\') ? meta.gear_index : -1;\n  var gname = meta.gear || (gi>=0 ? GEARS[gi] : \'\');\n  var pos = (typeof meta.position_max===\'number\') ? meta.position_max : (gi>=0 ? POSMAP[gi] : null);\n  var posTxt = pos!=null ? Math.round(pos*100)+\'%\' : \'—\';\n  if(gname){ txt(\'hGear\', gname+\' · 仓位≤\'+posTxt); txt(\'qGear\', gname); }\n  txt(\'qPos\', posTxt);\n\n  var g = document.getElementById(\'gears\');\n  if(g){ for(var i=0;i<7;i++){ var b=document.createElement(\'i\'); if(i===gi) b.className=\'on\'; g.appendChild(b);} }\n\n  /* ── 阈值真源 ── */\n  var TH = {}, fired = [], nextRow = null;\n  document.querySelectorAll(\'#tbl-thresholds tbody tr[data-t]\').forEach(function(tr){\n    var id = tr.getAttribute(\'data-t\');\n    var td = tr.querySelectorAll(\'td\');\n    TH[id] = {\n      dir:  tr.getAttribute(\'data-dir\') || \'down\',\n      item: td[3] ? norm(td[3].textContent) : \'\',\n      val:  td[4] ? norm(td[4].textContent) : \'\',\n      pin:  tr.getAttribute(\'data-pin\') === \'1\',\n      fired: tr.getAttribute(\'data-fired\') === \'1\',\n      daily: tr.getAttribute(\'data-reanchor\') === \'daily\',\n      dist: tr.getAttribute(\'data-dist\') || \'\'\n    };\n    if(TH[id].fired) fired.push(id);\n    if(tr.getAttribute(\'data-next\') === \'1\' && !nextRow) nextRow = id;\n  });\n\n  /* 摘要卡：最近失效线 */\n  if(nextRow && TH[nextRow]){\n    var nt = TH[nextRow];\n    txt(\'qNext\', nextRow+\' \'+nt.item+\' \'+nt.val);\n    txt(\'qNextDist\', nt.dist || \'明日最先见分晓\');\n  }\n\n  /* ── tref 校验器：静态文本优先，JS 只比对 ── */\n  var bad = [], undef = [];\n  document.querySelectorAll(\'.tref[data-t]:not([data-prev])\').forEach(function(s){\n    var id = s.getAttribute(\'data-t\'), t = TH[id];\n    if(!t){\n      s.classList.add(\'undef\');\n      s.title = \'阈值表中未定义 \' + id;\n      if(undef.indexOf(id)<0) undef.push(id);\n      if(!norm(s.textContent)) s.textContent = id + \' ?\';\n      return;\n    }\n    var want = norm(id+\' \'+t.item+\' \'+t.val);\n    var have = norm(s.textContent);\n    if(!have){ s.textContent = want; return; }        // 空则兜底填充\n    if(have !== want){\n      s.classList.add(\'miss\');\n      s.title = \'与阈值表不一致，应为：\' + want;\n      bad.push({id:id, have:have, want:want});\n    }\n  });\n\n  var box = document.getElementById(\'tcheck\');\n  var probs = [];\n\n  /* 指数关口间距校验 */\n  function numOf(id){\n    if(!TH[id]) return null;\n    var m = String(TH[id].val).match(/(\\d{3,5}(?:\\.\\d+)?)/);\n    return m ? parseFloat(m[1]) : null;\n  }\n  var close = (typeof meta.index_close===\'number\' && meta.index_close>0) ? meta.index_close : null;\n  var t5=numOf(\'T5\'), t6=numOf(\'T6\'), t7=numOf(\'T7\'), t8=numOf(\'T8\');\n  function gap(a,b,min,la,lb){\n    if(a==null||b==null||!close) return;\n    if(a<=b){ probs.push(la+\'(\'+a+\') 必须高于 \'+lb+\'(\'+b+\')\'); return; }\n    var d=(a-b)/close;\n    if(d < min) probs.push(la+\'(\'+a+\') 与 \'+lb+\'(\'+b+\') 仅相距 \'+(d*100).toFixed(2)+\'%，须 ≥\'+(min*100).toFixed(1)+\'%——贴太近会让两个动作同时触发\');\n  }\n  gap(t5,t6,0.002,\'T5 支撑\',\'T6 防守\');\n  gap(t8,t7,0.003,\'T8 进攻\',\'T7 前关\');\n\n  if(undef.length) probs.push(\'未在本表定义的编号：\'+undef.join(\'、\'));\n  bad.forEach(function(x){ probs.push(x.id+\' 正文写「\'+x.have+\'」，应为「\'+x.want+\'」\'); });\n  if(!nextRow) probs.push(\'没有任何一行标 data-next="1"，摘要卡「最近失效线」无法生成\');\n\n  /* 仓位跃升速率校验（R24） */\n  var pg = meta.prev_gear_index;\n  if(typeof gi===\'number\' && gi>=0 && gi<7 && typeof pg===\'number\' && pg>=0 && pg<7){\n    var prevPos = POSMAP[pg], nowPos = (pos!=null?pos:POSMAP[gi]);\n    var jump = nowPos - prevPos;\n    if(jump > 0.2001){\n      var capPos = prevPos + 0.20, best = -1;\n      POSMAP.forEach(function(v,i){\n        if(v <= capPos + 1e-9 && (best<0 || v > POSMAP[best])) best = i;\n      });\n      probs.push(\'仓位单日提升 \'+Math.round(jump*100)+\'pp（\'+GEARS[pg]+\' \'+Math.round(prevPos*100)\n        +\'% → \'+GEARS[gi]+\' \'+Math.round(nowPos*100)+\'%），R24 限制 ≤20pp——当日上限 \'\n        +Math.round(capPos*100)+\'%，应先取 \'+GEARS[best]+\'（\'+Math.round(POSMAP[best]*100)\n        +\'%），次日读数确认后再补足\');\n    }\n  }\n\n  if(box && probs.length){\n    box.innerHTML = \'<b style="font-size:13.5px">阈值校验未通过</b><ul style="margin:6px 0 0;padding-left:19px;font-size:13px"><li>\'\n                  + probs.join(\'</li><li>\') + \'</li></ul>\';\n    box.classList.add(\'show\');\n  }\n  var fbox = document.getElementById(\'tfired\');\n  if(fbox){\n    var hitDown = fired.filter(function(k){ return TH[k].dir!==\'up\'; });\n    var hitUp   = fired.filter(function(k){ return TH[k].dir===\'up\'; });\n    var dailyFired = hitDown.filter(function(k){ return TH[k].daily; });\n    function fmt(k){ return k+\'（\'+TH[k].item+\' \'+TH[k].val+\'）\'+(TH[k].daily?\'\':\'<span style="color:var(--faint)">·常量</span>\'); }\n    var h=\'\';\n    h += \'<b style="font-size:13.5px">今日已触发（▼ 反向）</b>：\' + (hitDown.length ? hitDown.map(fmt).join(\'、\') : \'无\');\n    if(hitUp.length) h += \'<div style="margin-top:5px"><b style="font-size:13.5px;color:var(--accent)">今日已达成（▲ 正向）</b>：\'\n      + hitUp.map(fmt).join(\'、\') + \'</div>\';\n    h += \'<div style="font-size:12.5px;color:var(--muted);margin-top:4px">\'\n      + (dailyFired.length\n          ? \'其中 \'+dailyFired.join(\'、\')+\' 属每日重锚类，<b>次日必须换档重设</b>，触发过的数值不得复用。carry 常量类触发后不重设。\'\n          : \'反向触发项中无每日重锚类，次日不需换档。\')\n      + \'判定时点＝20:00 最新报价，盘中触发已回落的不计。</div>\';\n    fbox.innerHTML = h;\n  }\n\n  /* ── 底部浮条 ── */\n  var pin = document.getElementById(\'pinbar\');\n  Object.keys(TH).forEach(function(k){\n    if(!TH[k].pin || TH[k].fired) return;\n    var c = document.createElement(\'span\');\n    c.className = \'chip \' + (TH[k].dir===\'up\' ? \'u\' : \'d\');\n    c.innerHTML = \'<b>\'+(TH[k].dir===\'up\'?\'▲\':\'▼\')+k+\'</b>\'+TH[k].item+\' \'+TH[k].val;\n    pin.appendChild(c);\n  });\n  if(pin.children.length<=1) pin.style.display=\'none\';\n\n  /* ── 判分分布（仅统计判分表内） ── */\n  var LAB = {A:[\'🎯 精确\',\'var(--up)\'],B:[\'✅ 命中\',\'var(--up)\'],C:[\'⚠️ 半对\',\'var(--warn)\'],D:[\'❌ 判错\',\'var(--down)\']};\n  var cnt = {A:0,B:0,C:0,D:0}, total=0, nCnt=0;\n  document.querySelectorAll(\'#tbl-scores td.g[data-g]\').forEach(function(td){\n    var k = td.getAttribute(\'data-g\');\n    if(k === \'N\'){ nCnt++; return; }            // N = 条件未触发，不计入分母\n    if(cnt[k]!=null){ cnt[k]++; total++; }\n  });\n  var bar = document.getElementById(\'scorebar\'), key = document.getElementById(\'scorekey\');\n  if(bar && total){\n    [\'A\',\'B\',\'C\',\'D\'].forEach(function(k){\n      if(!cnt[k]) return;\n      var seg=document.createElement(\'i\');\n      seg.style.width=(cnt[k]/total*100)+\'%\';\n      seg.style.background=LAB[k][1];\n      if(k===\'B\') seg.style.opacity=\'.62\';\n      bar.appendChild(seg);\n    });\n    var hit = ((cnt.A+cnt.B)/total*100).toFixed(0);\n    var parts = [\'A\',\'B\',\'C\',\'D\'].map(function(k){\n      return \'<s><i class="dot" style="background:\'+LAB[k][1]+(k===\'B\'?\';opacity:.62\':\'\')+\'"></i>\'+LAB[k][0]+\' \'+cnt[k]+\'</s>\';\n    });\n    if(nCnt) parts.push(\'<s><i class="dot" style="background:var(--faint)"></i>◻ 未触发 \'+nCnt+\'（不计入）</s>\');\n    parts.push(\'<s style="font-weight:600;color:var(--ink)">命中率 \'+hit+\'%（A+B ÷ \'+total+\'）</s>\');\n    key.innerHTML = parts.join(\'\');\n    if(meta.scores && meta.scores.length !== (total+nCnt)){\n      key.innerHTML += \'<s style="color:var(--up);font-weight:600">meta 有 \'+meta.scores.length+\' 条，表内 \'+(total+nCnt)+\' 条，不一致</s>\';\n    }\n  } else if(key){ key.textContent=\'暂无判分数据\'; }\n\n  /* ── 目录 ── */\n  var btn=document.getElementById(\'tocBtn\'), toc=document.getElementById(\'toc\');\n  btn.addEventListener(\'click\', function(){\n    var open = toc.classList.toggle(\'open\');\n    btn.setAttribute(\'aria-expanded\', open?\'true\':\'false\');\n    btn.textContent = open ? \'收起\' : \'目录\';\n  });\n  toc.addEventListener(\'click\', function(e){\n    if(e.target.closest(\'a\')){\n      toc.classList.remove(\'open\');\n      btn.setAttribute(\'aria-expanded\',\'false\');\n      btn.textContent=\'目录\';\n    }\n  });\n})();\n'
BLK = {'reanchor': '<div class="card">\n    <b style="font-size:13.5px">重锚规则（每日执行）</b>\n    <div class="tbwrap" style="margin:8px 0 0"><table class="stack">\n      <thead><tr><th>类别</th><th>编号</th><th>重设口径</th></tr></thead>\n      <tbody>\n        <tr><td class="meta" data-label="类别">指数·下沿</td><td class="tid" data-label="编号">T5·T6</td><td data-label="重设口径">T5 支撑＝收盘 −1.0%；T6 防守＝收盘 −1.5%。<b>只按次日收盘判定</b>，盘中跌破又收回不算触发（v4.17·10/22 复评）</td></tr>\n        <tr><td class="meta" data-label="类别">指数·上沿</td><td class="tid" data-label="编号">T7·T8</td><td data-label="重设口径">T7 前关＝收盘 +1.0%；T8 进攻＝收盘 +1.5%。<b>只按次日收盘判定</b>——贴太近会让「守住维持仓位」和「攻破才加仓」同时触发，两个动作退化成一个</td></tr>\n        <tr><td class="meta" data-label="类别">主线双向</td><td class="tid" data-label="编号">T9·T10</td><td data-label="重设口径">按第 8 节判定出的当日主攻线龙头重写</td></tr>\n        <tr><td class="meta" data-label="类别">外盘映射</td><td class="tid" data-label="编号">T13</td><td data-label="重设口径">按当日主攻线对应的外盘细分重写</td></tr>\n        <tr><td class="meta" data-label="类别">商品锚</td><td class="tid" data-label="编号">T14·T15</td><td data-label="重设口径">按当日商品价位重设上下沿，两沿之间不得留无定义区间</td></tr>\n        <tr><td class="meta" data-label="类别">规则驱动</td><td class="tid" data-label="编号">R·重锚/降档类</td><td data-label="重设口径">当日新增的重锚/降档类规则<b>必须真正改变数值</b>，并在规则行注明偏离幅度，例如「T5 取当日低点 3852 而非收盘 -0.3%（3876），因 R13 恐慌出清」。只写「已应用：T5=3878」而该值等于标准口径＝没应用</td></tr>\n        <tr><td class="meta" data-label="类别">已触发</td><td class="tid" data-label="编号">daily 类 data-fired</td><td data-label="重设口径">次日必须换档重设，触发过的数值不再复用。例：布油破 100 触发后须上移（破 105 / 站稳 100 上方满 3 日），否则该阈值永远处于触发态＝失效</td></tr>\n        <tr><td class="fill meta" data-label="类别">已触发·例外</td><td class="tid" data-label="编号">carry 类 data-fired</td><td data-label="重设口径"><b>carry 类即使触发也不重设。</b>「涨停 &lt; 45 家」是判据不是价位，触发的是市场不是阈值本身</td></tr>\n        <tr><td class="meta" data-label="类别">可沿用</td><td class="tid" data-label="编号">T1·T2·T3·T4·T11·T12·T16</td><td data-label="重设口径">体系常量，仅在方法论修订时改动</td></tr>\n      </tbody>\n    </table></div>\n  </div>', 'scoring': '<div class="card">\n    <b style="font-size:13.5px">判分口径（防虚高）</b>\n    <div class="tbwrap" style="margin:8px 0 0"><table>\n      <thead><tr><th>档</th><th>含义</th><th>适用</th></tr></thead>\n      <tbody>\n        <tr><td class="g" data-g="A">🎯 A</td><td>数值/位置级别的准确</td><td><b>仅限带具体数值的预测</b>。回避类、纪律类判断天然拿不到 A，上限为 B</td></tr>\n        <tr><td class="g" data-g="B">✅ B</td><td>方向对</td><td>方向性预测兑现</td></tr>\n        <tr><td class="g" data-g="C">⚠️ C</td><td>方向对但路径/幅度显著偏离，或部分成立</td><td>不计入命中</td></tr>\n        <tr><td class="g" data-g="D">❌ D</td><td>反向兑现</td><td>—</td></tr>\n        <tr><td class="g" data-g="N">◻ N</td><td>条件句两侧均未触发，无从验证</td><td><b>不计入分母</b>。例：「失守 T5 减半 / 站上 T8 加仓」当日两者都没发生 → N，不得记命中</td></tr>\n      </tbody>\n    </table></div>\n  </div>', 'posmap': '<div class="card">\n    <b style="font-size:13.5px">档位→仓位硬映射</b>\n    <div class="tbwrap" style="margin:8px 0 0"><table>\n      <thead><tr><th>档位</th><th class="num">仓位上限</th><th>动作</th></tr></thead>\n      <tbody>\n        <tr><td>冰点</td><td class="num">10%</td><td>只观察，等第一根反包</td></tr>\n        <tr><td>修复</td><td class="num">30%</td><td>低吸首板 / 低位核心</td></tr>\n        <tr><td>发酵</td><td class="num">50%</td><td>主线核心可持有</td></tr>\n        <tr><td>高潮</td><td class="num">30%</td><td>只减不加，兑现</td></tr>\n        <tr><td>退潮1</td><td class="num">30%</td><td>只做新主线回踩</td></tr>\n        <tr><td>退潮2</td><td class="num">25%</td><td>只做映射/防御核心回踩</td></tr>\n        <tr><td>退潮3</td><td class="num">10%</td><td>观察仓，等冰点</td></tr>\n      </tbody>\n    </table></div>\n  </div>', 'matrix': '<div class="card">\n    <b style="font-size:13.5px">档位二维判定矩阵（R21）</b>\n    <div class="tbwrap" style="margin:8px 0 0"><table>\n      <thead><tr><th>涨停家数</th><th class="num">封板率 &lt;60%</th><th class="num">60~65%</th><th class="num">≥65%</th></tr></thead>\n      <tbody>\n        <tr><td>≥60</td><td class="num">虚火·不上修</td><td class="num">发酵</td><td class="num">发酵/高潮</td></tr>\n        <tr><td>50~59</td><td class="num"><b>虚火·不上修</b></td><td class="num">持平</td><td class="num">修复</td></tr>\n        <tr><td>45~49</td><td class="num">退潮1</td><td class="num">退潮1</td><td class="num">持平</td></tr>\n        <tr><td>&lt;45</td><td class="num">退潮2/3</td><td class="num">退潮2</td><td class="num">退潮1</td></tr>\n      </tbody>\n    </table></div>\n    <p class="src" style="margin:8px 0 0">涨停家数高而封板率低＝虚火，是次日崩塌的前兆，此时档位只能持平或下修，不得因涨停数好看而上修。跌家 &gt; 3000 时再下修一档（R18）。</p>\n    <div class="card warn" style="margin:10px 0 0">\n      <b style="font-size:13px">跳档速率限制 + 主线判分拆 2a/2b · 隔夜催化推翻熄火（R24）</b>\n      <div style="font-size:13px;margin-top:4px">\n        矩阵是<b>状态函数</b>，只看今日读数、不看昨日在哪，因此允许任意跳档。实盘上一天把仓位翻倍是危险的——脉冲日（事件驱动的一次性放量涨停潮）次日常被打回。所以：\n        <ul style="margin:6px 0 0">\n          <li><b>仓位上限单日提升不超过 20 个百分点。</b>档位是周期序（…退潮3 → 冰点 → 修复 → 发酵…），「几档」不是可靠的度量；真正要限的是钱的速度。矩阵指向的档位若使仓位跃升超过 20pp，当日只取仓位 ≤ 前日+20pp 的最近档位，次日读数确认后再补足</li>\n          <li>例：退潮2（25%）→ 矩阵指向发酵（50%）＝ +25pp 超限，当日只到修复（30%），次日涨停仍 ≥60 再进发酵</li>\n          <li><b>下调不受限制</b>——风险要跑得快，矩阵指向哪档就到哪档</li>\n          <li>meta 须同时填 <code>gear_index</code> 与 <code>prev_gear_index</code>，页面自动校验</li>\n        </ul>\n      </div>\n    </div>\n  </div>', 'r27': '<div class="card warn">\n    <b style="font-size:13.5px">隔夜催化可推翻熄火判定（R27）</b>\n    <div style="font-size:13px;margin-top:4px">\n      排序表只看当日 A 股读数，对隔夜外盘催化零权重——这是 7 天战绩里「主线延续/反转」判错 3 次的直接原因（09-17 判半导体资金流出＝熄火，09-18 隔夜费半 +3.14%、龙头业绩指引上调，半导体强势归来）。\n      <b>产业级隔夜催化可推翻「昨日资金流出」的熄火判定，让该候选直接重回排序池。</b>四类判据满足其一即算：\n      <ul style="margin:6px 0 0">\n        <li>外盘对应细分指数/个股涨幅 <b>&gt; 3%</b>（费半、光通信、存储等，须能对上具体细分）</li>\n        <li>龙头业绩指引上调 / 产业龙头公开表态（如芯片销量翻倍）</li>\n        <li>缺货涨价落地（报价单、渠道涨价函）</li>\n        <li>新品发布或提前发布</li>\n      </ul>\n      「隔夜催化」列写 <code>★ &lt;判据&gt;（读数）</code>，无则写 <code>—</code>。<b>带 ★ 的候选不得因昨日资金流出被排除在排序表之外</b>；其连续性验证若因昨日流出而 ✗，可凭 ★ 直接通过，在该列注明 <code>✓ 隔夜催化推翻熄火（路径C）</code>。\n    </div>\n  </div>', 'lr': '<details>\n    <summary>六闸门左右定义表<span>R10：逐条对照，不得凭感觉</span></summary>\n    <div class="det-body">\n      <div class="tbwrap"><table>\n        <thead><tr><th>#</th><th>判据</th><th class="down">左（有利）</th><th class="up">右（风险）</th></tr></thead>\n        <tbody>\n          <tr><td>1</td><td>驱动类型</td><td>可跟踪的产业事实：涨价落地、订单/中标、展会日程、政策文件、财报数据</td><td>不可控外部变量：地缘、天气、汇率、传闻、单纯的情绪接力</td></tr>\n          <tr><td>2</td><td>已走天数</td><td>≤2 天（首日或次日）</td><td>≥4 天，或高标已进入加速段</td></tr>\n          <tr><td>3</td><td>资金二阶</td><td>板块主力净流入且龙头净流入居前</td><td>板块净流出，或龙头涨停伴特大单净卖出</td></tr>\n          <tr><td>4</td><td>广度</td><td>≥3 只涨停或跟风股普涨</td><td>仅龙头独强、跟风不接力，或已现炸板潮</td></tr>\n          <tr><td>5</td><td>龙头形态</td><td>封板牢固、封单居前、日内不开板</td><td>炸板、冲高回落、封单薄、主力转净流出</td></tr>\n          <tr><td>6</td><td>次日高开分歧</td><td>预计平开或小幅低开（给回踩机会）</td><td>预计高开 &gt; 3%（触 T16 无从进场）</td></tr>\n        </tbody>\n      </table></div>\n      <p class="src">「消息驱动」不等于右——产业涨价、库存告急属可跟踪产业事实，判左；只有不可控外部变量与纯情绪接力才判右。</p>\n    </div>\n  </details>', 'noon': '<div class="card">\n    <b style="font-size:13.5px">午间三问（持仓 &gt; 0 时 11:30 执行）</b>\n    <ul>\n      <li>方向对不对 —— 跟踪线上午是「回踩不破」还是「高开回落」</li>\n      <li>龙头兑现没兑现 —— 有无涨停派发 / 封板松动</li>\n      <li>该不该降仓 —— 已现证伪信号则下午减仓，不赌尾盘</li>\n    </ul>\n  </div>', 'rolling': '<div class="card">\n    <b style="font-size:13.5px">滚动战绩</b>\n    <p style="margin:5px 0 0;font-size:13px;color:var(--muted)">\n      近 10 / 20 日分维度命中率由「复盘战绩聚合台」批量读取历史日报的 meta 生成，<b>本节不自行计算</b>。\n      单日数据算不出趋势，硬算只会写错。<br>\n      聚合台会自动告警：某维度连续 3 日未命中，或近 10 日命中率低于 50% → 该维度判据须在第 3 节改写。\n    </p>\n  </div>', 'path': '<p class="src"><b>两步走</b>：先按「主力净流入绝对额 &gt; 涨停家数 &gt; 龙头封板强度」排序，再对第 1 名做<b>连续性验证</b>。两条路径，<b>满足其一即可，但只能选一条写</b>：</p>\n  <div class="tbwrap" style="margin:6px 0 10px"><table class="stack">\n    <thead><tr><th>路径</th><th>判据</th><th>写法</th></tr></thead>\n    <tbody>\n      <tr><td class="meta" data-label="路径">A · 净流入连续</td><td data-label="判据">昨日该板块净流入<b>排名前 3</b>（硬标准，不凭感觉判「居前」），且今日仍第 1</td><td data-label="写法"><code>✓ 连续 2 日前3（昨日第2）</code></td></tr>\n      <tr><td class="meta" data-label="路径">B · 龙头连板晋级</td><td data-label="判据">龙头板数较昨日递增（1 板→2 板、2 板→3 板…）</td><td data-label="写法"><code>✓ 龙头连板晋级（路径B）</code></td></tr>\n      <tr><td class="meta" data-label="路径">C · 隔夜催化推翻</td><td data-label="判据">昨日资金流出导致路径 A 失效，但存在产业级隔夜催化（见上方 R27 四类判据）</td><td data-label="写法"><code>✓ 隔夜催化推翻熄火（路径C）</code></td></tr>\n      <tr><td class="meta" data-label="路径">✗ 未通过</td><td data-label="判据">两条都不满足</td><td data-label="写法"><code>✗ 昨日第5名、无连板</code></td></tr>\n    </tbody>\n  </table></div>\n  <p class="src"><b>只写一条路径，括号里只能放补充说明，不得放另一条路径的依据。</b>\n  反面例子：<code>✓ 连续 2 日居前（中晶 2 板晋级）</code>——主路径声称净流入连续但昨日实际排第 3 名开外，真正让它过关的是括号里的连板晋级；用真实依据给不实的主路径背书，等于既没说实话也没说清楚。该写 <code>✓ 龙头连板晋级（路径B）</code>。<br>\n  第 1 名验证不通过则降为次线、<b>主攻线当日空缺</b>，摘要卡与 <code>meta.mainline</code> 写「无主攻线」。<br>\n  注意：龙头封板强度（排序第三优先级，看封单额）与龙头连板晋级（连续性验证路径 B，看板数递增）是两件事，不得混在同一格里。<br>\n  排序优先级：<b>主力净流入绝对额 &gt; 涨停家数 &gt; 龙头封板强度</b>。第 1 名写进 <code>meta.mainline</code> 和第 0 节摘要，必须是<b>具体细分</b>（如「PCB/覆铜板材料」），不得写成「元件涨价」这类大类。并列不允许——净流入相同时看涨停家数，再相同看封板强度。</p>'}

# 子行业 → 母行业：子行业的主力净流入绝对值不应大于母行业（口径混用的常见破绽）
PARENT = {
    'PCB': '元件', '印制电路板': '元件', '覆铜板': '元件', '被动元件': '元件', 'MLCC': '元件',
    '固态电池': '电池', '锂电池': '电池', '电池化学品': '电池', '光伏电池组件': '电池',
    '房地产开发': '房地产', '房地产服务': '房地产', '园区开发': '房地产',
    '影视院线': '文化传媒', '出版': '文化传媒', '游戏': '文化传媒', '广告营销': '文化传媒',
    '半导体设备': '半导体', '半导体材料': '半导体', '数字芯片设计': '半导体', '集成电路封测': '半导体',
    '光模块': '通信设备', '通信设备': '通信', '通信网络设备及器件': '通信',
    '军工电子': '国防军工', '航天装备': '国防军工', '航空装备': '国防军工',
    '创新药': '医药生物', '化学制药': '医药生物', '生物制品': '医药生物', '医疗器械': '医药生物',
    '工业金属': '有色金属', '贵金属': '有色金属', '能源金属': '有色金属',
    '养殖': '农林牧渔', '种植业': '农林牧渔', '饲料': '农林牧渔',
    '白酒': '食品饮料', '乳品': '食品饮料',
}
GEARS  = ['冰点', '修复', '发酵', '高潮', '退潮1', '退潮2', '退潮3']
POSMAP = [0.10, 0.30, 0.50, 0.30, 0.30, 0.25, 0.10]
DIMS   = ['核心观点', '主线延续/反转', '主线细分选择', '情绪档位', '指数关口', '个股/资金']
GRADE_TXT = {'A': '🎯 精确', 'B': '✅ 命中', 'C': '⚠️ 半对', 'D': '❌ 判错', 'N': '◻ 未触发'}
CALIBER = 'v2-20261023'   # v4.17：判分口径版本（10/22 复评后），聚合台据此分段统计
DAILY  = {'T5', 'T6', 'T7', 'T8', 'T9', 'T10', 'T13', 'T14', 'T15'}
# 7b 与两情景只能引用「隔夜可验证」的阈值：外盘 / 商品 / 中概
OVERNIGHT_KW = ('外盘', '纳指', '费半', '美债', '美元', '中概', '金龙', '商品', '布油', 'WTI', '黄金',
                '铜', '金价', '标普', '道指', 'VIX', '离岸人民币', '利率', '10Y', '美股', '港股', '恒生', '恒指')
SCOPE_FIXED = {**{'T%d' % i: 'intraday' for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16)},
               **{'T%d' % i: 'overnight' for i in (11, 12, 13, 14, 15, 17)}}   # v4.28：体系常量的作用域固定，不靠关键词猜


def scope_of(t):
    if t.get('id') in SCOPE_FIXED:
        return SCOPE_FIXED[t['id']]
    if t.get('scope') in ('overnight', 'intraday'):
        return t['scope']
    blob = '%s %s %s' % (t.get('item', ''), t.get('val', ''), t.get('meaning', ''))   # v4.28：也看 val
    return 'overnight' if any(k in blob for k in OVERNIGHT_KW) else 'intraday'


# ───────────────────────── 文本工具 ─────────────────────────
def esc(x):
    return _h.escape('' if x is None else str(x), quote=True)


class Ctx:
    """渲染上下文：阈值表 + 问题收集"""
    def __init__(self):
        self.TH = {}          # id -> dict(item,val,dir,...)
        self.problems = []    # 硬错误（页面顶部红框）
        self.warnings = []    # 提示（不阻断）

    def tref(self, tid):
        t = self.TH.get(tid)
        if not t:
            self.problems.append('正文引用了未定义的阈值 {%s}' % tid)
            return '<span class="tref undef" data-t="%s">%s ?</span>' % (tid, tid)
        return '<span class="tref" data-t="%s">%s</span>' % (tid, esc('%s %s %s' % (tid, t['item'], t['val'])))

    def rich_prev(self, x, prev_th):
        """v4.11：§2 的「推演」是昨天写的，其中 {Tn} 须按昨天的阈值值显示（昨天的 T5=3792 不能显示成今天的 T5=3754）。
        昨日 state 里没有该编号时回退为今日值。span 带 data-prev，页面校验器跳过它。"""
        _saved = self.TH
        try:
            _m = dict(_saved)
            for _k, _v in (prev_th or {}).items():
                if _k in _m:
                    _x = dict(_m[_k]); _x['item'] = _v.get('item', _x.get('item')); _x['val'] = _v.get('val', _x.get('val')); _m[_k] = _x
            self.TH = _m
            _h = self.rich(x)
        finally:
            self.TH = _saved
        return _h.replace('<span class="tref" data-t=', '<span class="tref" data-prev="1" data-t=')

    def rich(self, x):
        """转义 + **粗体** + 换行 + {Tn} 占位符"""
        if x is None or x == '':
            return ''
        if isinstance(x, (int, float)):
            return esc(x)
        s = esc(x)
        s = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', s)
        s = s.replace('\n', '<br>')
        s = re.sub(r'\{(T\d+)\}', lambda m: self.tref(m.group(1)), s)
        return s


def plain_len(x):
    return len(re.sub(r'\{T\d+\}', 'Txx 占位占位占位', str(x or '')))


# ───────────────────────── 通用表格 ─────────────────────────
def table(ctx, head, rows, caption=None, num=None, force_stack=None, tid_col=None, cls_extra=''):
    """num: 数值列下标集合；是否 wide / stack 自动判定"""
    num = set(num or [])
    ncol = len(head)
    # v4.16：表格行应为数组；若写成一段文字，先按「｜/|」拆格，拆不出就整段放第一格（不再逐字拆）
    _rows = []
    for r in rows or []:
        if isinstance(r, str):
            parts = [x.strip() for x in re.split(r'\s*[｜|]\s*', r) if x.strip()]
            if len(parts) > ncol:
                parts = parts[:ncol - 1] + ['｜'.join(parts[ncol - 1:])]
            if len(parts) < 2:
                parts = [r.strip()]
            ctx.warnings.append('表格「%s」有一行应为数组，收到一段文字，已%s' % (
                re.sub(r'<[^>]+>|\{T\d+\}', '', str(caption or head[0]))[:16], '按「｜」拆成 %d 格' % len(parts) if len(parts) > 1 else '整段放入第一格'))
            r = parts
        _rows.append(r)
    rows = _rows
    longest = max([plain_len(c) for r in rows for c in r] + [0])
    wide = ncol >= 5
    stack = force_stack if force_stack is not None else (longest > 28 or ncol >= 6)
    cls = ' '.join([c for c in ['wide' if wide else '', 'stack' if stack else '', cls_extra] if c])
    out = ['<div class="tbwrap"><table%s>' % (' class="%s"' % cls if cls else '')]
    if caption:
        out.append('<caption>%s</caption>' % ctx.rich(caption))
    out.append('<thead><tr>%s</tr></thead><tbody>' % ''.join(
        '<th%s>%s</th>' % (' class="num"' if i in num else '', esc(h)) for i, h in enumerate(head)))
    for r in rows:
        r = list(r) + [''] * (ncol - len(r))
        cells = []
        for i, c in enumerate(r[:ncol]):
            classes = []
            if i in num: classes.append('num')
            if tid_col is not None and i == tid_col: classes.append('tid')
            if stack and i != ncol - 1 and plain_len(c) <= 12 and i not in (tid_col,):
                classes.append('meta')
            attrs = (' class="%s"' % ' '.join(classes)) if classes else ''
            if stack:
                attrs += ' data-label="%s"' % esc(head[i])
            cells.append('<td%s>%s</td>' % (attrs, ctx.rich(c) if str(c).strip() else '—'))
        out.append('<tr>%s</tr>' % ''.join(cells))
    out.append('</tbody></table></div>')
    return ''.join(out)


def _as_list(ctx, x, where):
    """v4.15：应为数组的字段若被写成字符串，按「；」/换行拆成条目并给 warning（防止逐字拆成一字一行）"""
    if x is None:
        return []
    if isinstance(x, str):
        parts = [p.strip() for p in re.split(r'[；;\n]+', x) if p.strip()]
        ctx.warnings.append('%s 应为数组，收到的是一段文字，已按「；」拆成 %d 条' % (where, len(parts)))
        return parts
    out = []
    for it in list(x):
        if isinstance(it, dict):   # v4.17：taboo 等被写成 {"x": "..."} 时转成一句话
            it = '；'.join(str(v) for v in it.values() if v not in (None, ''))
            ctx.warnings.append('%s 的条目应为文字，收到对象，已转为文字' % where)
        out.append(it)
    return out


def blocks(ctx, items):
    """通用内容块：table / p / list / note"""
    out = []
    for b in items or []:
        t = b.get('type', 'p')
        if t == 'table':
            tb = table(ctx, b['head'], b.get('rows', []), b.get('title'), b.get('num'))
            if b.get('collapsed'):
                out.append('<details><summary>%s<span>%s</span></summary><div class="det-body">%s</div></details>'
                           % (esc(b.get('title', '展开')), esc(b.get('hint', '')), tb.replace('<caption>%s</caption>' % ctx.rich(b.get('title')), '', 1)))
            else:
                out.append(tb)
        elif t == 'list':
            cls = {'warn': 'card warn', 'bad': 'card bad', 'ok': 'card ok'}.get(b.get('style'), 'card')
            title = '<b style="font-size:13.5px">%s</b>' % ctx.rich(b['title']) if b.get('title') else ''
            out.append('<div class="%s">%s<ul>%s</ul></div>' % (cls, title, ''.join('<li>%s</li>' % ctx.rich(i) for i in _as_list(ctx, b.get('items'), '列表块「%s」的 items' % (b.get('title') or '')))))
        elif t == 'note':
            out.append('<p class="src">%s</p>' % ctx.rich(b.get('text')))
        else:
            out.append('<p>%s</p>' % ctx.rich(b.get('text')))
    return ''.join(out)


def section(sid, title, sub, body):
    return '<section id="%s"><h2>%s<small>%s</small></h2>%s</section>' % (sid, title, sub, body)


# ───────────────────────── v4.19：机械判定（与 daily_ctx v1.1 同口径）─────────────────────────
def _num4(v):
    m_ = re.search(r'(\d{3,5}(?:\.\d+)?)', str(v or ''))
    return float(m_.group(1)) if m_ else None


def mech_fired(prev_th, close, mk):
    """按收盘计算昨日 T1~T8 今天是否触发；返回 (应列入 fired 的集合, 说明列表)"""
    out, why = set(), []
    lu, seal = mk.get('limitup'), mk.get('sealrate')
    if lu is not None:
        if lu < 50: out.add('T1'); why.append('T1 涨停 %s<50' % lu)
        if lu < 45: out.add('T2'); why.append('T2 涨停 %s<45' % lu)
    if seal is not None and seal < 0.55: out.add('T3'); why.append('T3 封板率 %.2f%%<55%%' % (seal * 100))
    if lu is not None and seal is not None and lu >= 60 and seal >= 0.70: out.add('T4'); why.append('T4 涨停 %s≥60 且封板率≥70%%' % lu)
    if close is not None:
        for k, op in (('T5', '<'), ('T6', '<'), ('T7', '>'), ('T8', '>')):
            v = _num4((prev_th.get(k) or {}).get('val'))
            if v is None:
                continue
            hit = close < v if op == '<' else close > v
            if k == 'T8' and hit and (mk.get('amount_chg_yi') is not None) and float(mk['amount_chg_yi']) <= 0:
                hit = False          # 放量站上：缩量不算
            if hit:
                out.add(k); why.append('%s 收盘 %s %s %g' % (k, close, op, v))
    return out, why


def parse_core(text):
    """forecast.core 第一句 → (方向 涨/跌/平, 家数比较符, 家数阈值)；押了两个方向返回 'multi'"""
    first = re.split(r'[；;。\n]', str(text or ''))[0] + '；' + (re.split(r'[；;。\n]', str(text or '')) + ['', ''])[1]
    dirs = set(re.findall(r'收(涨|跌|平)', first))
    d = 'multi' if len(dirs) > 1 else (dirs.pop() if dirs else None)
    mm = re.search(r'上涨家数\s*(≥|>=|＞=|<|＜)\s*(\d{3,4})', first)
    return d, (mm.group(1).replace('>=', '≥').replace('＞=', '≥').replace('＜', '<') if mm else None), (int(mm.group(2)) if mm else None)


def core_grade(prev_core, chg, up):
    d, op, n = parse_core(prev_core)
    if d in (None, 'multi') or op is None or chg is None or up is None:
        return None
    act = '涨' if chg > 0.003 else ('跌' if chg < -0.003 else '平')
    ok1 = (d == act)
    ok2 = (up >= n) if op == '≥' else (up < n)
    g = 'B' if ok1 and ok2 else ('C' if ok1 or ok2 else 'D')
    return {'pred': '收%s；上涨家数%s%d' % (d, op, n), 'actual': '收%s（%+.2f%%）；上涨家数 %d' % (act, chg * 100, up), 'grade': g}


def _tok2num(cond, th):
    """v4.25：cond 里写成 {T5}/{T7} 的点位，换成该日阈值表里的数字"""
    def _f(m):
        t = (th or {}).get(m.group(1))
        v = re.search(r'(\d{3,5}(?:\.\d+)?)', str((t or {}).get('val', '') if isinstance(t, dict) else t or ''))
        return v.group(1) if v else m.group(0)
    return re.sub(r'\{(T\d+)\}', _f, str(cond or ''))


def eval_cond(cond, close, up, th=None):
    """解析 index_scenarios.cond（「>A 且 上涨家数≥N」「A~B，或 >B 但上涨家数<N」「<A」）；不能解析返回 None"""
    if close is None:
        return None
    if th is not None:
        cond = _tok2num(cond, th)
    any_ok, parsed = False, False
    # v4.32：中文写法先转符号（3624至3697、高于/站上/低于/跌破、3790 上方/下方），「或上涨家数」按「或」拆
    cond = str(cond or '')
    cond = re.sub(r'(\d{4}(?:\.\d+)?)\s*(?:至|到)\s*(\d{4}(?:\.\d+)?)', r'\1~\2', cond)
    cond = re.sub(r'(?:高于|站上|突破)\s*(\d{4}(?:\.\d+)?)', r'>\1', cond)
    cond = re.sub(r'(?:低于|跌破|失守)\s*(\d{4}(?:\.\d+)?)', r'<\1', cond)
    cond = re.sub(r'(\d{4}(?:\.\d+)?)\s*(?:上方|之上)', r'>\1', cond)
    cond = re.sub(r'(\d{4}(?:\.\d+)?)\s*(?:下方|之下)', r'<\1', cond)
    for cl in re.split(r'，\s*或|,\s*或|；|\s或\s|或(?=\s*上涨家数)', cond):
        cl = cl.strip()
        if not cl:
            continue
        ok = None
        cp = re.sub(r'上涨家数\s*(?:≥|>=|<|＜|>|＞)\s*\d{3,4}', '', cl)   # v4.32：家数阈值不当点位
        m_ = re.search(r'(\d{4}(?:\.\d+)?)\s*[~～\-—]\s*(\d{4}(?:\.\d+)?)', cp)
        if m_:
            ok = float(m_.group(1)) <= close <= float(m_.group(2))
        else:
            m_ = re.search(r'([<>＜＞])\s*(\d{4}(?:\.\d+)?)', cp)
            if m_:
                ok = (close > float(m_.group(2))) if m_.group(1) in '>＞' else (close < float(m_.group(2)))
        b = re.search(r'上涨家数\s*(≥|>=|<|＜)\s*(\d{3,4})', cl)
        if ok is None and (not b or re.search(r'上证|收', cp)):   # v4.32：写了上证/收盘却没解析出点位 → 不猜
            return None
        if b and up is None:
            return None
        if b:
            ok = (True if ok is None else ok) and ((up >= int(b.group(2))) if b.group(1) in ('≥', '>=') else (up < int(b.group(2))))
        parsed = True
        any_ok = any_ok or ok
    return any_ok if parsed else None


_TOKNUM = re.compile(r'(\{T\d+\})(\s*[（(]\s*(?:now|现|今|=|＝)?\s*\d{3,5}(?:\.\d+)?\s*[)）]|\s*\d{3,5}(?:\.\d+)?(?![\d.]*%))')   # v4.21：也去掉「{T5}(now3563)」


def _strip_toknum(o, path=''):
    """v4.19：正文里「{T5}3653」这类 token 后跟点位的写法自动去掉数字（推演列是昨日原文，不动）"""
    if isinstance(o, str):
        if re.match(r'^/(scores/\d+|scenario_review)/forecast$', path):
            return o, 0
        n_ = len(_TOKNUM.findall(o))
        return (_TOKNUM.sub(r'\1', o), n_) if n_ else (o, 0)
    if isinstance(o, dict):
        tot = 0
        for k in list(o):
            o[k], c_ = _strip_toknum(o[k], path + '/' + str(k)); tot += c_
        return o, tot
    if isinstance(o, list):
        tot = 0
        for i in range(len(o)):
            o[i], c_ = _strip_toknum(o[i], path + '/' + str(i)); tot += c_
        return o, tot
    return o, 0


# ───────────────────────── 计算 ─────────────────────────
def reanchor_index(ctx, idx, ov):
    """T5~T8 按口径计算；ov: {'T5': {'override': 3930, 'reason': '...'}}"""
    close, high, low = float(idx['close']), float(idx['high']), float(idx['low'])
    notes = []
    # v4.17（10/22 复评）：一律以当日收盘为基准、按收盘判定——T5 −1.0% / T6 −1.5% / T7 +1.0% / T8 +1.5%
    t5 = round(close * 0.990)
    t6 = round(close * 0.985)
    t7 = round(close * 1.010)
    t8 = round(close * 1.015)
    std = {'T5': t5, 'T6': t6, 'T7': t7, 'T8': t8}
    final = dict(std)
    for k in ('T5', 'T6', 'T7', 'T8'):
        o = (ov or {}).get(k) or {}
        if o.get('override') is not None:
            v = round(float(o['override']))
            if not o.get('reason'):
                ctx.problems.append('%s 覆盖了标准口径却没写理由（reason）' % k)
            if v == std[k]:
                ctx.problems.append('%s 声称覆盖，但数值 %s 等于标准口径——等于没应用' % (k, v))
            final[k] = v
            notes.append('%s 取 %s 而非标准口径 %s（%s）' % (k, v, std[k], o.get('reason', '未写理由')))
    if final['T5'] <= final['T6']:
        ctx.problems.append('T5(%s) 必须高于 T6(%s)' % (final['T5'], final['T6']))
    elif (final['T5'] - final['T6']) / close < 0.002:
        ctx.problems.append('T5/T6 间距 %.2f%% < 0.2%%' % ((final['T5'] - final['T6']) / close * 100))
    if final['T8'] <= final['T7']:
        ctx.problems.append('T8(%s) 必须高于 T7(%s)' % (final['T8'], final['T7']))
    elif (final['T8'] - final['T7']) / close < 0.003:
        ctx.problems.append('T7/T8 间距 %.2f%% < 0.3%%' % ((final['T8'] - final['T7']) / close * 100))
    return final, std, notes


def matrix_cell(lu, seal):
    r = 0 if lu >= 60 else 1 if lu >= 50 else 2 if lu >= 45 else 3
    c = 0 if seal < 0.60 else 1 if seal < 0.65 else 2
    M = [['虚火·不上修', '发酵', '发酵/高潮'],
         ['虚火·不上修', '持平', '修复'],
         ['退潮1', '退潮1', '持平'],
         ['退潮2/3', '退潮2', '退潮1']]
    R = ['≥60', '50~59', '45~49', '<45']
    C = ['<60%', '60~65%', '≥65%']
    return '%s × %s' % (R[r], C[c]), M[r][c]


DOWN1 = {'发酵': '修复', '高潮': '退潮1', '修复': '退潮2', '退潮1': '退潮2', '退潮2': '退潮3', '退潮3': '冰点', '冰点': '冰点'}


def mech_gear(prev_idx, lu, seal, down_cnt=None):
    """v4.21：纯规则档位（矩阵落格 + R18 跌家>3000 下修正好一档），供第 3 条判分用，不受报告自选档影响"""
    cell, verdict = matrix_cell(lu, seal)
    flat = verdict in ('持平',) or verdict.startswith('虚火')
    if flat:
        return (GEARS[prev_idx] if prev_idx is not None else None), cell, verdict
    base = {'发酵/高潮': '发酵', '退潮2/3': '退潮2'}.get(verdict, verdict)
    if base not in GEARS:
        return None, cell, verdict
    if down_cnt is not None and down_cnt > 3000:
        base = DOWN1[base]
    return base, cell, verdict


def decide_gear(ctx, chosen, prev_idx, lu, seal, down_cnt=None, basis_txt=''):
    if chosen not in GEARS:
        ctx.problems.append('gear.chosen「%s」不在七档之内' % chosen)
        chosen = GEARS[prev_idx] if prev_idx is not None else '退潮2'
    gi = GEARS.index(chosen)
    cell, verdict = matrix_cell(lu, seal)
    # 矩阵一致性（提示，不强制——R18/R22/R27 可合理偏离）
    allowed = set()
    for v in re.split(r'[/·]', verdict):
        if v in GEARS: allowed.add(v)
        if v == '退潮2': allowed.add('退潮2')
        if v == '3': allowed.add('退潮3')
    def is_up(a, b):   # 从 a 到 b 是否上修
        return POSMAP[b] > POSMAP[a] or (GEARS[a].startswith('退潮') and GEARS[b] in ('修复', '发酵', '高潮')) \
               or (GEARS[a] == '冰点' and GEARS[b] in ('修复', '发酵', '高潮'))
    # R18：跌家 > 3000 须在矩阵结果上下修一档（矩阵为「持平/虚火」时不适用）
    main_v = next((g for g in GEARS if g in verdict), None)
    if down_cnt is not None and down_cnt > 3000 and main_v:
        if POSMAP[gi] >= POSMAP[GEARS.index(main_v)]:
            ctx.problems.append('跌家 %d > 3000，R18 要求在矩阵结果「%s」上下修一档，实际判「%s」未下修' % (
                down_cnt, main_v, chosen))
    r18_ok = bool(down_cnt is not None and down_cnt > 3000 and main_v
                  and POSMAP[gi] < POSMAP[GEARS.index(main_v)])   # R18 合规下修
    # v4.21：R18 只下修「正好一档」；再往下须在 basis 引用其他规则（如 R22/R33），否则报红
    _mg = mech_gear(prev_idx, lu, seal, down_cnt)[0]
    if r18_ok and _mg and POSMAP[gi] < POSMAP[GEARS.index(_mg)] - 1e-9:
        _oth = sorted(set(re.findall(r'R(\d{2})', basis_txt or '')) - {'18', '21', '24'})
        if _oth:
            ctx.warnings.append('跌家 >3000 按 R18 应下修一档到「%s」，实际判「%s」多下修了；basis 引用了 %s，请确认依据成立' % (
                _mg, chosen, '、'.join('R' + x for x in _oth)))
        else:
            ctx.problems.append('跌家 %d > 3000，R18 只下修一档：矩阵「%s」→「%s」(仓位 %d%%)，实际判「%s」多下修了且 basis 没引用其他规则（R22/R33 等）——改判「%s」或写明依据' % (
                down_cnt, main_v, _mg, POSMAP[GEARS.index(_mg)] * 100, chosen, _mg))
    if r18_ok:
        pass
    elif verdict.startswith('虚火') and prev_idx is not None and is_up(prev_idx, gi):
        ctx.problems.append('矩阵落「%s」＝虚火·不上修，但档位从 %s 上修到 %s' % (cell, GEARS[prev_idx], chosen))
    elif verdict == '持平' and prev_idx is not None and gi != prev_idx:
        ctx.warnings.append('矩阵落「%s」＝持平，实际从 %s 改为 %s，须在判定依据里说明理由' % (cell, GEARS[prev_idx], chosen))
    elif allowed and chosen not in allowed:
        ctx.warnings.append('矩阵落「%s」指向「%s」，实际判「%s」，须在判定依据里说明偏离理由' % (cell, verdict, chosen))
    # R24 封顶（强制）
    capped = None
    if prev_idx is not None and POSMAP[gi] - POSMAP[prev_idx] > 0.2001:
        capped = chosen
        gi = GEARS.index('修复')
        ctx.warnings.append('R24 已自动封顶：%s(%d%%) → %s(%d%%) 跃升超 20pp，当日改为修复(30%%)' % (
            GEARS[prev_idx], POSMAP[prev_idx] * 100, capped, POSMAP[GEARS.index(capped)] * 100))
    return gi, cell, verdict, capped


# ───────────────────────── 主流程 ─────────────────────────
def _iter_strings(o, p=''):
    """遍历 report 里的全部字符串叶子，返回 (路径, 文本)"""
    if isinstance(o, str):
        yield p, o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from _iter_strings(v, p + '/' + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _iter_strings(v, p + '/' + str(i))


def _seq_shape(vals):
    """v4.12：五日序列形态（按正负号判断；资金比例与净流入同号）"""
    v = [x for x in vals if isinstance(x, (int, float))]
    if len(v) < 3:
        return '样本不足（%d 期）' % len(v)
    sg = [1 if x > 0 else (-1 if x < 0 else 0) for x in v]
    neg = sum(1 for x in sg if x < 0)
    flips = sum(1 for a, b in zip(sg, sg[1:]) if a * b < 0)
    if sg[-1] < 0 and sg[-2] < 0:
        return '衰退（近两期为负）'
    if neg == 0:
        return '持续（全为正）'
    if neg == 1 and sg[-1] > 0 and flips <= 2 and sg[0] > 0:
        return '持续（只有一期为负）'
    if flips >= 3:
        return '脉冲（正负交替）'
    if sg[-1] > 0 and sg[-2] > 0 and any(x < 0 for x in sg[:-2]):
        return '跳跃反转（由负转正）'
    if sg[-1] < 0 and any(x > 0 for x in sg[:-1]):
        return '转弱（最新一期转负）'
    return '其他'


def _flow_series(names, date, n=5):
    """v4.12：从目录里的 market_YYYYMMDD.json 拼出各候选线近 n 个交易日的资金比例（无成交额时退回净流入）"""
    files = sorted(f for f in glob.glob('market_*.json') if re.fullmatch(r'market_\d{8}\.json', os.path.basename(f))
                   and os.path.basename(f)[7:15] <= date.replace('-', ''))[-n:]
    days, maps = [], []
    for f in files:
        try:
            with open(f, encoding='utf-8') as fh:
                j = json.load(fh)
        except Exception:
            continue
        mp = {}
        for key in ('sector_flow_industry',):
            blk = j.get(key)
            if isinstance(blk, dict):
                for r in (blk.get('top') or []) + (blk.get('bottom') or []):
                    mp[str(r.get('sector'))] = r
        sw = j.get('sector_flow_sw1')
        if isinstance(sw, dict):
            for r in sw.get('rows') or []:
                mp[str(r.get('sector'))] = r
        days.append(os.path.basename(f)[11:13] + '-' + os.path.basename(f)[13:15])
        maps.append(mp)
    out = []
    for nm in names:
        key = re.split(r'[（(·/]', str(nm))[0].strip()
        ratio, inflow, amt = [], [], None
        for mp in maps:
            r = mp.get(key)
            ratio.append(r.get('flow_ratio_pct') if r else None)
            inflow.append(r.get('inflow_yi') if r else None)
            if r and r.get('amount_yi'):
                amt = r.get('amount_yi')
        seq = [(a, '%') if isinstance(a, (int, float)) else (b, '亿') for a, b in zip(ratio, inflow)]   # 有资金比例用 %，没有退回净流入 亿
        d2 = (ratio[-1] - ratio[-2]) if len(ratio) >= 2 and all(isinstance(x, (int, float)) for x in ratio[-2:]) else None   # v4.13
        out.append({'name': key, 'seq': seq, 'shape': _seq_shape(inflow),
                    'ratio_today': ratio[-1] if ratio else None, 'amount': amt, 'd2': d2})
    return days, out


SECTOR_TECH = re.compile(r'电子|半导体|PCB|覆铜板|通信|光模块|光通信')


def _sw1_rank(mkobj, name):
    """v4.24：按申万一级资金净流入降序给出某方向的名次（1 起）；匹配不到返回 None"""
    try:
        rows = sorted((mkobj.get('sector_flow_sw1') or {}).get('rows') or [], key=lambda r: -(r.get('inflow_yi') or -1e9))
    except Exception:
        return None, None
    nm = str(name or '')
    keys = [re.split(r'[（(·]', nm)[0].strip()] + [k.strip() for k in re.split(r'[/／、（(）)·]', nm) if k.strip()]
    for k in keys:
        for i, r in enumerate(rows):
            if r.get('sector') and (k == r['sector'] or (len(r['sector']) >= 2 and k.startswith(r['sector']))):
                return i + 1, r.get('inflow_yi')
    return None, None


def quiet_accum(mkobj):
    """v4.26：「钱进价没涨」——申万一级里 当日净流入>0、5 日净流入>0、5 日涨幅<0 的方向，按 5 日净流入降序"""
    rows = (mkobj or {}).get('sector_flow_sw1', {}).get('rows') if isinstance((mkobj or {}).get('sector_flow_sw1'), dict) else None
    out = []
    for r in rows or []:
        a, b, c = r.get('inflow_yi'), r.get('inflow_5d_yi'), r.get('pct_5d')
        if all(isinstance(x, (int, float)) for x in (a, b, c)) and a > 0 and b > 0 and c < 0:
            out.append(r)
    return sorted(out, key=lambda r: -r['inflow_5d_yi'])


_LOCK_DIR = {'T1', 'T2', 'T3', 'T4', 'T11', 'T12', 'T13', 'T14', 'T15', 'T16', 'T17'}   # v4.27：方向锁定的体系常量
_WE_GENERIC = re.compile(r'^(买入日|卖出日|盘中|次日|尾盘|开盘|竞价|集合竞价)')


def overnight_expect(tid, val, meaning):
    """v4.29：从说明里读出隔夜读数，按触发值推算应否触发；读不出返回 None"""
    if tid not in ('T11', 'T12', 'T14', 'T15', 'T17'): return None
    mn = str(meaning or '')
    if re.search(r'未核|未取到|待核|缺失', mn): return None
    v = re.sub(r'10\s*[Yy年]', ' ', str(val or ''))
    mt = re.search(r'(\d+(?:\.\d+)?)', v)
    if not mt: return None
    thr = float(mt.group(1))
    s = re.sub(r'\d{1,2}[-/月]\d{1,2}日?', ' ', mn)       # 去掉日期
    s = re.sub(r'10\s*[Yy年]', ' ', s)                   # 去掉 10Y
    _lc = [m.end() for m in re.finditer(r'收盘|结算', s)]
    if _lc: s = s[_lc[-1]:]                              # 有「收盘/结算」时只看最后一处之后的读数
    if tid in ('T11', 'T17'):
        m = re.search(r'([+\-−]?\d+(?:\.\d+)?)\s*%', s)
        if not m: return None
        x = float(m.group(1).replace('−', '-'))
        if not re.match(r'[+\-−]', m.group(1)):   # v4.34：「收跌 2.49%」「跌幅2.49%」无负号时按前面的涨/跌字定符号
            _pre = s[max(0, m.start() - 4):m.start()]
            if '跌' in _pre: x = -abs(x)
        return (x < -thr), x
    if tid == 'T12':
        for m in re.finditer(r'(\d+(?:\.\d+)?)\s*%', s):
            x = float(m.group(1))
            if 2 <= x <= 8: return (x > thr), x
        return None
    for m in re.finditer(r'(?<![\d.])(\d{2,3}(?:\.\d+)?)(?!\s*%)(?![\d.])', s):   # T14/T15 布油价格
        x = float(m.group(1))
        if 30 <= x <= 300 and abs(x - thr) > 1e-9:
            return ((x < thr) if tid == 'T14' else (x > thr)), x
    return None


def _wrong_if_html(ctx, w):
    """v4.26：wrong_if 可写成两组（上修/下修），每组带撤回条件；旧的一句话写法照常显示"""
    if isinstance(w, list):
        li = []
        for x in w:
            if not isinstance(x, dict):
                li.append('<li>%s</li>' % ctx.rich(x)); continue
            li.append('<li><b>%s</b>：%s → %s%s</li>' % (esc(x.get('dir', '')), ctx.rich(x.get('cond')), ctx.rich(x.get('act')),
                      ('；<b>撤回</b>：%s' % ctx.rich(x.get('revert'))) if x.get('revert') else ''))
        return '<ul style="margin:4px 0 0">%s</ul>' % ''.join(li)
    return ctx.rich(w)


def render(report, prev):
    ctx = Ctx()
    R = report
    prev = prev or {}
    prev_th = {t['id']: t for t in prev.get('thresholds', [])}
    R, _ntok = _strip_toknum(R)
    if _ntok:
        ctx.warnings.append('正文有 %d 处「{Tn}」后跟了点位数字，已自动删去（点位由阈值表唯一给出）' % _ntok)

    # —— 阈值 ——
    th_in = R.get('thresholds', {})
    final_idx, std_idx, notes = reanchor_index(ctx, R['index'], th_in)
    th_list = []
    ids = sorted(set(list(prev_th.keys()) + [k for k in th_in if re.match(r'^T\d+$', k)]),
                 key=lambda x: int(x[1:]))
    for tid in ids:
        base = dict(prev_th.get(tid, {}))
        upd = th_in.get(tid) or {}
        t = {
            'id': tid,
            'dir': upd.get('dir', base.get('dir', 'down')),
            'item': upd.get('item', base.get('item', '')),
            'val': upd.get('val', base.get('val', '')),
            'meaning': upd.get('meaning', base.get('meaning', '')),
            'daily': tid in DAILY,
        }
        _sc = upd.get('scope') if upd.get('scope') in ('overnight', 'intraday') else base.get('scope')   # v4.28：带上写明的作用域
        if _sc in ('overnight', 'intraday'): t['scope'] = _sc
        if tid in _LOCK_DIR and base.get('dir') and upd.get('dir') and upd.get('dir') != base.get('dir'):   # v4.27：体系常量方向锁定
            ctx.problems.append('%s（%s）是体系常量，方向须沿用昨日 dir=%s，报告写成了 %s——已按昨日方向渲染，请把 thresholds.%s.dir 改回 %s' % (
                tid, base.get('item', ''), base['dir'], upd['dir'], tid, base['dir']))
            t['dir'] = base['dir']
        if tid in final_idx:
            n = final_idx[tid]
            t['val'] = ('收盘跌破 %d' % n) if tid in ('T5', 'T6') else ('收盘站上 %d' % n if tid == 'T7' else '收盘放量站上 %d' % n)
        if t['daily'] and tid not in final_idx and tid in prev_th and 'val' not in upd:
            ctx.problems.append('%s 属每日重锚类，今日未重写（沿用了昨日「%s」）' % (tid, base.get('val')))
        if not t['item'] or not t['val']:
            ctx.problems.append('%s 缺观测项或触发值' % tid)
        th_list.append(t)
    ctx.TH = {t['id']: t for t in th_list}
    fired = set(th_in.get('fired', []))
    for t in th_list:   # v4.29：隔夜阈值说明里的读数须与触发值、fired 一致
        _oe = overnight_expect(t['id'], t.get('val'), t.get('meaning'))
        if _oe is None: continue
        _exp, _x = _oe
        if _exp and t['id'] not in fired and '不复用' not in str(t.get('meaning', '')):
            ctx.problems.append('%s 说明里的读数 %g 按触发值「%s」已触发，fired 里却没有 %s——读数对就列进 fired、说明改「已触发」；读数不对就改正读数' % (t['id'], _x, t.get('val'), t['id']))
        elif not _exp and t['id'] in fired:
            ctx.problems.append('%s 在 fired 里，但说明里的读数 %g 按触发值「%s」未触发——二者须一致' % (t['id'], _x, t.get('val')))
    for t in th_list:   # v4.35：隔夜读数与昨日 state 同一阈值的读数完全相同 → 疑似沿用旧读数
        if t['id'] not in ('T11', 'T12', 'T14', 'T15', 'T17'): continue
        _pm = str((prev_th.get(t['id']) or {}).get('meaning', ''))
        _nm = str(t.get('meaning', ''))
        if not _pm or re.search(r'休市|无新收盘|停更|未更新|沿用最近有效', _nm): continue
        def _nums(s):
            s = re.sub(r'\d{1,2}[-/月]\d{1,2}日?', ' ', s)
            s = re.sub(r'10\s*[Yy年]', ' ', s)
            return {x for x in re.findall(r'(?<![\d.])(\d+\.\d{2,3})(?![\d])', s)}
        _thr = set(re.findall(r'\d+(?:\.\d+)?', str(t.get('val', ''))))
        _same = sorted((_nums(_nm) & _nums(_pm)) - _thr)
        if _same:
            ctx.problems.append('%s 说明里的读数 %s 与昨日 state 同一阈值的读数完全相同——疑似沿用了前一交易日的收盘，请核对是不是本交易日（美东）的新收盘；确属休市未更新的，说明里写明「休市/无新收盘」' % (t['id'], '、'.join(_same)))
    if 'T11' in fired and 'T13' in fired:   # v4.27：纳指跌超 1% 与「美股企稳」互斥
        ctx.problems.append('fired 同时含 T11（纳指跌 >1%）与 T13（美股企稳且美债站稳 5% 下方）——纳指已跌超 1% 不能算美股企稳，T13 不得达成；请从 fired 去掉 T13（R31 继续生效）')
    pins = th_in.get('pin', [])
    nxt = th_in.get('next')
    if len(pins) != 3: ctx.problems.append('浮条须恰好 3 条，当前 %d 条' % len(pins))
    if sum(1 for p in pins if ctx.TH.get(p, {}).get('dir') == 'down') < 2:
        ctx.problems.append('浮条三条里至少两条须为反向阈值')
    if 'T16' in pins: ctx.problems.append('浮条不要放 T16 追高禁线')
    _IDX = ('T5', 'T6', 'T7', 'T8')   # v4.19：指数关口每日按收盘重算，昨日触发不等于今日新值已触发
    for p in pins:
        if p in fired and p not in _IDX: ctx.problems.append('浮条放了已触发的 %s' % p)
    if not nxt: ctx.problems.append('缺 thresholds.next（明日最先见分晓的反向阈值）')
    elif nxt in fired and nxt not in _IDX: ctx.problems.append('next 指向了已触发的 %s' % nxt)

    # —— v4.19：fired 里 T1~T8 由代码按收盘核对 ——
    _close = float(R['index']['close']) if R.get('index', {}).get('close') is not None else None
    if prev_th:
        _exp, _why = mech_fired(prev_th, _close, R.get('market', {}))
        _got = {f for f in fired if f in ('T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8')}
        if _got != _exp:
            _full = sorted((set(fired) - {'T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8'}) | _exp, key=lambda x: int(x[1:]))
            ctx.problems.append('fired 里 T1~T8 应为 %s（代码按昨日阈值与今日收盘计算：%s），当前为 %s——请把 thresholds.fired 改为 %s，正文同步' % (
                sorted(_exp, key=lambda x: int(x[1:])) or '无', '；'.join(_why) or '均未触发', sorted(_got, key=lambda x: int(x[1:])) or '无',
                json.dumps(_full, ensure_ascii=False)))

    # —— 档位 ——
    m = R['market']
    prev_gi = prev.get('gear_index')
    _btxt = ' '.join(str(x) for x in (R['gear'].get('basis') if isinstance(R['gear'].get('basis'), list) else [R['gear'].get('basis') or '']))
    gi, cell, mverdict, capped = decide_gear(ctx, R['gear']['chosen'], prev_gi, m['limitup'], m['sealrate'], m.get('down_count'), _btxt)
    mgear = mech_gear(prev_gi, m['limitup'], m['sealrate'], m.get('down_count'))[0]   # v4.21：第 3 条判分用
    pos = POSMAP[gi]

    # —— 判分 ——（编号由代码按维度生成；7b 情景回填独立字段，固定插为最后一条）
    NUM = {'核心观点': '1', '主线延续/反转': '2a', '主线细分选择': '2b', '情绪档位': '3', '指数关口': '4', '个股/资金': '5'}
    ORDER = {k: i for i, k in enumerate(DIMS)}
    scores = [dict(s) for s in R.get('scores', []) if not s.get('scenario')]
    scores.sort(key=lambda s: ORDER.get(s.get('topic'), 99))
    seen = {}
    for s in scores:
        base = NUM.get(s.get('topic'), '?')
        seen[base] = seen.get(base, 0) + 1
        s['n'] = base if seen[base] == 1 else '%s-%d' % (base, seen[base])
    sr = R.get('scenario_review') or next((s for s in R.get('scores', []) if s.get('scenario')), None)
    if not sr:
        ctx.problems.append('缺 scenario_review（7b 情景回填）——判分须固定含「昨日两情景哪个成立、操作倾向是否一致」一条')
    else:
        if sr.get('grade') == 'A':   # v4.29：情景类判断上限 B
            ctx.problems.append('scenario_review.grade 不能判 A——A 只用于带具体数值的预测，隔夜情景是方向判断，兑现最高判 B')
        scores.append({'n': '6', 'topic': '隔夜情景', 'grade': sr.get('grade', 'N'),
                       'forecast': sr.get('forecast', ''), 'actual': sr.get('actual', '')})
    for base in ('1', '2a', '2b', '3', '4'):
        if base not in seen:
            ctx.problems.append('判分缺维度 %s（%s）' % (base, [k for k, v in NUM.items() if v == base][0]))
    for s in scores:
        if not str(s.get('forecast') or '').strip():
            ctx.problems.append('判分第 %s 条（%s）的「推演」为空——须逐字照抄昨日 state.forecast 对应字段' % (s.get('n'), s.get('topic')))
        if s['topic'] not in DIMS and s['topic'] != '隔夜情景': ctx.problems.append('判分维度「%s」不在 6 个固定维度内' % s['topic'])
        if s['grade'] not in GRADE_TXT: ctx.problems.append('判分「%s」非法' % s['grade'])
    g2a = [s for s in scores if s['topic'] == '主线延续/反转']
    g2b = [s for s in scores if s['topic'] == '主线细分选择']
    if g2a and g2a[0]['grade'] == 'D' and g2b and g2b[0]['grade'] != 'N':
        ctx.warnings.append('2a 判 D 时 2b 通常应判 N（不连坐）')

    # —— 规则库 ——
    rules = [dict(r) for r in prev.get('rules', [])]
    for r in rules:
        if r.get('status') == '新增': r['status'] = '生效'
    for u in R.get('rule_updates', []):
        for r in rules:
            if r['id'] == u['id']:
                if 'text' in u: r['text'] = u['text']
                if 'source' in u: r['source'] = u['source']
                if u.get('retire'): r['retired'] = True; r['status'] = u.get('status', '退役')
                elif 'status' in u: r['status'] = u['status']
    nid = max([int(r['id'][1:]) for r in rules] + [0])
    for nr in R.get('new_rules', []):
        nid += 1
        kind = nr.get('kind', 'scan')
        src = nr.get('source', '')
        if kind == 'reanchor' and not nr.get('applied'):
            ctx.problems.append('R%02d 是重锚/降档类，须写 applied（本报告如何应用、偏离标准口径多少）' % nid)
        rules.append({'id': 'R%02d' % nid, 'text': nr['text'], 'kind': kind, 'source': src,
                      'applied': nr.get('applied'),
                      'status': '生效' if src.startswith('战绩复盘') else '新增'})

    # —— v4.12：大盘情景概率、持有窗口、看错信号 ——
    isc = R.get('index_scenarios') or []
    if not isc:
        ctx.warnings.append('缺 index_scenarios：大盘 2~4 种情景及概率（合计 100%），供次日回填与聚合台算校准度')
    else:
        _tot = sum(float(x.get('prob') or 0) for x in isc)
        if not 2 <= len(isc) <= 4:
            ctx.problems.append('index_scenarios 须 2~4 种情景，当前 %d 种' % len(isc))
        if abs(_tot - 100) > 5:
            ctx.problems.append('index_scenarios 概率合计 %g%%，须约等于 100%%' % _tot)
        _t5, _t7 = final_idx.get('T5'), final_idx.get('T7')
        _badc = [str(x.get('name')) for x in isc if x.get('cond') and
                 any(abs(float(v) - _t5) > 0.6 and abs(float(v) - _t7) > 0.6 for v in re.findall(r'(?<!\d)(\d{4})(?!\d)', re.sub(r'[上下]涨家数\s*\S?\s*\d+|跌家数?\s*\S?\s*\d+|涨停\s*\S?\s*\d+', '', str(x.get('cond')))))]
        if _badc:
            ctx.problems.append('index_scenarios 的 cond 点位须用今天的 T5 %d / T7 %d：%s。标准写法——上行「上证收盘 >%d 且 上涨家数≥2500」；中间「上证收盘 %d~%d，或 >%d 但上涨家数<2500」；下行「上证收盘 <%d」' % (
                _t5, _t7, '、'.join(_badc), _t7, _t5, _t7, _t7, _t5))
        _nc = [str(x.get('name')) for x in isc if not x.get('cond')]
        if _nc:
            ctx.warnings.append('index_scenarios 缺 cond（收盘可核对的触发条件，如「上证收在 3830 上方且上涨家数 ≥3000」）：%s' % '、'.join(_nc))
    prev_isc = (prev.get('forecast') or {}).get('index_scenarios') or []
    isr = R.get('index_scenario_review') or {}
    ipr = None
    if prev_isc:
        _names = [str(x.get('name')) for x in prev_isc]
        _hit = isr.get('hit')
        _mh = [x.get('name') for x in prev_isc if eval_cond(x.get('cond'), _close, (R.get('market') or {}).get('up_count'), prev_th)]
        if len(_mh) == 1 and _hit and _hit != _mh[0]:   # v4.19：按昨日 cond 机械核对
            ctx.problems.append('index_scenario_review.hit 应为「%s」（代码按昨日 cond 与今日收盘 %s、上涨家数 %s 计算），报告写「%s」' % (
                _mh[0], _close, (R.get('market') or {}).get('up_count'), _hit))
        if not _hit:
            ctx.warnings.append('缺 index_scenario_review.hit：昨日大盘情景（%s）今天实际是哪一种%s' % ('／'.join(_names), ('，代码算得「%s」' % _mh[0]) if len(_mh) == 1 else ''))
        elif _hit not in _names:
            ctx.problems.append('index_scenario_review.hit「%s」不是昨日情景之一（%s）' % (_hit, '／'.join(_names)))
        else:
            _pr = [float(x.get('prob') or 0) / 100 for x in prev_isc]
            _k = _names.index(_hit)
            _pmax = max(_pr) if _pr else 0
            _a4 = 'B' if _pr[_k] >= _pmax - 1e-9 else 'C'   # v4.28：第 4 条＝昨日最高概率情景兑现 B，其他已列 C
            for s_ in scores:
                if s_['topic'] == '指数关口' and s_['grade'] != _a4:
                    ctx.problems.append('指数关口（第 4 条）判分由大盘情景机械得出：昨日最高概率「%s」%d%%，实际「%s」%d%% → 应判 %s，报告判 %s' % (
                        _names[_pr.index(_pmax)], round(_pmax * 100), _hit, round(_pr[_k] * 100), _a4, s_['grade']))
            ipr = {'names': _names, 'probs': [round(x, 4) for x in _pr], 'hit': _k,
                   'brier': round(sum((p_ - (1 if i == _k else 0)) ** 2 for i, p_ in enumerate(_pr)), 4),
                   'note': isr.get('note', ''), 'cond': str(prev_isc[_k].get('cond') or '')}
    # —— v4.19：核心观点格式（今日）与机械判分（昨日）——
    _cd, _cop, _cn = parse_core((R.get('forecast') or {}).get('core'))
    if _cd == 'multi' or _cd is None or _cop is None:
        ctx.problems.append('forecast.core 第一句须只押一个：「明日上证收涨／收跌／收平；上涨家数≥N 或 <N」（如「明日上证收跌；上涨家数<2500」），当前：「%s」' % str((R.get('forecast') or {}).get('core', ''))[:40])
    cpr = None
    if (prev.get('forecast') or {}).get('core'):
        cpr = core_grade(prev['forecast']['core'], R['index'].get('chg'), (R.get('market') or {}).get('up_count'))
        if cpr:
            for s_ in scores:
                if s_['topic'] == '核心观点' and s_['grade'] != cpr['grade']:
                    ctx.problems.append('核心观点判分由代码机械得出：昨日押「%s」，实际「%s」→ 应判 %s，报告判 %s' % (cpr['pred'], cpr['actual'], cpr['grade'], s_['grade']))
    # —— v4.17：次日档位情景（同大盘情景，按概率押注，代码判分）——
    gsc = R.get('gear_scenarios') or []
    if not gsc:
        ctx.problems.append('缺 gear_scenarios：次日情绪档位 2~4 种情景及概率（档位名须是七档之一，合计 100%）')
    else:
        _gt = sum(float(x.get('prob') or 0) for x in gsc)
        _bad = [str(x.get('name')) for x in gsc if x.get('name') not in GEARS]
        if _bad:
            ctx.problems.append('gear_scenarios 的档位名须是七档之一（%s），收到：%s' % ('/'.join(GEARS), '、'.join(_bad)))
        if not 2 <= len(gsc) <= 4:
            ctx.problems.append('gear_scenarios 须 2~4 种，当前 %d 种' % len(gsc))
        if abs(_gt - 100) > 5:
            ctx.problems.append('gear_scenarios 概率合计 %g%%，须约等于 100%%' % _gt)
        _ps = sorted((float(x.get('prob') or 0) for x in gsc), reverse=True)
        if len(_ps) >= 2 and _ps[0] == _ps[1]:
            ctx.problems.append('gear_scenarios 最高概率须唯一（次日按「最高概率档是否兑现」判分），当前并列 %g%%' % _ps[0])
    if gsc:   # v4.24：forecast.gear 文字押的档位须与 gear_scenarios 最高概率一致
        try:
            _top = max(gsc, key=lambda x: float(x.get('prob') or 0)).get('name')
            _ft = str((R.get('forecast') or {}).get('gear', ''))
            _m = re.search(r'押[^冰修发高退]{0,3}(冰点|修复|发酵|高潮|退潮[123])', _ft)
            if _m and _top and _m.group(1) != _top:
                ctx.problems.append('forecast.gear 押「%s」，但 gear_scenarios 最高概率是「%s」——文字与概率表须一致' % (_m.group(1), _top))
        except Exception:
            pass
    prev_gsc = (prev.get('forecast') or {}).get('gear_scenarios') or []
    gpr = None
    if prev_gsc:
        _gn = [str(x.get('name')) for x in prev_gsc]
        _gp = [float(x.get('prob') or 0) / 100 for x in prev_gsc]
        _act = mgear or GEARS[gi]   # v4.21：按规则档位判分（矩阵 + R18 一档），不按报告自选档
        _k = _gn.index(_act) if _act in _gn else -1
        _top = max(range(len(_gp)), key=lambda i: _gp[i])
        _auto = 'B' if _k == _top else ('C' if _k >= 0 else 'D')
        gpr = {'names': _gn, 'probs': [round(x, 4) for x in _gp], 'hit': _k, 'actual': _act, 'grade': _auto, 'chosen': GEARS[gi],
               'brier': round(sum((p_ - (1 if i == _k else 0)) ** 2 for i, p_ in enumerate(_gp)) + (1 if _k < 0 else 0), 4)}
        for s_ in scores:
            if s_['topic'] == '情绪档位' and s_['grade'] != _auto:
                ctx.problems.append('情绪档位判分须由档位情景机械得出：昨日押「%s」%d%%，实际「%s」%s → 应判 %s，报告判 %s' % (
                    _gn[_top], round(_gp[_top] * 100), _act, ('（昨日给 %d%%）' % round(_gp[_k] * 100)) if _k >= 0 else '（昨日未列）', _auto, s_['grade']))
    win = R.get('window') or {}
    if not (win.get('buy') and win.get('sell')):
        ctx.warnings.append('缺 window：写明买入日、卖出日，以及中间是否隔周末/节假日（gap）')
    if not R.get('wrong_if'):
        ctx.warnings.append('缺 wrong_if：「什么情况说明看错了」——须是开盘半小时内可验证的盘面读数')
    elif isinstance(R.get('wrong_if'), list):   # v4.26：两组（上修/下修）各带撤回条件
        _wd = ''.join(str(x.get('dir', '')) for x in R['wrong_if'] if isinstance(x, dict))
        if not ('上' in _wd and '下' in _wd):
            ctx.warnings.append('wrong_if 须写两组：上修（看多错了往进攻调）和下修（往防守调），当前只有「%s」' % _wd)
        _nr = [str(x.get('dir', '?')) for x in R['wrong_if'] if isinstance(x, dict) and not x.get('revert')]
        if _nr:
            ctx.warnings.append('wrong_if 每组须带 revert（撤回条件：调了之后什么情况再撤回）：%s 缺' % '、'.join(_nr))
    else:
        ctx.warnings.append('wrong_if 建议改为两组写法 [{dir:"上修",cond,act,revert},{dir:"下修",cond,act,revert}]（v4.26）')

    # —— meta ——
    meta = {
        'date': R['date'], 'weekday': R.get('weekday', ''), 'next': R['next'],
        'gear': GEARS[gi], 'gear_index': gi, 'prev_gear_index': prev_gi if prev_gi is not None else gi,
        'position_max': pos, 'mainline': R.get('mainline', ''),
        'limitup': m['limitup'], 'limitdown': m.get('limitdown'), 'sealrate': m['sealrate'],
        'promote_rate': m.get('promote_rate'), 'amount_yi': m.get('amount_yi'), 'amount_chg_yi': m.get('amount_chg_yi'),
        'up_count': m.get('up_count'), 'down_count': m.get('down_count'),
        'index_close': R['index']['close'], 'index_chg': R['index'].get('chg'),
        'verdict': R.get('verdict', ''),
        'scores': [{'n': s['n'], 'topic': s['topic'], 'grade': s['grade']} for s in scores],
        'misses': len(R.get('misses', [])), 'new_rules': len(R.get('new_rules', [])),
        'index_scenarios': [{'name': x.get('name'), 'prob': x.get('prob')} for x in isc],
        'index_prob_review': ipr, 'window': win or None, 'wrong_if': R.get('wrong_if', ''),
        'gear_scenarios': [{'name': x.get('name'), 'prob': x.get('prob')} for x in gsc],
        'gear_prob_review': gpr, 'caliber': CALIBER, 'core_review': cpr,
    }

    H = []   # body 片段

    # ═══ 0 · 摘要 ═══
    nt = ctx.TH.get(nxt) if nxt else None
    H.append('''<section id="s0"><div class="hero"><div class="hero-top">
<h1>A股复盘 · %s（%s）→ %s 次日推演</h1><p class="sub">%s</p></div>
<div class="quad">
<div><div class="k">情绪档位</div><div class="v" id="qGear">%s</div><div class="n">%s</div></div>
<div><div class="k">仓位上限</div><div class="v" id="qPos">%d%%</div><div class="n">%s</div></div>
<div><div class="k">明日动作</div><div class="v">%s</div><div class="n">%s</div></div>
<div><div class="k">最近失效线</div><div class="v" id="qNext">%s</div><div class="n" id="qNextDist">%s</div></div>
</div><div class="hero-note">%s</div>%s</div><p class="src">%s</p></section>''' % (
        esc(R['date']), esc(R.get('weekday', '')), esc(R['next'][5:]), ctx.rich(R.get('headline')),
        GEARS[gi], ctx.rich(R['gear'].get('delta', '')),
        int(round(pos * 100)), ('R24 限仓：矩阵判 %s，当日只到修复' % capped) if capped else '档位硬映射',
        ctx.rich(R.get('action', {}).get('main')), ctx.rich(R.get('action', {}).get('note')),
        ctx.tref(nxt) if nt else '—', esc(th_in.get('next_dist', '')),
        ctx.rich(R.get('thesis')),
        (('<div class="hero-note"><b>持有窗口</b>：买入 %s → 卖出 %s%s</div>' % (esc(win.get('buy')), esc(win.get('sell')),
          ('（%s）' % esc(win['gap'])) if win.get('gap') else '')) if win.get('buy') else '')
        + (('<div class="hero-note" style="border-top:1px dashed var(--warn)"><b style="color:var(--warn)">什么情况说明看错了</b>：%s</div>'
            % _wrong_if_html(ctx, R.get('wrong_if'))) if R.get('wrong_if') else ''),
        ctx.rich(R.get('gen_note', '不构成投资建议'))))

    # ═══ 1 · 阈值 ═══
    rows = []
    for t in th_list:
        a = 'data-t="%s" data-dir="%s" data-reanchor="%s"' % (t['id'], t['dir'], 'daily' if t['daily'] else 'carry')
        if t['id'] in pins: a += ' data-pin="1"'
        if t['id'] in fired: a += ' data-fired="1"'
        if t['id'] == nxt: a += ' data-next="1" data-dist="%s"' % esc(th_in.get('next_dist', ''))
        rows.append('<tr %s><td class="tid">%s</td><td class="dir">%s</td><td class="re">%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            a, t['id'], '▲' if t['dir'] == 'up' else '▼', '每日重锚' if t['daily'] else '可沿用',
            esc(t['item']), esc(t['val']), ctx.rich(t['meaning'])))
    note_html = ''
    if notes:
        note_html = '<div class="card ok"><b style="font-size:13.5px">本报告重锚偏离标准口径</b><ul>%s</ul></div>' % ''.join('<li>%s</li>' % esc(n) for n in notes)
    std_line = '<p class="src">标准口径计算值：T5 %d / T6 %d / T7 %d / T8 %d（收盘 %s，高 %s，低 %s）。</p>' % (
        std_idx['T5'], std_idx['T6'], std_idx['T7'], std_idx['T8'], R['index']['close'], R['index']['high'], R['index']['low'])
    H.append(section('s1', '1 · 阈值总表', '全篇唯一真源，正反双向。T5~T8 由代码按口径计算，正文 tref 由代码生成，不会不一致。',
        '<div id="tcheck" class="card bad"></div><div id="tfired" class="card warn"></div>'
        '<div class="tbwrap"><table class="wide" id="tbl-thresholds"><thead><tr><th>编号</th><th>向</th><th>重锚</th><th>观测项</th><th>触发值</th><th>触发后含义</th></tr></thead><tbody>'
        + ''.join(rows) + '</tbody></table></div>' + std_line + note_html + BLK['reanchor']))

    # ═══ 2 · 判分 ═══
    srows = []
    for s in scores:
        srows.append('<tr><td class="meta" data-label="#">%s</td><td class="meta" data-label="维度">%s</td>'
                     '<td data-label="推演 → 实际"><span class="pv">%s</span><span class="pvsep">↓</span><span class="ac">%s</span></td>'
                     '<td class="g" data-g="%s" data-label="判分">%s</td></tr>' % (
                         esc(s['n']), esc(s['topic']), ctx.rich_prev(s.get('forecast'), prev_th), ctx.rich_prev(s.get('actual'), prev_th),   # v4.21：实际栏说的也是昨日关口
                         s['grade'], GRADE_TXT.get(s['grade'], s['grade'])))
    miss_html = ''.join('<li><b>漏判%s</b> %s<br><b>→ 升级为规则</b>：%s</li>' % (
        '①②③④⑤'[i] if i < 5 else i + 1, ctx.rich(x.get('what')), ctx.rich(x.get('rule'))) for i, x in enumerate(R.get('misses', [])))
    if not R.get('misses'): ctx.problems.append('漏判自白至少 1 条')
    H.append(section('s2', '2 · 昨日校验与滚动战绩', '判分：A 精确 / B 命中 / C 半对 / D 判错 / N 未触发。跨日命中率交聚合台。',
        '<div class="tbwrap"><table class="stack" id="tbl-scores"><caption>%s 推演 → %s 实际</caption>'
        '<thead><tr><th>#</th><th>维度</th><th>推演 → 实际</th><th>判分</th></tr></thead><tbody>%s</tbody></table></div>' % (
            esc(prev.get('date', 'T-1')), esc(R['date']), ''.join(srows))
        + ((('<div class="card"><b style="font-size:13.5px">昨日大盘情景概率 → 实际</b><div style="font-size:13px;margin-top:4px">%s</div>'
             '<div class="src" style="margin:4px 0 0">Brier 分 %.3f（0 最好、2 最差；聚合台按日累计看校准度）%s</div></div>') % (
            '／'.join(('<b>%s %d%% ✓</b>' if i == ipr['hit'] else '%s %d%%') % (esc(nm), round(pp * 100)) for i, (nm, pp) in enumerate(zip(ipr['names'], ipr['probs']))),
            ipr['brier'], (('｜触发条件：' + esc(ipr['cond'])) if ipr.get('cond') else '') + (('｜' + ctx.rich(ipr['note'])) if ipr.get('note') else ''))) if ipr else '')
        + ((('<div class="card"><b style="font-size:13.5px">昨日档位情景概率 → 实际（第 3 条判分由此机械得出）</b><div style="font-size:13px;margin-top:4px">%s%s</div>'
             '<div class="src" style="margin:4px 0 0">规则档位「%s」（矩阵 + R18 一档）%s→ 判 %s｜Brier %.3f。规则：昨日最高概率档兑现＝B；兑现的是其他已列档＝C；未列档＝D</div></div>') % (
            '／'.join(('<b>%s %d%% ✓</b>' if i == gpr['hit'] else '%s %d%%') % (esc(nm), round(pp * 100)) for i, (nm, pp) in enumerate(zip(gpr['names'], gpr['probs']))),
            ('' if gpr['hit'] >= 0 else '（实际档位未列）'), esc(gpr['actual']), (('；报告自选档「%s」不参与判分 ' % esc(gpr.get('chosen'))) if gpr.get('chosen') and gpr.get('chosen') != gpr['actual'] else ''), gpr['grade'], gpr['brier'])) if gpr else '')
        + BLK['scoring']
        + '<div class="card"><b style="font-size:13.5px">今日判分分布</b><div class="scorebar" id="scorebar"></div><div class="scorekey" id="scorekey"></div></div>'
        + BLK['rolling']
        + '<div class="card warn"><b style="font-size:13.5px">漏判自白</b><ul>%s</ul></div>' % miss_html
        + blocks(ctx, R.get('s2_extra'))))

    # ═══ 3 · 规则库 ═══
    rrows = []
    for r in rules:
        k = '重锚/降档 · <b>当日生效</b>' if r.get('kind') == 'reanchor' else '扫描 · 次日生效'
        txt = ctx.rich(r['text'])
        if r.get('applied') and r['status'] == '新增':
            txt += '<br><span style="color:var(--accent)">本报告已应用：%s</span>' % ctx.rich(r['applied'])
        rrows.append('<tr%s><td class="tid" data-label="编号">%s</td><td data-label="规则">%s</td>'
                     '<td class="re meta" data-label="类型 / 生效">%s</td><td class="meta" data-label="来源">%s</td>'
                     '<td class="meta" data-label="状态">%s</td></tr>' % (
                         ' data-retired="1"' if r.get('retired') else '', r['id'], txt, k, esc(r.get('source', '')), esc(r['status'])))
    H.append(section('s3', '3 · 规则库', '常驻累积。每日只追加，编号永不复用；昨日「新增」已由代码自动转「生效」。',
        '<div class="tbwrap"><table class="wide stack"><thead><tr><th>编号</th><th>规则</th><th>类型 / 生效</th><th>来源</th><th>状态</th></tr></thead><tbody>%s</tbody></table></div>' % ''.join(rrows)))

    # ═══ 4 · 口径 ═══
    _cal = _as_list(ctx, R.get('caliber'), 'caliber')
    cal = ''.join('<li>%s</li>' % ctx.rich(x) for x in _cal)
    H.append(section('s4', '4 · 口径说明', '多源差异并列，不掩盖。',
        '<details><summary>展开口径与多源差异<span>%d 项</span></summary><div class="det-body"><ul>%s</ul></div></details>' % (len(_cal), cal)))

    # ═══ 5 · 指数资金 ═══
    H.append(section('s5', '5 · 指数资金总览', '证据层，只放可核对的原始读数。',
        '<p class="lead">%s</p>' % ctx.rich(R.get('s5_lead')) + blocks(ctx, R.get('s5'))
        + (table(ctx, ['情景', '概率', '触发条件（收盘可核对）', '依据'], [[x.get('name'), '%g%%' % float(x.get('prob') or 0), x.get('cond', '—'), x.get('basis', '')] for x in isc],
                 caption='次日大盘情景（概率合计 100%，次日回填实际情景）', force_stack=True) if isc else '')))

    # ═══ 6 · 情绪档位 ═══
    basis = ''.join('<li>%s</li>' % ctx.rich(x) for x in _as_list(ctx, R['gear'].get('basis'), 'gear.basis'))
    cap_html = ('<div class="card warn"><b style="font-size:13.5px">R24 已封顶</b>：矩阵/判断指向 %s（%d%%），较前日 %s（%d%%）跃升超 20pp，当日只到修复（30%%），次日读数确认后再补足。</div>' % (
        capped, POSMAP[GEARS.index(capped)] * 100, GEARS[prev_gi], POSMAP[prev_gi] * 100)) if capped else ''
    H.append(section('s6', '6 · 情绪档位', '档位 → 仓位硬映射，由代码执行。',
        '<div class="gears" id="gears" aria-hidden="true"></div><div class="gearlab"><span>冰点</span><span>修复</span><span>发酵</span><span>高潮</span><span>退潮1</span><span>退潮2</span><span>退潮3</span></div>'
        + '<div class="card ok"><b style="font-size:13.5px">矩阵落格（代码计算）</b>：涨停 %s 家 × 封板率 %.2f%% → <b>%s</b> ＝ %s</div>' % (m['limitup'], m['sealrate'] * 100, cell, mverdict)
        + cap_html + BLK['posmap'] + BLK['matrix']
        + '<p><b>判定依据：</b></p><ul>%s</ul><p><b>特殊性：</b>%s</p>' % (basis, ctx.rich(R['gear'].get('special')))
        + (table(ctx, ['次日档位', '概率', '依据'], [[x.get('name'), '%g%%' % float(x.get('prob') or 0), x.get('basis', '')] for x in gsc],
                 caption='次日档位情景（v4.17·概率合计 100%；次日由代码按实际档位判分：最高概率档兑现＝B，其他已列档＝C，未列＝D）', force_stack=True) if gsc else '')
        + blocks(ctx, R.get('s6'))))

    # ═══ 7 · 外盘 ═══
    for _p11, _s11 in _iter_strings(R.get('s11') or []):   # v4.33：已结项事件不再携带
        if re.search(r'结项', _s11) and re.search(r'携带到|持续携带', _s11) and not re.search(r'已结项移除', _s11):
            ctx.warnings.append('s11%s 写了「结项」却仍「携带到」——已结项事件应删掉，只在末尾一句「已结项移除：……」列名：「%s」' % (_p11, _s11[:40]))
    q = R.get('watch', {})
    items = q.get('items', [])
    if len(items) != 5: ctx.problems.append('7b 须恰好 5 项，当前 %d 项' % len(items))
    for it in items:
        refs = re.findall(r'\{(T\d+)\}', it)
        if not refs: ctx.problems.append('7b 有一项没有阈值编号：「%s」' % it[:20])
        for r_ in refs:
            # v4.31：隔夜类阈值每晚是一次新读数，今天触发过也要（也可以）在今晚重新观察；只有盘中/持续态的已触发阈值才移入背景
            if r_ in fired and not (ctx.TH.get(r_) and scope_of(ctx.TH[r_]) == 'overnight'):
                ctx.problems.append('7b 引用了已触发的 %s，应移入既定背景' % r_)
            elif ctx.TH.get(r_) and scope_of(ctx.TH[r_]) == 'intraday':
                ctx.problems.append('7b 引用了 A 股盘中阈值 %s（%s）——隔夜无法验证，次日补丁只能写「未取到」。'
                                    '7b 五项须全部为外盘/商品/中概类' % (r_, ctx.TH[r_]['item']))
    # v4.31：今天触发的隔夜阈值（纳指、费半、10Y、布油、金龙）今晚是新读数，7b 和情景都没再观察它 → 提醒
    _ref7 = set(re.findall(r'\{(T\d+)\}', ' '.join(str(x) for x in items) + ' ' + ' '.join(str((q.get('scenarios', {}).get(k_) or {}).get('cond', '')) for k_ in ('A', 'B'))))
    for r_ in sorted(fired):
        if ctx.TH.get(r_) and scope_of(ctx.TH[r_]) == 'overnight' and r_ not in _ref7 and r_ in ('T11', 'T12', 'T15', 'T17'):
            ctx.warnings.append('今天触发的隔夜阈值 %s（%s）在 7b 和情景条件里都没有再观察——隔夜类每晚是新读数，今晚的 %s 仍是关键变量，建议放回 7b 或用作情景条件' % (r_, ctx.TH[r_]['item'], ctx.TH[r_]['item']))
    scen = q.get('scenarios', {})
    _ta = ' '.join(str(scen.get(k_, {}).get(f_, '')) for k_ in ('A', 'B') for f_ in ('cond', 'act')) + ' ' + ' '.join(str(x) for x in items)
    if re.search(r'(纳指|纳斯达克|费半)[^{}，,；;。]{0,15}\{T17\}', _ta):   # v4.28：T17 是金龙
        ctx.problems.append('情景/7b 把 {T17} 写成了纳指/费半——T17 是「中概(金龙)跌 >1.5%」，纳指用 {T11}')
    _ca = str(scen.get('A', {}).get('cond', '')); _aa = _ca + ' ' + str(scen.get('A', {}).get('act', ''))
    if re.search(r'(?<!未)触发\{T13\}', _ca) and re.search(r'R31\s*(不解除|继续生效|继续|仍生效)', _aa):
        ctx.problems.append('情景 A 条件是「触发{T13}」（R31 解除条件），条件/操作却写「R31 不解除/继续」——A 成立即 R31 解除，请改写')
    for k_ in ('A', 'B'):   # v4.27：判定条件须含阈值编号，次日才能机械判定
        if scen.get(k_) is not None and not re.search(r'\{T\d+\}', scen.get(k_, {}).get('cond', '') or ''):
            ctx.problems.append('情景 %s 判定条件没有阈值编号（须写成「触发{Tn}」/「未触发{Tn}」，A/B 互补）：「%s」' % (k_, str(scen.get(k_, {}).get('cond', ''))[:30]))
    for k_ in ('A', 'B'):
        for r_ in re.findall(r'\{(T\d+)\}', scen.get(k_, {}).get('cond', '')):
            if r_ in fired and not (ctx.TH.get(r_) and scope_of(ctx.TH[r_]) == 'overnight'):   # v4.31：隔夜类不受限
                ctx.problems.append('情景 %s 判定条件引用了已触发的 %s' % (k_, r_))
            elif ctx.TH.get(r_) and scope_of(ctx.TH[r_]) == 'intraday':
                ctx.problems.append('情景 %s 引用了 A 股盘中阈值 %s（%s）——隔夜判不了，判定条件只能用外盘/商品/中概类'
                                    % (k_, r_, ctx.TH[r_]['item']))
    # v4.21：A/B 两情景须覆盖所有触发组合，否则次日两边都不成立只能判 N
    def _lits(c):
        out = []
        for part in re.split(r'或', c or ''):
            L = [(t, not neg) for neg, t in re.findall(r'(未)?(?:触发|达成)\{(T\d+)\}', part)]
            if L: out.append(L)
        return out
    _la, _lb = _lits(scen.get('A', {}).get('cond', '')), _lits(scen.get('B', {}).get('cond', ''))
    _toks = sorted({t for D in (_la + _lb) for t, _ in D})
    if _la and _lb and 0 < len(_toks) <= 5:
        import itertools as _it
        _hole = None
        for _bits in _it.product([False, True], repeat=len(_toks)):
            _asg = dict(zip(_toks, _bits))
            _ok = lambda D: any(all(_asg[t] == v for t, v in conj) for conj in D)
            if not _ok(_la) and not _ok(_lb):
                _hole = _asg; break
        if _hole:
            ctx.warnings.append('隔夜情景 A/B 没有覆盖全部组合：%s 时两边都不成立，次日只能判 N——建议改成互补（A「触发{Tx}」/ B「未触发{Tx}」，其余写进 act）' % (
                ' 且 '.join(('触发' if v else '未触发') + k for k, v in _hole.items())))
    s7 = ('<h3>7a · 已收盘事实</h3>' + blocks(ctx, R.get('s7a'))
          + '<h3>7b · 今晚待观测（21:30 后）</h3><div class="card warn"><p style="margin:0 0 6px"><b>报告生成时美股未开盘</b>，以下为待验证项，次日第 2 节回填。</p><ul>%s</ul></div>' % ''.join('<li>☐ %s</li>' % ctx.rich(i) for i in items)
          + table(ctx, ['情景', '判定条件', '次日操作倾向'],
                  [['A · 转好', scen.get('A', {}).get('cond', ''), scen.get('A', {}).get('act', '')],
                   ['B · 转坏', scen.get('B', {}).get('cond', ''), scen.get('B', {}).get('act', '')]],
                  caption='两情景映射（今晚收盘 → 明日开盘）', force_stack=True)
          + ('<p class="src"><b>既定背景（不参与 A/B 判定）</b>：%s</p>' % ctx.rich(q.get('background')) if q.get('background') else ''))
    H.append(section('s7', '7 · 隔夜外盘', 'a 已收盘事实 / b 今晚待观测。', s7))

    # ═══ 8 · 主线 ═══
    cands = R.get('candidates', [])
    # 主攻线可以不是第 1 名：排序按净流入，但定主攻线看「明天可能性最大」
    ml = str(R.get('mainline', ''))
    if cands and ml not in ('', '无主攻线'):
        mc = next((c for c in cands if re.split(r'[（(·/]', str(c.get('name', '')))[0].strip()
                   and re.split(r'[（(·/]', str(c.get('name', '')))[0].strip() in ml), None)
        if mc is None:
            ctx.problems.append('mainline「%s」不在候选排序表里' % ml)
        else:
            bad = [k for k, v in (('连续性验证', mc.get('continuity', '')),
                                  ('同形态先例', mc.get('precedent', '')),
                                  ('环境匹配', mc.get('env_match', ''))) if str(v).startswith('✗')]
            if bad:
                ctx.problems.append('主攻线「%s」的 %s 为 ✗，不得定为主攻线——'
                                    '可改由其他三项全过的候选担任，或写「无主攻线」' % (ml, '、'.join(bad)))
    if cands and (ml in ('', '无主攻线') or ml.startswith('无主攻')) and '退潮' not in GEARS[gi]:   # v4.29：第 1 名未过时看有没有三项全过的接任者
        _ok3 = lambda c: all(str(c.get(k_, '')).startswith('✓') for k_ in ('continuity', 'precedent', 'env_match'))
        _c0 = cands[0]
        if not _ok3(_c0):
            _succ = [c for c in cands[1:] if _ok3(c) and '回避' not in str(c.get('role', ''))]
            if _succ:
                ctx.warnings.append('第 1 名「%s」未过三项验证，「%s」三项全过——按第 8 节可由它接任主攻线；不接任须在 gear.special 写明理由' % (
                    _c0.get('name', ''), _succ[0].get('name', '')))
    # ── 与 market_YYYYMMDD.json 核对：资金流数字不许自己填 ──
    mk, mk_name, mk_size, mkfoot = None, R.get('market_file') or ('market_%s.json' % R['date'].replace('-', '')), None, ''
    try:
        with open(mk_name, encoding='utf-8') as f:
            mk = json.load(f)
        mk_size = os.path.getsize(mk_name)
    except Exception:
        mk = None        # v4.9：找不到当日文件就是找不到，绝不拿其他日期的文件顶替
    if mk is None:
        _others = sorted(glob.glob('market_*.json'))
        ctx.problems.append('目录里没有可读的 %s（目录内的 market 文件：%s）——不得用其他日期的文件顶替，不得自行运行 daily_pull.py，'
                            '也不得手写/补写/重建 market 文件；请先把取数工具产出的当日 market json 放进目录再生成' % (mk_name, '、'.join(_others) or '无'))
        mkfoot = '<br>⚠ 本报告没有读到当日 market json，资金流数字不可信'
    else:
        if mk.get('_generated_by') not in ('daily_pull.py', 'browser_pull.html'):
            ctx.problems.append('%s 不是 daily_pull.py 或 browser_pull.html 的原始产物（缺 _generated_by 标记）——'
                                '市场数据文件不得手工修改或补写' % mk_name)
        _bd = mk.get('breadth')
        if isinstance(_bd, dict) and ('兜底' in str(_bd.get('_scope', '')) or '不含北交所' in str(_bd.get('_scope', ''))):
            if not any(('北交所' in str(x) or '沪深' in str(x)) for x in (R.get('caliber') or [])):
                ctx.warnings.append('market 里的涨跌家数是沪深口径（不含北交所约 270 只），与东财 APP 的沪深京口径相差约 270 只。'
                                    'caliber 里须注明口径，且不得与 APP 读数直接逐项对比')
        elif mk.get('_generated_by') == 'browser_pull.html':
            ctx.warnings.append('本日 market 数据取自手机浏览器直取工具（browser_pull.html）：口径与东财 APP 一致，'
                                '与 daily_pull.py 的历史数据同源（东财接口），可正常对比')
        if mk.get('_errors'):
            _fix = ('请到浏览器取数工具里等一会儿或换网络后点该项的「重试此项」，再把新的 market json 传入目录（不要在 Coze 里重跑 daily_pull，那边取不到东财 push2）'
                    if mk.get('_generated_by') == 'browser_pull.html' else '建议重新运行 daily_pull.py --only market')
            ctx.problems.append(('%s 采集时有 %d 项失败：%s。这些字段必须在报告中如实写「未取到」，'
                                 '不得估算；' + _fix.replace('%', '%%'))
                                % (mk_name, len(mk['_errors']), '；'.join(str(x)[:40] for x in mk['_errors'][:4])))
        # ═══ v4.9 数据文件自检：每天不再靠人工核对 ═══
        if mk.get('_generated_by') != 'browser_pull.html':
            ctx.problems.append('%s 的 _generated_by=%s，不是手机取数工具（browser_pull.html）的产物——Coze 侧 daily_pull 取不到东财资金流，'
                                '这个文件不能用于复盘；请把你上传的当日 market json 覆盖进目录后再生成，不得自行取数或改写' % (mk_name, mk.get('_generated_by')))
        if str(mk.get('date', '')) != str(R['date']):
            ctx.problems.append('%s 里的 date=%s 与报告日期 %s 不符——这不是当日数据' % (mk_name, mk.get('date'), R['date']))
        if mk.get('_ok') is False:
            ctx.problems.append('%s 的 _ok=false（取数不完整）——请先在取数工具里重试失败项，再生成报告' % mk_name)
        if mk.get('_generated_by') == 'browser_pull.html' and not (mk.get('_tool_version') and mk.get('_generated_at')):
            ctx.problems.append('%s 缺 _tool_version/_generated_at——不是取数工具的原始产物，疑似被改写或重建，不得使用' % mk_name)
        pf = R.get('preflight')
        if not isinstance(pf, dict):
            ctx.problems.append('缺 preflight（开工自检）：须先 ls 目录并读取 %s，把文件名/字节数/_generated_by/_generated_at 如实填入 report.preflight，渲染器会与真实文件比对' % mk_name)
        else:
            _pb = []
            if str(pf.get('market_file', '')) != str(mk_name):
                _pb.append('文件名(填%s)' % pf.get('market_file'))
            try:
                if int(pf.get('bytes')) != int(mk_size):
                    _pb.append('字节数(填%s/实际%s)' % (pf.get('bytes'), mk_size))
            except Exception:
                _pb.append('字节数(填%s/实际%s)' % (pf.get('bytes'), mk_size))
            if str(pf.get('generated_by') or '') != str(mk.get('_generated_by') or ''):
                _pb.append('generated_by(填%s/实际%s)' % (pf.get('generated_by'), mk.get('_generated_by')))
            if str(pf.get('generated_at') or '') != str(mk.get('_generated_at') or ''):
                _pb.append('generated_at(填%s/实际%s)' % (pf.get('generated_at'), mk.get('_generated_at')))
            if _pb:
                ctx.problems.append('preflight 与目录里的真实文件对不上：%s——说明没有真正读取这份文件，或读取之后文件被改动' % '；'.join(_pb))
        _sw = {r['sector']: float(r['inflow_yi']) for r in ((mk.get('sector_flow_sw1') or {}).get('rows') or [])
               if r.get('sector') and r.get('inflow_yi') is not None}
        _mf = (mk.get('market_flow') or {}).get('main_net_yi')
        _sumtxt = ''
        if len(_sw) >= 25 and _mf is not None:
            _sum, _mf = sum(_sw.values()), float(_mf)
            _d = abs(_sum - _mf)
            _sumtxt = ' · 申万一级合计 %+.2f / 全市场主力 %+.2f' % (_sum, _mf)
            if _d > max(100, 0.35 * abs(_mf)):
                ctx.problems.append('market 里 31 个申万一级主力净流入合计 %+.2f 亿，与全市场主力 %+.2f 亿相差 %.1f 亿——两者本应互相印证，说明板块资金或总额被改过/口径混杂'
                                    % (_sum, _mf, _d))
            elif _d > max(30, 0.12 * abs(_mf)):
                ctx.warnings.append('申万一级合计 %+.2f 亿与全市场主力 %+.2f 亿相差 %.1f 亿，偏大，请留意口径' % (_sum, _mf, _d))
        # 报告里的关键标量必须与 json 一致
        _ix = mk.get('indices') if isinstance(mk.get('indices'), dict) else {}
        _sh = _ix.get('上证指数') or mk.get('sh_index') or {}
        _lm = mk.get('limit') if isinstance(mk.get('limit'), dict) else {}
        _bd2 = mk.get('breadth') if isinstance(mk.get('breadth'), dict) else {}
        _rm, _ri, _cmp = R.get('market') or {}, R.get('index') or {}, []

        def _chk(label, got, exp, tol=0.0):
            try:
                if got is not None and exp is not None and abs(float(got) - float(exp)) > tol:
                    _cmp.append('%s 报告=%s / json=%s' % (label, got, exp))
            except Exception:
                pass
        _chk('涨停家数', _rm.get('limitup'), _lm.get('limitup'))
        _chk('跌停家数', _rm.get('limitdown'), _lm.get('limitdown'))
        _chk('封板率', _rm.get('sealrate'), _lm.get('sealrate'), 0.0006)
        _chk('上涨家数', _rm.get('up_count'), _bd2.get('up_count'))
        _chk('下跌家数', _rm.get('down_count'), _bd2.get('down_count'))
        _chk('上证收盘', _ri.get('close'), _sh.get('close'), 0.011)
        _chk('上证最高', _ri.get('high'), _sh.get('high'), 0.011)
        _chk('上证最低', _ri.get('low'), _sh.get('low'), 0.011)
        _chk('上证涨跌幅', _ri.get('chg'), _sh.get('chg'), 0.0002)
        _a1, _a2, _a3 = [(_ix.get(k) or {}).get('amount_yi') for k in ('上证指数', '深证成指', '北证50')]
        if _a1 is not None and _a2 is not None and _rm.get('amount_yi') is not None:
            _opts = [_a1 + _a2] + ([_a1 + _a2 + _a3] if _a3 is not None else [])
            if all(abs(float(_rm['amount_yi']) - o) > 1.5 for o in _opts):
                _cmp.append('成交额(亿) 报告=%s / json 两市=%.2f' % (_rm.get('amount_yi'), _opts[0]))
        if _cmp:
            ctx.problems.append('报告里的关键数字与 %s 不符：%s——这些数字必须照抄文件，不得凭印象或第三方改写' % (mk_name, '；'.join(_cmp)))
        # 叙述里的板块资金数字：逐句与 sector_flow_sw1 比对（摘要、标题、判分、口径栏、操作台…全文都查）
        if len(_sw) >= 25:
            _alias = {'医药': '医药生物', '交运': '交通运输', '非银': '非银金融', '军工': '国防军工', '地产': '房地产', '有色': '有色金属',
                      '家电': '家用电器', '建材': '建筑材料', '建筑': '建筑装饰', '农业': '农林牧渔', '石化': '石油石化', '商贸': '商贸零售',
                      '轻工': '轻工制造', '机械': '机械设备'}
            _nm = sorted(set(list(_sw) + list(_alias)), key=lambda x: -len(x))
            _re = re.compile('(' + '|'.join(re.escape(n) for n in _nm) + r')\s*([+\-−]\d+(?:\.\d+)?)(?![\d%.％])')
            _skip = ('昨日', '昨天', '前日', '前一', '上周', '09-', '9/', '较昨')
            _stale = []
            for _p, _s in _iter_strings(R):
                if re.match(r'^/(scores/\d+|scenario_review)/forecast$', _p):   # v4.17：推演列是昨日原文，不比对今日资金
                    continue
                for _seg in re.split(r'[；;。\n，,、]', _s):
                    if any(k in _seg for k in _skip):
                        continue
                    for _m in _re.finditer(_seg):
                        _full = _alias.get(_m.group(1), _m.group(1))
                        _exp = _sw.get(_full)
                        if _exp is None:
                            continue
                        _txt = _m.group(2).replace('−', '-')
                        _tol = (0.6 + 0.01 * abs(_exp)) if '.' not in _txt else max(0.05, 0.005 * abs(_exp))
                        if abs(float(_txt) - _exp) > _tol:
                            _stale.append('%s「%s%s」应为 %+.2f' % (_p, _m.group(1), _txt, _exp))
            if _stale:
                ctx.problems.append('叙述里的板块主力净流入与 %s（sector_flow_sw1）不符，共 %d 处：%s——资金流数字必须全文照抄同一来源，请逐处改正（摘要、标题、判分、口径栏、操作台等都要查）'
                                    % (mk_name, len(_stale), '；'.join(_stale[:6]) + ('…' if len(_stale) > 6 else '')))
        # 已取到 market json 时，不得再引用第三方来源作为资金流/家数/成交的出处
        _tp = [_p for _p, _s in _iter_strings(R)
               if re.search(r'Wind|数据宝', _s) and not re.search(r'未使用|不使用|不得|禁止|未采用', _s)]
        if _tp:
            ctx.problems.append('已取到 %s，正文却仍引用第三方来源(Wind/数据宝)：%s——资金流、涨跌家数、成交额只许取自 market json (R32)' % (mk_name, '、'.join(_tp[:5])))
        # 「高开/低开」必须与开盘价相符（警告）
        try:
            _o, _c, _g = _sh.get('open'), _sh.get('close'), _sh.get('chg')
            if _o and _c and _g is not None and _g > -0.99:
                _pc = _c / (1 + _g)
                _op = _o / _pc - 1
                if _op < 0.002:
                    _hit = [_p for _p, _s in _iter_strings(R) if re.search(r'高开低走|高开后|高开[(（]\d|[今当]日高开|首日高开', _s)]
                    if _hit:
                        ctx.warnings.append('报告写了「高开」（%s），但 market 里上证开盘 %s 较昨收约 %.2f 为 %+.2f%%（平开/低开）——请按开盘价如实描述'
                                            % ('、'.join(_hit[:3]), _o, _pc, _op * 100))
        except Exception:
            pass
        mkfoot = '<br>数据文件 %s · %s %s · %s · %s 字节%s' % (
            esc(mk_name), esc(mk.get('_generated_by', '')), esc(mk.get('_tool_version', '')), esc(mk.get('_generated_at', '')),
            format(mk_size, ',') if mk_size is not None else '?', _sumtxt)
        pool, null_secs, sector_n, ind_vals = {}, set(), 0, []
        for key in ('sector_flow_concept', 'sector_flow_industry', 'sector_flow_sw1'):
            blk = mk.get(key)
            if isinstance(blk, dict):
                for r in (blk.get('top') or []) + (blk.get('bottom') or []) + (blk.get('rows') or []):
                    if r.get('inflow_yi') is not None:
                        pool.setdefault(round(float(r['inflow_yi']), 2), []).append(r.get('sector'))
                        sector_n += 1
                        if key != 'sector_flow_concept':
                            ind_vals.append(float(r['inflow_yi']))
                    elif r.get('sector'):
                        null_secs.add(r['sector'])
        for r in (mk.get('stock_flow_top') or []):
            if isinstance(r, dict) and r.get('main_inflow_yi') is not None:
                pool.setdefault(round(float(r['main_inflow_yi']), 2), []).append(r.get('name'))
        names = set()
        lim = mk.get('limit') if isinstance(mk.get('limit'), dict) else {}
        for k in ('top_seal', 'top_boards'):
            for r in (lim.get(k) or []):
                if r.get('stock'):
                    names.add(r['stock'])
        for r in (lim.get('ladder') or []):
            names.update(r.get('stocks') or [])
        for r in (mk.get('stock_flow_top') or []):
            if isinstance(r, dict) and r.get('name'):
                names.add(r['name'])

    def _yi(x):
        mm = re.search(r'([+-]?\d+(?:\.\d+)?)', str(x or '').replace(',', ''))
        return float(mm.group(1)) if mm else None
    # R32 资金流口径唯一性：market json 里板块资金流整体未取到时，排序表不得写任何具体净流入数字
    flow_missing = mk is not None and sector_n == 0     # v4.9：只看板块资金榜，个股资金不算
    all_missing = bool(cands) and all('未取到' in str(c.get('inflow', '')) for c in cands)
    if flow_missing:
        wrote = [(c.get('name', ''), c.get('inflow')) for c in cands
                 if _yi(c.get('inflow')) is not None]
        if wrote:
            ctx.problems.append('%s 的板块资金流未取到，排序表却写了具体净流入（%s）——不同来源的「主力净流入」口径各不相同'
                                '（同一板块在不同软件里可差一倍），混用会让排名失去意义。'
                                '请把「主力净流入」列统一写「未取到」，R20 改按 涨停家数 > 连板高度 > 龙头封板强度 排序，'
                                '并在口径栏写明「今日资金流未取到，排序不含资金维度」'
                                % (mk_name, '、'.join('%s %s' % w for w in wrote[:3])))
        if not any('资金流' in str(x) and '未取到' in str(x) for x in (R.get('caliber') or [])):
            ctx.warnings.append('资金流未取到，caliber 里须有一条「今日资金流未取到，排序不含资金维度」')
        # 路径 A 需要「昨日净流入前 3」，口径断裂时不可用
        for c in cands:
            if '连续' in str(c.get('continuity', '')) and '前3' in str(c.get('continuity', '')):
                ctx.problems.append('「%s」连续性验证走了路径 A（昨日净流入前 3），但今日资金流未取到、口径已断裂——'
                                    '路径 A 不可用，只能走路径 B（龙头连板晋级）或判 ✗' % c.get('name', ''))
    vals = [(c.get('name', ''), _yi(c.get('inflow'))) for c in cands]
    ranked = [v for v in vals if v[1] is not None]
    for i in range(1, len(ranked)):
        if ranked[i][1] > ranked[i - 1][1] + 1e-9:
            ctx.problems.append('R20 排序违规：「%s」净流入 %+.2f 亿高于排在它前面的「%s」%+.2f 亿——第一优先级是主力净流入绝对额' % (
                ranked[i][0], ranked[i][1], ranked[i - 1][0], ranked[i - 1][1]))
            break
    if flow_missing and all_missing:
        def _n(x):
            mm = re.search(r'(\d+)', str(x or ''))
            return int(mm.group(1)) if mm else None
        lus = [(c.get('name', ''), _n(c.get('limitups'))) for c in cands if _n(c.get('limitups')) is not None]
        for i in range(1, len(lus)):
            if lus[i][1] > lus[i - 1][1]:
                ctx.problems.append('资金流缺失时 R20 改按涨停家数降序：「%s」涨停 %d 家多于排在前面的「%s」%d 家'
                                    % (lus[i][0], lus[i][1], lus[i - 1][0], lus[i - 1][1]))
                break
    if cands:   # 连续性验证只针对排第 1 的候选
        c = cands[0]
        _cat = str(c.get('catalyst', ''))
        if re.search(r'(?<![不无未没]标)(?<![不无未没])★', _cat) and not re.search(r'(不|无|未|没)\s*标?\s*★', _cat) \
                and str(c.get('continuity', '')).startswith('✗') \
                and '理由' not in str(c.get('continuity', '')) \
                and not str(c.get('precedent', '')).startswith('✗'):   # 有失败先例时路径C不适用
            ctx.problems.append('第 1 名「%s」带 ★ 催化却判连续性 ✗——R27 允许凭 ★ 走路径 C；若确不适用，在该格写明「理由：…」' % c.get('name', ''))
    # 排序：资金流缺失时不再要求按净流入降序
    if mk is not None and pool:
        def _match_sector(nm):
            key = re.split(r'[（(·/]', str(nm or ''))[0].strip()
            hits = []
            for val, secs in pool.items():
                for sec in secs:
                    if not sec:
                        continue
                    if sec in key or key in sec or (len(key) >= 2 and key[:2] in sec):
                        hits.append((sec, val))
            return hits
        for c in cands:
            v = _yi(c.get('inflow'))
            if v is None:
                continue
            hits = _match_sector(c.get('name'))
            if not hits and any(sec in str(c.get('name', '')) or str(c.get('name', ''))[:2] in sec for sec in null_secs):
                ctx.problems.append('「%s」在 %s 里的净流入为 null（未采集到），报告却写了 %+.2f 亿——'
                                    '应如实写「未取到」' % (c.get('name', ''), mk_name, v))
            elif not hits:
                ctx.warnings.append('「%s」不在 %s 的资金榜（前10/后5）里，无法核对 %+.2f 亿；若确在榜外请在候选名后注明取数来源'
                                    % (c.get('name', ''), mk_name, v))
            elif not any(abs(val - v) <= max(0.05, abs(v) * 0.005) for _, val in hits):
                ctx.problems.append('「%s」净流入写 %+.2f 亿，但 %s 里该板块为 %s——资金流数字必须照抄该文件'
                                    % (c.get('name', ''), v, mk_name,
                                       '、'.join('%s %+.2f 亿' % (sec, val) for sec, val in hits[:3])))
        # 常识校验：子行业净流入 > 母行业净流入（同为流入时）→ 必有一个口径有问题
        flow_by = {}
        for val, secs in pool.items():
            for sec in secs:
                if sec:
                    flow_by[sec] = val
        for c in cands:
            v = _yi(c.get('inflow'))
            key = re.split(r'[（(·/]', str(c.get('name', '')))[0].strip()
            par = PARENT.get(key) or next((p for k_, p in PARENT.items() if k_ in key), None)
            if v is not None and v > 0 and par and not any(abs(v - x) <= max(0.05, abs(v) * 0.005) for x in ind_vals):
                pv = next((x for sec, x in flow_by.items() if par in sec or sec in par), None)
                if pv is not None and v > pv + 0.5:
                    ctx.problems.append('「%s」净流入 %+.2f 亿大于其母行业「%s」的 %+.2f 亿——子行业不可能比所属母行业流入更多，'
                                        '说明两个数字口径不同（如概念口径混进了行业口径），须统一取自 market json'
                                        % (c.get('name', ''), v, par, pv))
        bb = mk.get('billboard')
        if isinstance(bb, dict):
            onb = {str(r.get('name')): r for r in bb.get('top', []) if r.get('name')}
            for c in cands[:3]:
                lead = re.split(r'[ （(]', str(c.get('leader', '')).strip())[0]
                hitb = onb.get(lead)
                txt = ' '.join(str(c.get(k, '')) for k in ('leader', 'continuity', 'precedent'))
                if hitb and '榜' not in txt:
                    ctx.warnings.append('「%s」的龙头「%s」今日上了龙虎榜（榜单净%s %.2f 亿），却未在报告中引用——'
                                        '涨停但榜单净卖出是分配信号，请在龙头封板强度格补上'
                                        % (c.get('name', ''), lead, '买' if hitb.get('net_yi', 0) >= 0 else '卖',
                                           abs(hitb.get('net_yi', 0))))
        if names:
            for c in cands[:2]:
                lead = re.split(r'[ （(]', str(c.get('leader', '')).strip())[0]
                if lead and lead not in names and not any(lead in n or n in lead for n in names):
                    ctx.warnings.append('「%s」的龙头「%s」不在 %s 的涨停池/资金榜里，请确认不是凭印象填写'
                                        % (c.get('name', ''), lead, mk_name))
    # 同形态先例（R30）：先按 precedent_review 回填昨日结果，再回查最近 5 个交易日快照
    hist_all = [dict(h, candidates=[dict(c0) for c0 in h.get('candidates', [])])
                for h in (prev.get('history') or [])]
    rev = {(str(x.get('date', '')), re.split(r'[（(·/]', str(x.get('name', '')))[0].strip()): x
           for x in (R.get('precedent_review') or [])}
    filled = 0
    for h in hist_all:
        for c0 in h.get('candidates', []):
            k0 = re.split(r'[（(·/]', str(c0.get('name', '')))[0].strip()
            x = rev.get((str(h.get('date', '')), k0)) or rev.get(('', k0))
            if x and (c0.get('outcome') == '待回填' or (x.get('revise') and x.get('outcome') != c0.get('outcome'))):
                if c0.get('outcome') != '待回填':   # v4.17：复评后按新口径改写已回填结果，须带 revise:true
                    ctx.warnings.append('已按复评口径改写 %s %s：%s → %s' % (h.get('date'), c0.get('name'), c0.get('outcome'), x.get('outcome')))
                c0['outcome'] = x.get('outcome', '待回填')
                c0['note'] = x.get('note', '')
                filled += 1
                _rl = re.split(r'[（(]', str(c0.get('role', '')))[0]   # v4.20：只看 role 主体，括号里的说明不算
                if '回避' in _rl and c0['outcome'] not in ('中性', '失败'):   # v4.20
                    ctx.problems.append('%s %s 是回避线，次日结果只能记「中性」（续弱/未居前）或「失败」（反走强、资金居前），不能记「%s」' % (
                        h.get('date'), c0.get('name'), c0['outcome']))
                if '回避' in _rl and c0['outcome'] == '失败' and not re.search(r'居前|居首|反走强|第\s*[1-3一二三]|前\s*[3三]', str(c0.get('note', ''))):
                    ctx.problems.append('%s %s 是回避线，记「失败」须是该方向资金居前／居首反走强，note 里写明名次；若只是续弱或小幅转正未居前，应记「中性」' % (
                        h.get('date'), c0.get('name')))
                if '回避' in _rl and mk is not None and c0['outcome'] in ('中性', '失败'):   # v4.24：居前＝申万资金前 3
                    _rk, _iv = _sw1_rank(mk, c0.get('name'))
                    if _rk is not None:
                        if c0['outcome'] == '失败' and _rk > 3:
                            ctx.problems.append('%s %s 记「失败」，但今日申万资金排第 %d（%s 亿）——「居前」＝前 3，应记「中性」' % (
                                h.get('date'), c0.get('name'), _rk, _iv))
                        if c0['outcome'] == '中性' and _rk <= 3 and (_iv or 0) > 0:
                            ctx.problems.append('%s %s 记「中性」，但今日申万资金排第 %d（+%s 亿）反走强居前——应记「失败」' % (
                                h.get('date'), c0.get('name'), _rk, _iv))
                if '观察' in _rl and '回避' not in _rl and c0['outcome'] not in ('中性', '延续'):   # v4.24：观察类只记中性/延续
                    ctx.problems.append('%s %s 是观察类（%s），次日结果只能记「中性」或「延续」，不能记「%s」' % (
                        h.get('date'), c0.get('name'), _rl, c0['outcome']))
                if '回避' not in _rl and mk is not None and c0['outcome'] == '延续':   # v4.33：龙头晋级与资金大幅流出同时成立时资金优先（R25）
                    _rk, _iv = _sw1_rank(mk, c0.get('name'))
                    if _rk is not None and _rk >= 22 and (_iv or 0) < 0:
                        ctx.problems.append('%s %s 记「延续」，但母行业今日申万资金第 %d（%s 亿）大幅流出——龙头晋级与资金大幅流出同时成立时资金优先（R25）：%s' % (
                            h.get('date'), c0.get('name'), _rk, _iv, '观察类记「中性」' if '观察' in _rl else '主攻/次线记「失败」'))
                if False and '观察' in _rl and '回避' not in _rl and c0['outcome'] == '失败':
                    ctx.problems.append('%s %s 是观察类（%s），按 10/22 复评口径未确认只记「中性」、确认延续记「延续」，不记「失败」' % (
                        h.get('date'), c0.get('name'), _rl))
    _pgn = GEARS[prev_gi] if isinstance(prev_gi, int) and 0 <= prev_gi < len(GEARS) else ''
    _ph = next((h for h in hist_all if h.get('date') == prev.get('date')), None)
    if _pgn and '退潮' not in _pgn and _ph:   # v4.28：昨日非退潮档，次线失败时 2b 不能判 B
        _sf = [c0.get('name') for c0 in _ph.get('candidates', []) if '次线' in str(c0.get('role', '')) and c0.get('outcome') == '失败']
        for s_ in scores:
            if s_['topic'] == '主线细分选择' and s_['grade'] in ('A', 'B') and _sf:
                ctx.problems.append('昨日（%s）是「%s」档，不适用退潮口径；昨日次线 %s 今日记失败 → 第 2b 条不能判 %s（次线没兑现判 C）' % (
                    prev.get('date'), _pgn, '、'.join(map(str, _sf)), s_['grade']))
    _p9 = prev_th.get('T9') or {}
    if _p9 and 'T9' not in fired and mk is not None and not re.search(r'停用|无主攻', str(_p9.get('val', ''))):   # v4.28：T9 回填提示
        _rk9, _iv9 = _sw1_rank(mk, re.split(r'[（(\s]', str(_p9.get('val', '')))[0])
        if _iv9 is not None and _iv9 < 0 and re.search(r'净流出', str(_p9.get('val', ''))):
            ctx.warnings.append('昨日 T9「%s」：该方向今日申万第 %s、%s 亿（净流出）——看是否已触发，触发就列进 fired，并把对应候选按定位回填（主攻/次线记失败；观察类只记中性）' % (_p9.get('val'), _rk9, _iv9))   # v4.32
    for t in th_list:   # v4.24：T5~T8 说明里「=NNNN」须与 val 点位一致
        if t['id'] in ('T5', 'T6', 'T7', 'T8'):
            _vm = re.search(r'(\d{4,5})', str(t.get('val', '')))
            _mm = re.search(r'[=＝]\s*(\d{4,5})', str(t.get('meaning', '')))
            _own = isinstance(th_in.get(t['id']), dict) and th_in[t['id']].get('meaning')
            if _vm and not _own and R.get('index', {}).get('close') is not None:   # 报告没写说明：代码按今日收盘重写，不沿用昨日文字
                t['meaning'] = '今收%s×%s=%s（代码按收盘重锚）' % (R['index']['close'], {'T5': '0.990', 'T6': '0.985', 'T7': '1.010', 'T8': '1.015'}[t['id']], _vm.group(1))
            elif _vm and _mm and _vm.group(1) != _mm.group(1):
                ctx.warnings.append('%s 说明写「=%s」，与触发值 %s 不一致' % (t['id'], _mm.group(1), _vm.group(1)))
    # —— v4.20：一致性检查（防止为过校验只改标签）——
    # ① 隔夜/主线阈值说明写了「已触发/达成」，fired 却没有（或反之）
    for t in th_list:
        if t['id'] in ('T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'T16'):
            continue          # T1~T8 由代码机械核对；T16 是纪律
        _mn = str(t.get('meaning', ''))
        # v4.23：否定词按「同一小句」判断（从上一个标点到该词，最多 15 字），「不认定昨日T10达成」「不能确认达成」都算否定
        _says = None
        for _mm in re.finditer(r'(已触发|已达成|今日达成|触发$|达成[（(·、，,；]|达成$)', _mn):
            _seg = re.split(r'[，,；;。！!？?（(）)、]', _mn[max(0, _mm.start() - 15):_mm.start()])[-1]
            if not re.search(r'[未不没无]|难以', _seg):
                _says = _mm; break
        _no = re.search(r'未触发|未达成|不触发', _mn)
        if _says and not _no and t['id'] not in fired:
            ctx.problems.append('%s 的说明写「%s」，fired 里却没有 %s——已触发就列进 fired，并把引用它的 7b/情景改写到 background；没触发就改说明' % (t['id'], _says.group(1), t['id']))
        if t['id'] in fired and _no and not _says:
            ctx.problems.append('%s 在 fired 里，说明却写「%s」——二者须一致' % (t['id'], _no.group(0)))
    # ③ 无主攻线时，正文不得写「主攻线 X」「主攻持有」
    if str(R.get('mainline', '')).startswith('无主攻'):
        _fc = R.get('forecast') or {}
        for _k, _v in (('forecast.mainline_continuity', _fc.get('mainline_continuity')), ('forecast.mainline_sub', _fc.get('mainline_sub')),
                       ('forecast.stocks', _fc.get('stocks')), ('action.main', (R.get('action') or {}).get('main'))):
            _t = str(_v or '')
            _m = re.search(r'(^|[；;。，,\s（(])主攻线\s*[:：]?\s*(?!空缺|仍空缺|无|未|不|待|须|需|可|才)[^\s；;，,）)]', _t) or re.search(r'(?<![无不非])主攻持有', _t)
            if _m:
                ctx.problems.append('mainline 是「无主攻线」，%s 却写了「%s」——无主攻线时正文只能写观察/回避，不得出现主攻线某某或主攻持有' % (_k, _t[max(0, _m.start()):_m.end() + 8].strip()))
    # ④ 今日候选 role 只能从固定几种里选
    _ROLES = ('主攻线', '次线', '承接线', '回避线', '防御观察', '承接观察', '观察')
    for c in cands:
        _r = str(c.get('role', '')).strip()
        if not any(_r.startswith(x) for x in _ROLES):
            ctx.problems.append('候选「%s」的 role「%s」不在固定几种之内（%s），说明写进 note 或括号里' % (str(c.get('name', ''))[:12], _r[:16], '／'.join(_ROLES)))
    # ⑤ 候选 role 与 mainline_sub 里的归类一致（回避 vs 非回避）
    _ms = str((R.get('forecast') or {}).get('mainline_sub', ''))
    _segs = []
    for _sg in re.split(r'[；;]', _ms):
        _lab = re.match(r'\s*(主攻线|次线|回避线|回避|防御观察|承接观察|观察)', _sg)
        if _lab:
            _segs.append(('回避' if _lab.group(1).startswith('回避') else '非回避', _sg))
    for c in cands:
        _key = re.split(r'[（(/·]', str(c.get('name', '')))[0].strip()[:4]
        if len(_key) < 2:
            continue
        _rl = '回避' if '回避' in re.split(r'[（(]', str(c.get('role', '')))[0] else '非回避'
        _in = [lab for lab, sg in _segs if _key in sg]
        if _in and _rl not in _in:
            ctx.problems.append('候选「%s」role 是%s，forecast.mainline_sub 却把它归在%s里——两处须一致' % (
                str(c.get('name', ''))[:12], '回避线' if _rl == '回避' else '非回避（观察/次线等）', '观察/次线等' if _rl == '回避' else '回避'))
    if False and GEARS[gi].startswith('退潮'):   # v4.17 提示已由 v4.28 的报红检查取代（退潮档只列回避线＋至多 1 个防御观察）
        _cj = [str(c.get('name', ''))[:12] for c in cands if '承接' in str(c.get('role', '')) and '回避' not in str(c.get('role', ''))]
        if _cj:
            ctx.warnings.append('今日档位 %s，按 10/22 复评只列回避线＋至多 1 个「防御观察」，不列承接类：%s' % (GEARS[gi], '、'.join(_cj)))
        _dg = [c for c in cands if '观察' in re.split(r'[（(]', str(c.get('role', '')))[0] and '回避' not in re.split(r'[（(]', str(c.get('role', '')))[0]]
        if len(_dg) > 1:
            ctx.warnings.append('退潮档「防御观察」至多 1 个，当前 %d 个' % len(_dg))
    pend = [(h['date'], c0['name']) for h in hist_all for c0 in h.get('candidates', [])
            if c0.get('outcome') == '待回填' and h.get('date') != R['date']]
    if pend:
        ctx.problems.append('最近快照里有 %d 条候选的次日结果未回填（%s…）——'
                            'precedent_review 须逐条写 {date, name, outcome: 延续|失败|中性}'
                            % (len(pend), '、'.join('%s %s' % p for p in pend[:3])))
    hist = hist_all[-5:]
    hist_fail = {}          # 名称关键词 -> (日期, 定位, 结果)
    for h in hist:
        for c0 in h.get('candidates', []):
            if c0.get('outcome') == '失败' and '回避' not in re.split(r'[（(]', str(c0.get('role', '')))[0]:   # v4.32：回避线记失败＝回避错过（该线反而强），不算同形态先例
                k0 = re.split(r'[（(·/]', str(c0.get('name', '')))[0].strip()
                if k0:
                    hist_fail[k0] = (h.get('date'), c0.get('role', ''), c0.get('note', ''))
    prec_rows = []
    for h in hist:
        for c0 in h.get('candidates', []):
            prec_rows.append([h.get('date'), c0.get('name'), c0.get('role', ''),
                              c0.get('type', ''), c0.get('outcome', '待回填')])
    prec_html = table(ctx, ['日期', '候选线', '当日定位', '类型', '次日结果'], prec_rows,
                      caption='最近 5 日候选快照（R30 同形态先例的核对依据，由代码自动累积）',
                      force_stack=True) if prec_rows else (
        '<p class="src">最近 5 日候选快照为空（首次运行或 state 未累积），本日起自动累积。</p>')

    for c in cands:
        nm = str(c.get('name', ''))
        key = re.split(r'[（(·/]', nm)[0].strip()
        hit = next(((k, v) for k, v in hist_fail.items() if k and (k in nm or key and key in k)), None)
        pr = str(c.get('precedent', ''))
        if hit and not pr.startswith('✗'):
            ctx.problems.append('「%s」在 %s 曾作为%s出现且次日失败（同形态先例），本次「同形态先例」列却写「%s」——'
                                '有失败先例的只能降为观察，不得进主攻线' % (nm, hit[1][0], hit[1][1] or '候选', pr or '空'))
        if hit and c.get('role', '').startswith('主攻'):
            ctx.problems.append('「%s」有本周同形态失败先例（%s），不得定为主攻线' % (nm, hit[1][0]))
        if not pr or pr == '—':
            ctx.warnings.append('「%s」未填「同形态先例」列——须回查最近 5 日快照后写 ✓ 无先例 或 ✗ <日期> 同形态失败' % nm)
    # R30 口径：同形态 = 同一板块/产业链。✗ 引用的先例须能在 history 里对上同名候选
    def _core(x):
        return re.sub(r'[（(].*?[)）]', '', str(x or '')).replace('·', '/').split('/')
    def _grams(x):
        g = set()
        for seg in _core(x):
            seg = seg.strip()
            g.update(seg[i:i + 2] for i in range(max(1, len(seg) - 1)))
        return {t for t in g if len(t) == 2}
    for c in cands:
        pr = str(c.get('precedent', ''))
        if not pr.startswith('✗'):
            continue
        dm = re.search(r'(\d{2})-(\d{2})', pr)
        pool = [c0 for h in hist for c0 in h.get('candidates', [])
                if not dm or str(h.get('date', ''))[5:] == '%s-%s' % dm.groups()]
        _dts = set('%s-%s' % x for x in re.findall(r'(\d{2})-(\d{2})', pr))
        _same = [c0 for h in hist for c0 in h.get('candidates', [])
                 if (not _dts or str(h.get('date', ''))[5:] in _dts) and _grams(c.get('name')) & _grams(c0.get('name'))]
        _key = re.split(r'[（(·/]', str(c.get('name', '')))[0].strip()
        if not any(k and (k in str(c.get('name', '')) or _key and _key in k) for k in hist_fail):   # v4.32
            ctx.warnings.append('「%s」写了 ✗ 同形态先例，但代码在最近 5 日快照里没找到同板块的做多类失败（回避线的「回避错过」不算）——核对后多半应写「✓ 无先例」' % c.get('name', ''))
        if _same and all('回避' in re.split(r'[（(]', str(c0.get('role', '')))[0] for c0 in _same):   # v4.32
            ctx.problems.append('「%s」的同形态先例引用的是%s回避线「%s」——回避线记失败＝回避错过（该线反而走强），不算同形态先例，应写「✓ 无先例」'
                                % (c.get('name', ''), ('%s-%s ' % dm.groups()) if dm else '', _same[0].get('name')))
        if pool and not any(_grams(c.get('name')) & _grams(c0.get('name')) for c0 in pool):
            ctx.warnings.append('「%s」的同形态先例引用的是%s的「%s」——不同板块，类型相似不构成同形态先例；'
                                '应写「✓ 无先例」，相似类型的失败可在连续性理由里提一句'
                                % (c.get('name', ''), ('%s-%s ' % dm.groups()) if dm else '',
                                   '、'.join(str(c0.get('name')) for c0 in pool[:2])))
    # 环境匹配
    for c in cands:
        if str(c.get('role', '')).startswith('主攻') and str(c.get('env_match', '')).startswith('✗'):
            ctx.problems.append('「%s」环境匹配为 ✗（与当前%s环境不匹配），不得定为主攻线' % (c.get('name', ''), GEARS[gi]))

    for c in cands:
        if '未取到' in str(c.get('inflow', '')) and not (flow_missing and all_missing):
            ctx.problems.append('候选「%s」主力净流入未取到，不得进入排序（排序第一优先级缺值）'
                                '——若是整体资金流未取到，则所有候选须统一写「未取到」并改按涨停家数排序' % c.get('name', ''))
        cv = c.get('continuity', '')
        marks = len(re.findall(r'路径\s?[ABC]', cv))
        if cv.startswith('✓') and (marks > 1 or (marks == 0 and '连续' not in cv)):
            ctx.problems.append('连续性验证须写且只写一条路径（A/B/C）：「%s」' % cv[:24])
    # v4.24：路径 A 声称「昨日前3」时，用目录里前一交易日的 market json 核对申万排名
    _pmk = None
    _pd = str(prev.get('date', '')).replace('-', '')
    if _pd:
        try:
            with open('market_%s.json' % _pd, encoding='utf-8') as _f:
                _pmk = json.load(_f)
        except Exception:
            _pmk = None
    for c in cands:
        cv = str(c.get('continuity', ''))
        if cv.startswith('✓') and re.search(r'路径\s?A|连续\s?2\s?日|两日前|前\s?3', cv):
            if _pmk is None:
                ctx.warnings.append('「%s」走路径 A，但目录里没有前一交易日 market_%s.json，无法核对昨日排名' % (c.get('name', ''), _pd))
            else:
                _rk, _iv = _sw1_rank(_pmk, c.get('name'))
                if _rk is not None and _rk > 3:
                    ctx.problems.append('「%s」连续性写路径 A，但昨日（%s）申万资金排第 %d（%s 亿），不满足「昨日前 3」——改走路径 B/C 或判 ✗、降为次线' % (
                        c.get('name', ''), prev.get('date'), _rk, _iv))
    # v4.30①：路径 B 自动核对——候选点名个股或所属细分里有「较昨日板数 +1」的连板股，连续性却判 ✗
    if mk is not None and _pmk is not None:
        def _lad(m):
            out = {}
            for r_ in ((m.get('limit') or {}).get('ladder') or []):
                for s_ in (r_.get('stocks') or []):
                    out[s_] = r_.get('boards')
            return out
        _tb, _yb = _lad(mk), _lad(_pmk)
        _sec = {r_.get('stock'): str(r_.get('sector') or '') for r_ in ((mk.get('limit') or {}).get('top_boards') or []) if r_.get('stock')}
        _prom = [(s_, b_) for s_, b_ in _tb.items() if isinstance(b_, int) and b_ >= 2 and _yb.get(s_) == b_ - 1]
        for c in cands:
            cv = str(c.get('continuity', ''))
            if '回避' in str(c.get('role', '')) or not cv.startswith('✗'):
                continue
            _txt = ' '.join(x for _, x in _iter_strings(c))
            for s_, b_ in _prom:
                _sc = _sec.get(s_, '')
                if s_ in cv:
                    continue        # 连续性里已点名并说明，不重复报
                if s_ in _txt or (len(_sc) >= 2 and _sc[:2] in _txt):
                    ctx.problems.append('「%s」连续性判 ✗，但 %s（%s）昨日 %d 板→今日 %d 板——路径 B 可能成立：属于该方向就改判 ✓（路径B），'
                                        '不属于就在 continuity 里点名写明为什么不算' % (c.get('name', ''), s_, _sc or '细分未知', b_ - 1, b_))
                    break
    # v4.30②：主攻线已定时，T9/T10 须绑在主攻线（板块名或龙头）上
    if cands and ml and not ml.startswith('无主攻'):
        _mc = next((c for c in cands if str(c.get('role', '')).startswith('主攻')), None)
        _tok = {x for x in re.split(r'[（()）·/、，,；;\s]+', ml) if len(x) >= 2 and not re.search(r'\d', x)}
        _tok |= {x[:-1] for x in list(_tok) if x.endswith('线')}
        if _mc is not None:
            _tok |= {x for x in re.split(r'[（()）·/、，,；;\s]+', str(_mc.get('name', '')) + '/' + str(_mc.get('type', ''))) if len(x) >= 2 and not re.search(r'\d', x)}
            _mtxt = ' '.join(x for _, x in _iter_strings(_mc)) + ' ' + ml
            _tok |= {n_ for n_ in (names if mk is not None else set()) if n_ and n_ in _mtxt}
        _tok -= {'申万', '主攻线', '主攻', '次线', '观察', '防御观察', '回避线'}
        for _id in ('T9', 'T10'):
            _v = str((ctx.TH.get(_id) or {}).get('val', ''))
            if _v and not any(k_ in _v for k_ in _tok):
                ctx.problems.append('主攻线是「%s」，%s 的 val「%s」却没有绑在主攻线上——T9 写主攻线龙头/板块的熄火条件，T10 写主攻线的确认条件' % (ml, _id, _v[:30]))
    # v4.30③：wrong_if / 档位情景里写的仓位不得超过「今日仓位上限 +20pp」（R24）
    _fc3 = R.get('forecast') or {}
    _ptxt = [('forecast.gear', str(_fc3.get('gear', '')))] + [('wrong_if[%d].act' % i, str((w or {}).get('act', ''))) for i, w in enumerate(R.get('wrong_if') if isinstance(R.get('wrong_if'), list) else []) if isinstance(w, dict)]
    for _k, _t in _ptxt:
        for _m in re.finditer(r'(加仓|仓位|加到|加至|升至|提至|提到|升到)[^0-9%，,；;。]{0,4}(\d{1,3})\s*%', _t):
            if isinstance(pos, (int, float)) and int(_m.group(2)) / 100.0 > pos + 0.2 + 1e-9:
                ctx.problems.append('%s 写了「%s」，超过今日上限 %d%% + 20pp＝%d%%（R24 单日最多加 20pp）' % (
                    _k, _m.group(0), round(pos * 100), round(pos * 100) + 20))
    # v4.30④：主攻线已定时，标题/摘要/7a 不得写「主攻未确认 / 无主攻线 / 主攻空缺」
    if ml and not ml.startswith('无主攻'):
        _src = [('headline', R.get('headline'))] + [('s7a', x) for _, x in _iter_strings(R.get('s7a'))] + [('action.main', (R.get('action') or {}).get('main'))]
        for _k, _v in _src:
            _t = str(_v or '')
            _m = re.search(r'(?<![昨前])(?<!昨日)(主攻(?:线)?(?:未确认|空缺|缺位)|主线未确认|无主攻线)', _t)
            if _m:
                ctx.problems.append('mainline 是「%s」，%s 却写「%s」——主攻线已定，标题/摘要/7a 须一致' % (ml, _k, _m.group(0)))
                break
    if '退潮' in GEARS[gi]:   # v4.28：退潮档候选只列回避线＋至多 1 个防御观察
        _nb = [c for c in cands if '回避' not in str(c.get('role', ''))]
        _bad = [c for c in _nb if '防御观察' not in str(c.get('role', ''))]
        if _bad:
            ctx.problems.append('今日「%s」属退潮档，候选只能列回避线＋至多 1 个「防御观察」；%s 须改 role 或移入 excluded' % (
                GEARS[gi], '、'.join('「%s」(%s)' % (c.get('name', ''), c.get('role', '')) for c in _bad)))
        if len(_nb) - len(_bad) > 1:
            ctx.problems.append('退潮档「防御观察」至多 1 个，当前 %d 个' % (len(_nb) - len(_bad)))
    # v4.24：R31 生效期间（state.r31_active，解除只认 T13 达成），电子/半导体/PCB/通信方向 role 只能是回避线
    r31_active = bool(prev.get('r31_active', True)) or bool(R.get('r31_reactivate'))
    if 'T13' in fired and 'T11' not in fired:   # v4.27：与 T11 冲突时不解除
        r31_active = False
    if r31_active:
        _anchor = ' '.join(str(ctx.TH.get(k, {}).get('val', '')) for k in ('T9', 'T10'))   # v4.35：R31 生效时 T9/T10 与 wrong_if 动作不得绑科技方向
        if SECTOR_TECH.search(_anchor) or re.search(r'科技', _anchor):
            ctx.problems.append('R31 生效中：T9/T10 绑定了电子/半导体/PCB/通信方向（「%s」）——这些方向只能是回避线，不能作防御观察或确认锚；请改绑非科技的防御观察，或无观察时写明「无防御观察，T9/T10 仅记指数广度」' % _anchor[:60])
        for _w in (R.get('wrong_if') or []) if isinstance(R.get('wrong_if'), list) else []:
            _act = str(_w.get('act', '')) if isinstance(_w, dict) else ''
            _hit = [m for m in re.finditer(r'(科技|电子|半导体|PCB|光通信|通信)[^，。；,]{0,6}(试探|观察|承接|做多|加仓|回踩)', _act)
                    if not re.search(r'不|勿|禁|未', _act[max(0, m.start() - 7):m.start()])]
            if _hit:
                ctx.problems.append('R31 生效中：wrong_if「%s」的动作写了科技方向（「%s」）——R31 期间科技只回避，上修动作只能落在非科技防御观察或「新增 0」' % (_w.get('dir', ''), _act[:40]))
        for c in cands:
            _rl = re.split(r'[（(]', str(c.get('role', '')))[0]
            if SECTOR_TECH.search(str(c.get('name', '')) + str(c.get('type', ''))) and '回避' not in _rl:
                ctx.problems.append('R31 生效中（T13 未达成）：「%s」属电子/半导体/PCB/通信方向，role 只能写「回避线」，当前为「%s」' % (
                    c.get('name', ''), c.get('role', '')))
    _lack = [str(c.get('name', ''))[:12] for c in cands if '回避' not in str(c.get('role', '')) and not (c.get('counter') and c.get('confirm'))]
    if _lack:
        ctx.warnings.append('候选缺「反方 counter」或「怎么确认 confirm」：%s' % '、'.join(_lack))
    ctab = table(ctx, ['候选线', '主力净流入', '涨停·首板', '龙头封板强度', '隔夜/政策催化',
                       '同形态先例', '环境匹配', '连续性验证', '反方（什么情况下不涨）', '怎么确认（时间＋阈值）', '排名', '定位'],
                 [[c.get('name'), c.get('inflow'), c.get('limitups'), c.get('leader'), c.get('catalyst', '—'),
                   c.get('precedent', '—'), c.get('env_match', '—'), c.get('continuity', '—'),
                   c.get('counter', '—'), c.get('confirm', '—'),
                   i + 1, c.get('role', '')] for i, c in enumerate(cands)],
                 caption='主线候选排序（R20）—— 排完序再进六闸门', num=[1, 2], force_stack=True)
    # 「最活跃」与「值得参与」分开（R29）
    sp = R.get('spotlight') or {}
    act, par = sp.get('most_active'), sp.get('participate')
    if not act or not par:
        ctx.problems.append('缺 spotlight：须分别写出「明日最活跃」与「明日值得参与」，两者可以不是同一条线')
    sp_html = ('<div class="card ok"><b style="font-size:13.5px">最活跃 ≠ 值得参与（R29）</b>'
               '<div class="tbwrap" style="margin:8px 0 0"><table class="stack">'
               '<thead><tr><th>问题</th><th>答案</th><th>依据</th></tr></thead><tbody>'
               '<tr><td class="meta" data-label="问题">明日最活跃</td><td class="meta" data-label="答案">%s</td>'
               '<td data-label="依据">%s</td></tr>'
               '<tr><td class="meta" data-label="问题">明日值得参与</td><td class="meta" data-label="答案">%s</td>'
               '<td data-label="依据">%s</td></tr></tbody></table></div>'
               '<p class="src" style="margin:8px 0 0">「最活跃」讲的是成交与话题，「值得参与」讲的是延续性。'
               '两者不是同一条线时不矛盾——最活跃的那条往往正是本周已经走过一遍的那条。'
               '<b>主攻线只能从「值得参与」里出</b>，不得直接把最活跃的那条推上主攻位。</p></div>') % (
        ctx.rich((act or {}).get('name', '—')), ctx.rich((act or {}).get('why', '—')),
        ctx.rich((par or {}).get('name', '—')), ctx.rich((par or {}).get('why', '—')))
    if par and cands:
        pname = str(par.get('name', ''))
        if '无' in pname or '空' in pname:
            if R.get('mainline') not in ('', '无主攻线'):
                ctx.problems.append('spotlight.participate 为「无」，mainline 应写「无主攻线」')
        else:
            key = re.split(r'[（(·/]', pname)[0].strip()
            if key and key not in str(R.get('mainline', '')) and str(R.get('mainline', ''))[:2] not in pname:
                ctx.problems.append('mainline「%s」与 spotlight.participate「%s」对不上——主攻线只能从「值得参与」里出'
                                    % (R.get('mainline', ''), pname))
    gates = R.get('gates', {})
    gtab = table(ctx, ['#', '判据'] + gates.get('lines', ['主攻线', '次线', '回避线']),
                 gates.get('rows', []) + [['—', '风险命中数（≥2 不追高）'] + [str(x) for x in gates.get('risk', [])]],
                 caption='六闸门并排判定', force_stack=False)
    r27 = BLK['r27'].replace('隔夜催化可推翻熄火判定（R27）', '隔夜 / 政策催化可推翻熄火判定（R27 · R28）').replace(
        '<li>新品发布或提前发布</li>', '<li>新品发布或提前发布</li><li>国家级 / 部委级政策文件落地（R28）</li>').replace('「隔夜催化」列', '「隔夜/政策催化」列')
    # v4.12：资金比例五日并排（代码从目录里的历次 market json 拼出，模型不填）
    _days, _fs = _flow_series([c.get('name') for c in cands], R['date'])
    _rk = sorted([x for x in _fs if isinstance(x['ratio_today'], (int, float))], key=lambda x: -x['ratio_today'])
    _rank = {x['name']: i + 1 for i, x in enumerate(_rk)}
    def _fmt(v, u):
        return '—' if not isinstance(v, (int, float)) else ('%+.2f%s' % (v, u))
    def _d2(v):
        return '—' if not isinstance(v, (int, float)) else ('%+.2f %s' % (v, '↓减速' if v < 0 else ('↑加速' if v > 0 else '持平')))
    ftab = table(ctx, ['线', '五日序列（%s）' % ' → '.join(_days) if _days else '五日序列', '形态', '较昨日 Δ', '成交额', '影子名次'],
                 [[x['name'], ' → '.join(_fmt(v, u) for v, u in x['seq']), x['shape'], _d2(x.get('d2')),
                   ('%g 亿' % x['amount']) if x['amount'] else '—', _rank.get(x['name'], '—')] for x in _fs],
                 caption='候选线资金比例五日并排（净流入 ÷ 成交额；没有成交额的日子退回净流入「亿」）——先判形态再看规模；'
                         '较昨日 Δ＝今日资金比例 − 昨日（博主口径的「资金二阶」，＜0 为减速；与六闸门 #3 不是一回事）；'
                         '影子名次与 Δ 只作对照，10/22 复评前不参与定主攻', force_stack=True) if cands else ''
    # v4.13：候选线记录项（消息轮次／盘面四问／资金集中度）——只记录，10/22 复评前不参与定主攻
    _nr = [c for c in cands if '回避' not in str(c.get('role', ''))]
    _lk = [str(c.get('name', ''))[:12] for c in _nr if not (c.get('news_round') and c.get('four_checks') and c.get('concentration'))]
    if _lk:
        ctx.warnings.append('候选缺记录项 news_round／four_checks／concentration：%s' % '、'.join(_lk))
    _bf = [str(c.get('name', ''))[:12] for c in _nr if c.get('four_checks') and not re.match(r'\s*[0-4]\s*/\s*4', str(c.get('four_checks')))]
    if _bf:
        ctx.warnings.append('four_checks 须以「x/4」开头（板块效应／龙头／中军／梯队各算 1）：%s' % '、'.join(_bf))
    rtab = table(ctx, ['线', '消息轮次（首发日·第几轮）', '盘面四问（x/4）', '资金集中度（龙头一只 vs 板块合计）'],
                 [[c.get('name'), c.get('news_round', '—'), c.get('four_checks', '—'), c.get('concentration', '—')] for c in _nr],
                 caption='候选线记录项（v4.13 · 只记录，10/22 复评前不参与定主攻）——A 股盘中已交易过的外盘映射不算新消息；'
                         '单只个股净流入 ≥ 板块合计＝钱没扩散', force_stack=True) if _nr else ''
    _qa = quiet_accum(mk) if mk is not None else []   # v4.26
    qtab = table(ctx, ['方向', '当日净流入', '5 日净流入', '5 日涨幅', '是否在候选'],
                 [[r['sector'], '%+.2f 亿' % r['inflow_yi'], '%+.2f 亿' % r['inflow_5d_yi'], '%+.2f%%' % r['pct_5d'],
                   '是' if _sw1_rank(mk, r['sector'])[0] is not None and any(re.split(r'[（(·/]', str(c.get('name', '')))[0].strip().startswith(r['sector']) for c in cands) else '否']
                  for r in _qa[:8]],
                 caption='钱进价没涨（v4.26 · 代码扫描）：申万一级当日净流入>0、5 日净流入>0、5 日涨幅<0——可能在建仓的方向；不在候选里的，在 excluded 写一句为什么不列',
                 force_stack=True) if _qa else ''
    if _qa:
        _miss = [r['sector'] for r in _qa[:3]
                 if not any(re.split(r'[（(·/]', str(c.get('name', '')))[0].strip().startswith(r['sector']) for c in cands)
                 and not any(str(x.get('name', '')).startswith(r['sector']) for x in (R.get('excluded') or []))]
        if _miss:
            ctx.warnings.append('「钱进价没涨」前列方向既不在候选也不在 excluded：%s——列入候选或在 excluded 写不列理由' % '、'.join(_miss))
    exc = R.get('excluded') or []
    if not exc:
        ctx.warnings.append('缺 excluded：资金榜/涨幅榜前排但没选进候选的线，逐条写不列理由（反面数据）')
    etab = table(ctx, ['线', '不列理由（反面数据）'], [[x.get('name'), x.get('why')] for x in exc],
                 caption='前排但不列的线（钱进价不涨／价涨钱走／20 日仍流出／资金比例回落）', force_stack=True) if exc else ''
    hot = R.get('hot_boards') or []
    if not hot:
        ctx.warnings.append('缺 hot_boards：近 5 个交易日热过的板块逐个表态「追／不追／等回调／只看不做」')
    _badc = [str(x.get('name')) for x in hot if x.get('chase') not in ('追', '不追', '等回调', '只看不做')]
    if _badc:
        ctx.problems.append('hot_boards 的 chase 只能是 追／不追／等回调／只看不做：%s' % '、'.join(_badc))
    htab = table(ctx, ['板块', '近几天热在哪（带数据）', '明天大概怎么走', '追不追', '理由（含反面数据）'],
                 [[x.get('name'), x.get('recent'), x.get('outlook'), x.get('chase'), x.get('why')] for x in hot],
                 caption='热门板块追不追（近 5 个交易日热过的逐个表态）', force_stack=True) if hot else ''
    H.append(section('s8', '8 · 主线判定', '候选先排序（R20），催化可推翻熄火（R27/R28），再进六闸门。',
        sp_html + ctab + ftab + rtab + qtab + etab + htab + prec_html + r27 + BLK['path'] + gtab + BLK['lr'] + blocks(ctx, R.get('s8'))))

    # ═══ 9 · 操作台 ═══
    ops = R.get('ops', {})
    H.append(section('s9', '9 · 操作台', '进场 / 退出 / 验证 三合一，阈值一律用编号。',
        table(ctx, ['标的', '类型', '成本/仓位', '个股退出触发'], [['（手填）', '持仓', '—', '—'], ['（手填）', '观察', '—', '—']],
              caption='持仓 / 观察仓 —— 使用者手填，AI 不得填充或推测', force_stack=False)
        + table(ctx, ['条件', '具体触发', '仓位'], ops.get('entry', []), caption='进场条件（盘中可观测）', force_stack=True)
        + table(ctx, ['对象', '触发', '动作'], ops.get('exit', []), caption='退出 / 降级触发（触发即执行）', force_stack=True)
        + table(ctx, ['#', '指标', '正向 ▲', '反向 ▼'], ops.get('verify', []), caption='验证指标（正反双写）', force_stack=True)
        + '<div class="card bad"><b style="font-size:13.5px">禁忌清单（唯一一处）</b><ul>%s</ul></div>' % ''.join('<li>%s</li>' % ctx.rich(x) for x in _as_list(ctx, ops.get('taboo'), 'ops.taboo'))
        + BLK['noon']))

    # ═══ 10~12 ═══
    H.append(section('s10', '10 · 群体心理', '过路钱还是主动进攻。',
        blocks(ctx, R.get('s10')) + '<p><b>结论：</b>%s</p><p><b>明日关键看：</b>%s</p>' % (ctx.rich(R.get('psy_conclusion')), ctx.rich(R.get('psy_watch')))))
    _we = R.get('window_events') or []   # v4.26：买入日到卖出日之间已定日程，逐条写受益/受损/盘中确认
    if not _we:
        ctx.warnings.append('缺 window_events：买入日→卖出日之间已定日程，逐条写 when/what/up(受益)/down(受损)/confirm(盘中确认信号)')
    else:
        _bw = [str(x.get('what', '?'))[:10] for x in _we if not (x.get('up') and x.get('down') and x.get('confirm'))]
        if _bw:
            ctx.warnings.append('window_events 每条须写 up（受益）/down（受损）/confirm（盘中确认）：%s 不全' % '、'.join(_bw))
        _real = [x for x in _we if re.search(r'\d{1,2}\s*[-/.月]\s*\d{1,2}', str(x.get('when', ''))) and not _WE_GENERIC.match(str(x.get('what', '')).strip())]
        if not _real:   # v4.27：至少一条带日期的真实日程
            ctx.warnings.append('window_events 没有带日期的真实日程（只有买入日/卖出日/盘中这类泛条目）——请写已定事件，如数据公布、会议、选举、财报，when 写日期（如「11-04 北京白天」）')
    wtab = table(ctx, ['时点', '事件', '往好走·受益', '往坏走·受损', '盘中确认信号'],
                 [[x.get('when'), x.get('what'), x.get('up'), x.get('down'), x.get('confirm')] for x in _we],
                 caption='卖出日前的已知事件（v4.26）', force_stack=True) if _we else ''
    H.append(section('s11', '11 · 风险窗口日历', '时点选择 &gt; 方向判断。', wtab + blocks(ctx, R.get('s11'))))
    H.append(section('s12', '12 · 数据源', '正文不逐条标注，统一收在此处。', blocks(ctx, R.get('s12'))))

    # —— 页面顶部汇总 warnings（橙）；problems 由页面 JS 之外再单独放一块红框 ——
    diag = ''
    if ctx.problems:
        diag += '<div class="card bad"><b style="font-size:13.5px">渲染器校验未通过（%d）</b><ul>%s</ul></div>' % (
            len(ctx.problems), ''.join('<li>%s</li>' % esc(p) for p in ctx.problems))
    if ctx.warnings:
        diag += '<div class="card warn"><b style="font-size:13.5px">需在正文说明的偏离（%d）</b><ul>%s</ul></div>' % (
            len(ctx.warnings), ''.join('<li>%s</li>' % esc(w) for w in ctx.warnings))

    toc = ''.join('<li><a href="#s%d"><b>%d</b>%s</a></li>' % (i, i, n) for i, n in enumerate(
        ['摘要', '阈值总表', '校验与战绩', '规则库', '口径说明', '指数资金', '情绪档位', '隔夜外盘', '主线判定', '操作台', '群体心理', '风险日历', '数据源']))

    # —— 明日状态 ——
    state = {
        'date': R['date'], 'next': R['next'], 'gear_index': gi, 'position_max': pos,
        'mainline': R.get('mainline', ''),
        'thresholds': [dict({k: t[k] for k in ('id', 'dir', 'item', 'val', 'meaning')}, scope=scope_of(t)) for t in th_list],
        'fired': sorted(fired),
        'r31_active': r31_active,   # v4.24：R31 生效状态（T13 达成即解除；10Y 收盘重新破 5% 时报告写 r31_reactivate:true）
        'rules': rules,
        'history': (hist_all + [{
            'date': R['date'], 'mainline': R.get('mainline', ''),
            'candidates': [{'name': c.get('name'), 'role': c.get('role', ''), 'type': c.get('type', ''),
                            'outcome': '待回填'} for c in cands],
        }])[-5:],
        'forecast': {
            'core': R.get('forecast', {}).get('core'),
            'mainline_continuity': R.get('forecast', {}).get('mainline_continuity'),
            'mainline_sub': R.get('forecast', {}).get('mainline_sub'),
            'gear': R.get('forecast', {}).get('gear'),
            'index': R.get('forecast', {}).get('index'),
            'stocks': R.get('forecast', {}).get('stocks'),
            'watch': items, 'scenarios': scen, 'background': q.get('background'),
            'index_scenarios': [{'name': x.get('name'), 'prob': x.get('prob'), 'cond': _tok2num(x.get('cond', ''), ctx.TH)} for x in isc],   # v4.25：存数字，次日可机械判定
            'taboo': _as_list(ctx, ops.get('taboo'), 'ops.taboo'),
            'wrong_if': R.get('wrong_if'), 'window_events': R.get('window_events') or [],
            'gear_scenarios': [{'name': x.get('name'), 'prob': x.get('prob')} for x in gsc],
        },
    }

    page = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><!-- render.py ' + RENDER_VERSION + ' -->'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<title>A股复盘 · %s → 次日推演</title>'
            '<script type="application/json" id="meta">%s</script>'
            '<script type="application/json" id="state">%s</script>'
            '<style>%s</style></head><body>'
            '<header class="bar"><div class="bar-in"><span class="bar-date" id="hDate">%s</span>'
            '<span class="bar-gear" id="hGear">%s · 仓位≤%d%%</span>'
            '<button class="bar-btn" id="tocBtn" aria-expanded="false" aria-controls="toc">目录</button></div></header>'
            '<nav id="toc" aria-label="模块导航"><ol>%s</ol></nav>'
            '<main class="wrap">%s%s<footer>本报告为方法论体系每日例行产物，不构成投资建议。<br>'
            '模板 v4 · 数据/渲染分离 · 由代码节点生成 · render.py ' + RENDER_VERSION + '%s</footer></main>'
            '<div id="pinbar" aria-label="今日锁定阈值"><span class="lbl">阈值</span></div>'
            '<script>%s</script></body></html>') % (
        esc(R['date']), json.dumps(meta, ensure_ascii=False).replace('</', '<\\/'),
        json.dumps(state, ensure_ascii=False).replace('</', '<\\/'), CSS,
        esc(R['date'] + ' ' + R.get('weekday', '')), GEARS[gi], int(round(pos * 100)),
        toc, diag, ''.join(H), mkfoot, JS)
    _P, _W = list(dict.fromkeys(ctx.problems)), list(dict.fromkeys(ctx.warnings))
    try:   # v4.19：每次渲染把 problems 追加记到 render_log_YYYYMMDD.txt，Coze 隐藏中间轮次也能事后查
        from datetime import datetime as _dt
        with open('render_log_%s.txt' % str(R.get('date', '')).replace('-', ''), 'a', encoding='utf-8') as _f:
            _f.write('=== %s · render.py %s · problems %d · warnings %d ===\n%s\n' % (
                _dt.now().strftime('%H:%M:%S'), RENDER_VERSION, len(_P), len(_W), '\n'.join('P ' + x for x in _P) or 'P （无）'))
    except Exception:
        pass
    return page, state, _P, _W


# ───────────────────────── Coze 代码节点入口 ─────────────────────────
async def main(args):
    """
    输入参数（在 Coze 代码节点里配置）：
      report      : String，判断节点输出的 report JSON
      prev_state  : String，昨日 state JSON（首日用 state_init.json 的内容）
    输出：
      html        : String，今日报告全文
      state       : String，存入变量/数据库，供明日作为 prev_state
      problems    : String，校验问题（空字符串表示通过）
    """
    p = args.params
    rep = p['report']; prev = p.get('prev_state') or '{}'
    rep = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', rep.strip())) if isinstance(rep, str) else rep
    prev = json.loads(prev) if isinstance(prev, str) else prev
    page, state, probs, warns = render(rep, prev)
    return {'html': page, 'state': json.dumps(state, ensure_ascii=False),
            'problems': '\n'.join(probs), 'warnings': '\n'.join(warns)}
