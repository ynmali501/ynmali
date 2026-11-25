# -*- coding: utf-8 -*-
"""
daily_ctx.py —— A股复盘·次日推演 每日开工上下文（替代每天手写的触发语）
用法：python3 daily_ctx.py                 # 报告日期＝今天（北京时间）
      python3 daily_ctx.py --date 2026-10-16
只读不写；仅依赖标准库 + 同目录的 trade_gate.py（只用其休市表，不联网）。
输出一个文字块，最后一行是「结论：READY」或「结论：STOP（原因）」。STOP 时退出码为 2。

它替你算好每天会变的东西：
  ① 今天是不是交易日；② 版本核对（调用同目录 check_versions.py）；
  ③ 当日 market json 的来源、日期、字节数、指纹；
  ④ prev_state：找「date＝上一交易日 且 next＝今天」的那份 state（同一天多份或找不到即 STOP）；
  ⑤ 持有窗口（买入日、卖出日、报告日→买入日之间与窗口内的休市日）；
  ⑥ 昨日大盘情景（名称＋触发条件）与今天收盘读数，供回填 index_scenario_review；
  ⑦ 昨日风险日历里「携带到」今天及以后的事件；
  ⑧ v1.1【机械判定】：昨日 T1~T8 今天按收盘是否触发、大盘情景命中哪一种、核心观点该判几分
     （与 render.py ≥v4.19 同一套函数，Coze 照抄即可，不必自己推）。
  ⑫ v1.5：机械判定加「钱进价没涨」扫描（申万当日净流入>0、5 日净流入>0、5 日涨幅<0；需 render.py ≥ v4.26）。
  ⑭ v1.7：机械判定加「今日申万资金前 5」，第 1 名未过三项验证时按顺序找接任者；需 render.py ≥ v4.29。
  ⑮ v1.8：T9 回填提示按昨日定位区分（主攻/次线记失败，观察类只记中性）；大盘情景解析不了时写「cond 无法解析」，不再混称「无一满足」。需 render.py ≥ v4.32。
  ⑬ v1.6：机械判定加 §2 第 4 条判分（昨日最高概率情景兑现 B／其他已列 C）、2b 口径提示、昨日 T9 回填提示、R31 加 T11 互斥说明；需 render.py ≥ v4.28。
  ⑪ v1.4：昨日大盘情景 cond 若写成 {T5}/{T7}，按昨日阈值换成数字再判定（需 render.py ≥ v4.25）。
  ⑩ v1.3：机械判定加「昨日回避线今日名次」（居前＝申万资金前 3）、「昨日申万前 3」（供路径 A 核对）与 R31 状态（需 render.py ≥ v4.24）。
  ⑨ v1.2：机械判定加「规则档位」（矩阵落格 + 跌家>3000 下修正好一档）与昨日档位情景的判分（需 render.py ≥ v4.21）。
"""
__version__ = 'v1.8'          # 每次改动递增；查看：python3 daily_ctx.py --version
import sys, os, re, json, glob, hashlib, subprocess
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BJ = timezone(timedelta(hours=8))
WD = '一二三四五六日'


def fp(path):
    b = open(path, 'rb').read()
    return len(b), hashlib.sha256(b).hexdigest()[:10]


def load_gate():
    sys.path.insert(0, HERE)
    try:
        import trade_gate
        return trade_gate
    except Exception as e:
        return None


def is_td(gate, d):
    return gate.calendar_check(d)


def prev_td(gate, d, n=1):
    x = d
    while n:
        x -= timedelta(days=1)
        if is_td(gate, x)[0]:
            n -= 1
    return x


def next_td(gate, d):
    x = d + timedelta(days=1)
    while not is_td(gate, x)[0]:
        x += timedelta(days=1)
    return x


def closed_between(gate, a, b):
    """a、b 之间（不含两端）的休市日，返回 ['10-17 周末休市', ...]"""
    out, x = [], a + timedelta(days=1)
    while x < b:
        ok, why = is_td(gate, x)
        if not ok:
            out.append('%s（周%s·%s）' % (x.strftime('%m-%d'), WD[x.weekday()], why))
        x += timedelta(days=1)
    return out


