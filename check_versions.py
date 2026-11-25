# -*- coding: utf-8 -*-
"""
check_versions.py v1.37 —— 一条命令核对当前目录里的脚本/提示词是不是最新版（不依赖文件管理器显示的大小）
用法：把本文件放进要检查的目录（Coze 项目目录），运行  python3 check_versions.py
只读文件，不改任何东西；仅依赖标准库。
v1.2：render.py → v4.11、holding_watch.py → v5.5；新增判断节点提示词（v4.9，两种文件名都认）
v1.3：daily_pull.py → v2.6（默认只跑 base,kline；不覆盖取数工具的 market json）
v1.4：新增 fill_stock_table.py v1.2（每日底表_ETF 目录）
v1.5：render.py → v4.12、判断节点提示词 → v4.10
v1.6：render.py → v4.13、判断节点提示词 → v4.11
v1.7：render.py → v4.14、判断节点提示词 → v4.12
v1.8：render.py → v4.15
v1.9：render.py → v4.16
v1.10：holding_watch.py → v5.6
v1.11：判断节点提示词 → v4.13；新增 daily_ctx.py v1.0
v1.12：holding_watch.py → v5.7
v1.13：holding_watch.py → v5.8
v1.14：holding_watch.py → v5.9
v1.15：render.py → v4.17、判断节点提示词 → v4.14（10/22 复评落地）
v1.16：render.py → v4.18（摘要卡两行排版）
v1.17：render.py → v4.19、判断节点提示词 → v4.15、daily_ctx.py → v1.1（机械判定，减少排错）
v1.18：render.py → v4.20、判断节点提示词 → v4.16（一致性检查，防只改标签）
v1.19：render.py → v4.21、判断节点提示词 → v4.17、daily_ctx.py → v1.2（10-30 核定：2a 口径、R18 一档、规则档位判分、情景互补）
v1.20：render.py → v4.22（阈值说明「已触发/达成」的否定词识别放宽，修「不能确认达成」误报）
v1.21：render.py → v4.23（否定词按同一小句判断，修「不认定昨日T10达成」误报）
v1.22：render.py → v4.24、判断节点提示词 → v4.18、daily_ctx.py → v1.3（11-02 核定：居前＝申万前3、路径A核对昨日排名、R31 状态、观察类结果、档位押注一致）
v1.23：render.py → v4.25、daily_ctx.py → v1.4（大盘情景 cond 里的 {T5}/{T7} 换成数字后再判定、state 存数字）
v1.24：render.py → v4.26、判断节点提示词 → v4.19、daily_ctx.py → v1.5（wrong_if 两组带撤回、window_events、钱进价没涨扫描）
v1.25：render.py → v4.27（11-03 核定：体系常量方向锁定、T11/T13 互斥、情景须含阈值编号、window_events 须有带日期日程）
v1.37：判断节点提示词 → v4.30（11-21 复评：大盘概率基线、档位名单覆盖、切换预警只记录；render/daily_ctx 不变）
v1.36：render.py → v4.35、判断节点提示词 → v4.29（隔夜读数沿用昨日检查；R31 期间 T9/T10、wrong_if 不得绑科技）
v1.35：render.py → v4.34、判断节点提示词 → v4.28（T11/T17「收跌」无负号判负；10Y 两位不补零）
v1.34：render.py → v4.33、判断节点提示词 → v4.27（回填资金优先、s11 结项不携带）
v1.33：daily_ctx.py → v1.8（T9 回填提示按定位区分、情景 cond 无法解析单独提示）
v1.32：render.py → v4.32、判断节点提示词 → v4.26（R30 先例跳过回避线、情景 cond 认中文写法、T9 回填提示按定位）
v1.31：render.py → v4.31、判断节点提示词 → v4.25（隔夜类已触发阈值次日照样观察）
v1.30：判断节点提示词 → v4.24，文件改名 Perplexity提示词_v4_判断节点.md（旧名仍认）
v1.29：判断节点提示词 → v4.23（11-06 用户核定：2a 延续口径、T13 美股企稳＝纳指费半都 ≤1%、下修不拿封板率当必要条件、外盘读数口径、持有期美国数据）
v1.28：render.py → v4.30、判断节点提示词 → v4.22（11-05 核定：路径 B 自动核对、T9/T10 绑主攻线、仓位数字 ≤ 今日 + 20pp、标题摘要与主攻线一致）
v1.27：render.py → v4.29、判断节点提示词 → v4.21、daily_ctx.py → v1.7（接任者提示、情景判分上限 B、隔夜读数核对、今日申万前 5）
v1.26：render.py → v4.28、判断节点提示词 → v4.20、daily_ctx.py → v1.6（作用域修正、退潮档候选、2b 口径、T9 回填、第 4 条机械判分、T17/情景 A 文字）
"""
import os, re, sys, hashlib
HERE = os.path.dirname(os.path.abspath(__file__))
# 显示名: (候选文件名, 期望版本, 期望字节数, sha256 前 10 位, 版本号正则)
PY_VER = r"__version__\s*=\s*'([^']+)'"
EXPECT = [
    ('render.py', ['render.py'], 'v4.35', 171462, '5413589909', r"RENDER_VERSION\s*=\s*'([^']+)'"),
    ('判断节点提示词', ['Perplexity提示词_v4_判断节点.md', 'Coze提示词v4判断节点.md', 'Coze提示词_v4_判断节点.md'], 'v4.30', 79618, '2a81e05a72', r"版本：(v[\d.]+)"),
    ('daily_ctx.py', ['daily_ctx.py'], 'v1.8', 20764, '2114b3deaa', PY_VER),
    ('daily_pull.py', ['daily_pull.py'], 'v2.6', 24270, '449f7b39f0', PY_VER),
    ('holding_watch.py', ['holding_watch.py'], 'v5.9', 24632, '799253f2b6', PY_VER),
    ('trade_gate.py', ['trade_gate.py'], 'v1.2', 4322, 'fbfdb6b389', PY_VER),
    ('fill_stock_table.py', ['fill_stock_table.py'], 'v1.2', 119732, 'a87584696d', PY_VER),
]
bad = 0
print('%-18s %9s  %-8s %-11s %s' % ('文件', '字节数', '版本', 'sha256前10', '结论'))
for label, names, ver, size, sha, pat in EXPECT:
    p = next((os.path.join(HERE, n) for n in names if os.path.exists(os.path.join(HERE, n))), None)
    if not p:
        print('%-18s %9s  %-8s %-11s %s' % (label, '—', '—', '—', '（本目录没有，跳过）'))
        continue
    b = open(p, 'rb').read()
    s = b.decode('utf-8', 'ignore')
    m = re.search(pat, s)
    got_ver = m.group(1) if m else '无标记'
    got_sha = hashlib.sha256(b).hexdigest()[:10]
    notes = []
    if got_ver != ver: notes.append('版本应为 %s' % ver)
    if len(b) != size: notes.append('大小应为 %d' % size)
    if got_sha != sha: notes.append('内容与最新版不同')
    ok = not notes
    bad += (not ok)
    print('%-18s %9d  %-8s %-11s %s' % (label, len(b), got_ver, got_sha, '✓ 与最新版一致' if ok else '✗ ' + '；'.join(notes)))
print()
print('全部一致 ✓' if bad == 0 else '有 %d 个文件不是最新版。请先删掉目录里的旧文件，再重新拷入（不要选「跳过」或「保留两者」）。' % bad)
sys.exit(0 if bad == 0 else 1)