def iter_text(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from iter_text(v)
    elif isinstance(o, list):
        for v in o:
            yield from iter_text(v)


def main():
    if '--version' in sys.argv:
        print('daily_ctx', __version__); return 0
    d = datetime.now(BJ)
    if '--date' in sys.argv:
        d = datetime.strptime(sys.argv[sys.argv.index('--date') + 1], '%Y-%m-%d').replace(tzinfo=BJ)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    ds, dk = d.strftime('%Y-%m-%d'), d.strftime('%Y%m%d')
    L, stop = [], []
    L.append('===== daily_ctx %s · 报告日期 %s（周%s）=====' % (__version__, ds, WD[d.weekday()]))

    # ① 交易日
    gate = load_gate()
    if gate is None:
        L.append('[交易日] ✗ 同目录缺 trade_gate.py，无法判断交易日与持有窗口')
        stop.append('缺 trade_gate.py')
    else:
        ok, why = is_td(gate, d)
        if not ok:
            L.append('[交易日] ✗ %s %s —— 今天不复盘' % (ds, why))
            stop.append('非交易日（%s）' % why)
        else:
            L.append('[交易日] ✓ %s%s' % (ds, ('　' + why) if why else ''))

    # ② 版本
    cv = os.path.join(HERE, 'check_versions.py')
    if os.path.exists(cv):
        try:
            r = subprocess.run([sys.executable, cv], cwd=HERE, capture_output=True, text=True, timeout=60)
            out = (r.stdout or '') + (r.stderr or '')
            keep = [x for x in out.splitlines() if x.startswith(('render.py', '判断节点提示词', 'daily_ctx.py'))
                    or '全部一致' in x or '不一致' in x]
            L.append('[版本] ' + ('｜'.join(x.strip() for x in keep) if keep else out.strip()[:300]))
            if any('✗' in x for x in keep) or '不一致' in out:
                stop.append('check_versions 有 ✗')
        except Exception as e:
            L.append('[版本] ✗ check_versions.py 运行失败：%s' % e); stop.append('check_versions 运行失败')
    else:
        L.append('[版本] ✗ 同目录缺 check_versions.py'); stop.append('缺 check_versions.py')

    # ③ 当日 market json
    mpath = os.path.join(HERE, 'market_%s.json' % dk)
    m = None
    if not os.path.exists(mpath):
        L.append('[数据] ✗ 缺 market_%s.json' % dk); stop.append('缺当日 market json')
    else:
        n, h = fp(mpath)
        try:
            m = json.load(open(mpath, encoding='utf-8'))
        except Exception as e:
            L.append('[数据] ✗ market_%s.json 读取失败：%s' % (dk, e)); stop.append('market json 读取失败')
        if m is not None:
            gb, dt, ok_, tv = m.get('_generated_by'), m.get('date'), m.get('_ok'), m.get('_tool_version')
            bad = []
            if gb != 'browser_pull.html': bad.append('_generated_by=%s' % gb)
            if str(dt) != ds: bad.append('date=%s' % dt)
            if ok_ is False: bad.append('_ok=false')
            L.append('[数据] %s market_%s.json｜%d 字节｜sha %s｜%s｜%s｜date %s｜_ok %s｜_generated_at %s' % (
                '✓' if not bad else '✗', dk, n, h, tv, gb, dt, ok_, m.get('_generated_at')))
            if bad:
                stop.append('market json 不合格：' + '、'.join(bad))

    # ④ prev_state
    prev = None
    if gate is not None and not any(s.startswith('非交易日') for s in stop):
        pd = prev_td(gate, d)
        pds, pdk = pd.strftime('%Y-%m-%d'), pd.strftime('%Y%m%d')
        hits = []
        for f in sorted(glob.glob(os.path.join(HERE, 'state_v4_*.json'))):
            try:
                s = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            if str(s.get('date')) == pds and str(s.get('next')) == ds:
                hits.append((f, s))
        exact = [x for x in hits if os.path.basename(x[0]) == 'state_v4_%s.json' % pdk]
        if exact:
            prev = exact[0]
        elif len(hits) == 1 or (hits and len({fp(x[0])[1] for x in hits}) == 1):
            prev = hits[0]          # 只有一份，或几份内容逐字节相同
        if prev:
            n, h = fp(prev[0])
            L.append('[prev_state] ✓ %s｜%d 字节｜sha %s｜date %s → next %s｜档位 gear_index=%s 仓位≤%s｜主线 %s' % (
                os.path.basename(prev[0]), n, h, pds, ds, prev[1].get('gear_index'),
                prev[1].get('position_max'), prev[1].get('mainline')))
            if len(hits) > 1:
                L.append('　　另有 %d 份内容日期相同的 state（%s），以标准文件名为准' % (
                    len(hits) - 1, '、'.join(os.path.basename(x[0]) for x in hits if x is not prev)))
        else:
            if hits:
                L.append('[prev_state] ✗ date=%s 且 next=%s 的 state 有 %d 份且内容不同，无法确定用哪份：%s' % (
                    pds, ds, len(hits), '、'.join(os.path.basename(x[0]) for x in hits)))
            else:
                L.append('[prev_state] ✗ 找不到 date=%s 且 next=%s 的 state' % (pds, ds))
            stop.append('prev_state 不唯一或缺失')

    # ⑤ 持有窗口
    if gate is not None and not any(s.startswith('非交易日') for s in stop):
        buy = next_td(gate, d); sell = next_td(gate, buy)
        pre = closed_between(gate, d, buy); mid = closed_between(gate, buy, sell)
        L.append('[持有窗口] 买入 %s（周%s）→ 卖出 %s（周%s）｜报告日到买入日休市：%s｜窗口内休市：%s' % (
            buy.strftime('%Y-%m-%d'), WD[buy.weekday()], sell.strftime('%Y-%m-%d'), WD[sell.weekday()],
            '、'.join(pre) or '无', '、'.join(mid) or '无'))
        if pre or mid:
            L.append('　　→ 有休市夹在中间：外盘「两头都有消息」，须在 gear.basis 写明是否因此降档')

    # ⑥ 昨日情景 + 今日读数
    if prev:
        isc = ((prev[1].get('forecast') or {}).get('index_scenarios')) or []
        if isc:
            L.append('[昨日情景] ' + '｜'.join('%s %s%%：%s' % (x.get('name'), x.get('prob'), x.get('cond')) for x in isc))
        else:
            L.append('[昨日情景] 昨日 state 无 index_scenarios → 今天不填 index_scenario_review')
    if m is not None:
        sh = (m.get('indices') or {}).get('上证指数') or m.get('sh_index') or {}
        br = m.get('breadth') or {}
        try:
            chg = '%+.2f%%' % (float(sh.get('chg')) * 100)
        except Exception:
            chg = '?'
        L.append('[今日读数] 上证收 %s（%s，高 %s 低 %s）｜涨 %s 跌 %s 平 %s｜涨停 %s 封板率 %s｜晋级率 %s' % (
            sh.get('close'), chg, sh.get('high'), sh.get('low'), br.get('up_count'), br.get('down_count'),
            br.get('flat_count'), (m.get('limit') or {}).get('limitup'), (m.get('limit') or {}).get('sealrate'),
            (m.get('limit') or {}).get('promote_rate')))

    # ⑦ 风险日历携带
    if prev and gate is not None:
        pdk = datetime.strptime(str(prev[1].get('date')), '%Y-%m-%d').strftime('%Y%m%d')
        rp = os.path.join(HERE, 'report_v4_%s.json' % pdk)
        if os.path.exists(rp):
            try:
                rj = json.load(open(rp, encoding='utf-8'))
                items = []
                for t in iter_text(rj.get('s11')):
                    for seg in re.split(r'[；;\n]', t):
                        mm = re.search(r'携带到\s*(\d{1,2})-(\d{1,2})', seg)
                        if mm:
                            due = datetime(d.year, int(mm.group(1)), int(mm.group(2)), tzinfo=BJ)
                            if due >= d:
                                items.append(seg.strip()[:90])
                        elif '持续携带' in seg:
                            items.append(seg.strip()[:90])
                L.append('[风险携带] ' + ('｜'.join(dict.fromkeys(items)) if items else '昨日 s11 无仍在期内的「携带到」事件'))
            except Exception as e:
                L.append('[风险携带] 读取 %s 失败：%s' % (os.path.basename(rp), e))
        else:
            L.append('[风险携带] 缺 report_v4_%s.json，跳过' % pdk)

    # ⑧ 机械判定（v1.1）
    if prev and m is not None:
        try:
            sys.path.insert(0, HERE)
            import render as _rd
            sh = (m.get('indices') or {}).get('上证指数') or m.get('sh_index') or {}
            br = m.get('breadth') or {}
            lm = m.get('limit') or {}
            close = float(sh['close']) if sh.get('close') is not None else None
            chg = float(sh['chg']) if sh.get('chg') is not None else None
            up = br.get('up_count')
            mk = {'limitup': lm.get('limitup'), 'sealrate': lm.get('sealrate')}
            pth = {t['id']: t for t in prev[1].get('thresholds', [])}
            fx, why = _rd.mech_fired(pth, close, mk)
            L.append('[机械判定] fired 里 T1~T8 应为：%s（%s）——再加上当日真实触发的 T9~T17；T8「放量」需对照成交额是否放大' % (
                json.dumps(sorted(fx, key=lambda x: int(x[1:])), ensure_ascii=False), '；'.join(why) or '均未触发'))
            isc = ((prev[1].get('forecast') or {}).get('index_scenarios')) or []
            _ev = [(x.get('name'), (_rd.eval_cond(x.get('cond'), close, up, pth) if 'th' in _rd.eval_cond.__code__.co_varnames else _rd.eval_cond(x.get('cond'), close, up))) for x in isc]   # v1.4：cond 里的 {T7} 换成昨日数字
            hits = [n_ for n_, v_ in _ev if v_]
            _unp = [str(n_) for n_, v_ in _ev if v_ is None]   # v1.8
            if isc:
                L.append('　　大盘情景命中：%s' % (('「%s」→ index_scenario_review.hit 照抄' % hits[0]) if len(hits) == 1
                                              else '无法唯一判定（%s），请人工按 cond 核对' % ('、'.join(map(str, hits)) or ('cond 无法解析：' + '、'.join(_unp) if _unp else '无一满足'))))
                if len(hits) == 1:   # v1.6：第 4 条机械判分
                    _ip = [float(x.get('prob') or 0) for x in isc]; _in = [x.get('name') for x in isc]
                    _hp = _ip[_in.index(hits[0])]
                    L.append('　　指数关口：昨日最高概率「%s」%g%%，实际「%s」%g%% → §2 第4条判 %s' % (
                        _in[_ip.index(max(_ip))], max(_ip), hits[0], _hp, 'B' if _hp >= max(_ip) else 'C'))
            cg = _rd.core_grade((prev[1].get('forecast') or {}).get('core'), chg, up)
            L.append('　　核心观点：' + (('昨日押「%s」，实际「%s」→ §2 第1条判 %s' % (cg['pred'], cg['actual'], cg['grade'])) if cg
                                      else '昨日 core 第一句不是「收涨/收跌/收平；上涨家数≥/<N」格式，按旧方法判'))
            if hasattr(_rd, 'mech_gear') and lm.get('limitup') is not None and lm.get('sealrate') is not None:   # v1.2
                mg, mcell, mv = _rd.mech_gear(prev[1].get('gear_index'), lm['limitup'], lm['sealrate'], br.get('down_count'))
                gsc = ((prev[1].get('forecast') or {}).get('gear_scenarios')) or []
                gl = ''
                if gsc and mg:
                    nm = [str(x.get('name')) for x in gsc]; pp = [float(x.get('prob') or 0) for x in gsc]
                    top = nm[pp.index(max(pp))]
                    gl = '；昨日押「%s」%g%% → §2 第3条判 %s' % (top, max(pp), 'B' if mg == top else ('C' if mg in nm else 'D'))
                L.append('　　规则档位：涨停 %s × 封板率 %.2f%% 落「%s」=%s，跌家 %s%s → 「%s」（gear.chosen 再往下须在 basis 引用 R22/R33 等）%s' % (
                    lm['limitup'], float(lm['sealrate']) * 100, mcell, mv, br.get('down_count'),
                    '>3000 下修一档' if (br.get('down_count') or 0) > 3000 else '≤3000 不下修', mg, gl))
            if hasattr(_rd, '_sw1_rank'):   # v1.3
                pds = str(prev[1].get('date', '')).replace('-', '')
                pmk = None
                try:
                    with open(os.path.join(HERE, 'market_%s.json' % pds), encoding='utf-8') as _f:
                        pmk = json.load(_f)
                except Exception:
                    pass
                if pmk:
                    _rows = sorted((pmk.get('sector_flow_sw1') or {}).get('rows') or [], key=lambda r: -(r.get('inflow_yi') or -1e9))[:3]
                    L.append('　　昨日（%s）申万资金前 3：%s——路径 A 只认这三个（另须今日第 1）' % (
                        prev[1].get('date'), '、'.join('%s %+.2f' % (r['sector'], r['inflow_yi']) for r in _rows)))
                else:
                    L.append('　　昨日 market_%s.json 不在目录，路径 A 无法核对（render 会给黄色提示）' % pds)
                _tr = sorted((m.get('sector_flow_sw1') or {}).get('rows') or [], key=lambda r: -(r.get('inflow_yi') or -1e9))[:5]   # v1.7
                if _tr:
                    L.append('　　今日申万资金前 5：%s——第 1 名三项验证有 ✗ 时按顺序找三项全过的接任主攻（今日退潮档除外），都不过才写无主攻线' % (
                        '、'.join('%d.%s %+.2f' % (i + 1, r['sector'], r['inflow_yi']) for i, r in enumerate(_tr))))
                hs = [h for h in (prev[1].get('history') or []) if h.get('date') == prev[1].get('date')]
                av = []
                for c0 in (hs[0].get('candidates') if hs else []):
                    if '回避' in re.split(r'[（(]', str(c0.get('role', '')))[0]:
                        rk, iv = _rd._sw1_rank(m, c0.get('name'))
                        if rk is not None:
                            av.append('%s 第%d（%+.2f）→ %s' % (c0.get('name'), rk, iv or 0, '失败' if (rk <= 3 and (iv or 0) > 0) else '中性'))
                if av:
                    L.append('　　昨日回避线今日结果（居前＝申万前 3 且净流入）：' + '；'.join(av))
                _GN = ['冰点', '修复', '发酵', '高潮', '退潮1', '退潮2', '退潮3']   # v1.6：2b 口径看昨日档位
                _pg = prev[1].get('gear_index')
                _pgn = _GN[_pg] if isinstance(_pg, int) and 0 <= _pg < 7 else '?'
                L.append('　　2b 口径：昨日档位「%s」→ %s' % (_pgn, '退潮口径，只评回避清单' if '退潮' in _pgn else
                         '常规口径：昨日次线没兑现判 C（不适用「只评回避清单」）；今日若是退潮档，候选只列回避线＋至多 1 个防御观察'))
                _p9 = next((t for t in prev[1].get('thresholds', []) if t.get('id') == 'T9'), None)
                if _p9 and not re.search(r'停用|无主攻', str(_p9.get('val', ''))):
                    _rk9, _iv9 = _rd._sw1_rank(m, re.split(r'[（(\s]', str(_p9.get('val', '')))[0])
                    if _iv9 is not None:
                        _k9 = re.split(r'[（(\s·/]', str(_p9.get('val', '')))[0][:2]   # v1.8：按昨日对应候选的定位给回填口径
                        _r9 = next((re.split(r'[（(]', str(c0.get('role', '')))[0] for c0 in (prev[1].get('history') or [{}])[-1].get('candidates', [])
                                    if _k9 and _k9 in str(c0.get('name', ''))), '')
                        _o9 = '记中性（观察类不记失败）' if ('观察' in _r9 and '回避' not in _r9) else '记失败'
                        L.append('　　昨日 T9「%s」：该方向今日申万第 %s（%+.2f 亿）%s' % (_p9.get('val'), _rk9, _iv9,
                                 ('→ 已净流出，T9 应列进 fired，对应候选（%s）%s' % (_r9 or '定位未查到', _o9)) if (_iv9 < 0 and '净流出' in str(_p9.get('val'))) else '→ 未净流出，再看龙头是否断板'))
                if hasattr(_rd, 'quiet_accum'):   # v1.5
                    qa = _rd.quiet_accum(m)
                    L.append('　　钱进价没涨（今日）：%s' % ('、'.join('%s 当日%+.2f/5日%+.2f/5日%+.2f%%' % (r['sector'], r['inflow_yi'], r['inflow_5d_yi'], r['pct_5d']) for r in qa[:5])
                                                       or '无') + '——前 3 个要么进候选、要么在 excluded 写不列理由')
                L.append('　　R31：%s' % ('生效中——电子/半导体/PCB/通信 role 只能写回避线，今日 T13 达成才解除；纳指跌>1%（T11 触发）时 T13 不得判达成'
                                          if prev[1].get('r31_active', True) else '已解除（10Y 收盘重新破 5% 时报告写 r31_reactivate:true）'))
        except Exception as e:
            L.append('[机械判定] 跳过（需同目录 render.py ≥ v4.19）：%s' % e)

    L.append('结论：' + ('READY' if not stop else 'STOP（%s）' % '；'.join(stop)))
    print('\n'.join(L))
    return 0 if not stop else 2


if __name__ == '__main__':
    sys.exit(main())
