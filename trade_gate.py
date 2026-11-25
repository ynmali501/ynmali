# -*- coding: utf-8 -*-
"""
trade_gate.py —— A 股交易日门控
用法：
  python3 trade_gate.py --mode close   # 收盘后任务（15:45 之后）：日历 + 校验今日行情是否已生成
  python3 trade_gate.py --mode pre     # 盘前任务（7:30）：只看日历（此时还没有今日行情）
输出只有一行：
  TRADE 2026-09-22                    → 交易日，继续执行
  SKIP  2026-09-25 中秋节休市           → 非交易日，任务应立即结束
仅依赖 Python 标准库。
"""
__version__ = 'v1.2'          # 每次改动递增；查看：python3 trade_gate.py --version
import sys, json, urllib.request
from datetime import datetime, timedelta, timezone

BJ = timezone(timedelta(hours=8))

# 2026 年沪深交易所休市安排（上证公告〔2025〕45号），只列落在周一至周五的休市日
# 注意：9/20（周日）、10/10（周六）为调休补班日，但交易所休市——周末判断已覆盖
HOLIDAYS = {
    2026: {
        '2026-01-01': '元旦', '2026-01-02': '元旦',
        '2026-02-16': '春节', '2026-02-17': '春节', '2026-02-18': '春节',
        '2026-02-19': '春节', '2026-02-20': '春节', '2026-02-23': '春节',
        '2026-04-06': '清明节',
        '2026-05-01': '劳动节', '2026-05-04': '劳动节', '2026-05-05': '劳动节',
        '2026-06-19': '端午节',
        '2026-09-25': '中秋节',
        '2026-10-01': '国庆节', '2026-10-02': '国庆节', '2026-10-05': '国庆节',
        '2026-10-06': '国庆节', '2026-10-07': '国庆节',
    },
}


def calendar_check(d):
    ds = d.strftime('%Y-%m-%d')
    if d.weekday() >= 5:
        return False, '周末休市'
    tbl = HOLIDAYS.get(d.year)
    if tbl is None:
        # 未收录年份：按工作日放行，但提醒更新（每年 12 月下旬交易所发布次年安排）
        return True, '⚠ 未收录 %d 年休市表，请更新 trade_gate.py' % d.year
    if ds in tbl:
        return False, tbl[ds] + '休市'
    return True, ''


def latest_bar_date():
    """上证指数最新一根日 K 的日期。腾讯优先（东财 push2his 在部分网络被阻断），失败回退东财；都失败返回 None"""
    import urllib.error
    srcs = [
        ('腾讯', 'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get?param=sh000001,day,,,2,qfq'),
        ('腾讯备', 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,2,qfq'),
        ('东财', 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
                 '?secid=1.000001&klt=101&fqt=0&lmt=1&end=20500101&fields1=f1&fields2=f51'),
    ]
    for name, url in srcs:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
            with urllib.request.urlopen(req, timeout=8) as r:
                j = json.loads(r.read().decode('utf-8'))
            if '腾讯' in name:
                d = (j.get('data') or {}).get('sh000001') or {}
                rows = d.get('qfqday') or d.get('day') or []
                if rows:
                    return rows[-1][0]
            else:
                ks = (j.get('data') or {}).get('klines') or []
                if ks:
                    return ks[-1].split(',')[0]
        except Exception:
            continue
    return None


def main():
    if '--version' in sys.argv:
        print('trade_gate', __version__); return
    mode = 'close'
    if '--mode' in sys.argv:
        mode = sys.argv[sys.argv.index('--mode') + 1]
    d = datetime.now(BJ)
    if '--date' in sys.argv:          # 测试用：--date 2026-09-25
        d = datetime.strptime(sys.argv[sys.argv.index('--date') + 1], '%Y-%m-%d').replace(tzinfo=BJ)
    ds = d.strftime('%Y-%m-%d')

    ok, why = calendar_check(d)
    if not ok:
        print('SKIP  %s %s' % (ds, why)); return

    if mode == 'close' and '--date' not in sys.argv:
        bar = latest_bar_date()
        if bar is None:
            print('TRADE %s（行情校验失败，按日历放行）%s' % (ds, why)); return
        if bar != ds:
            print('SKIP  %s 今日无行情（最新K线 %s，可能临时停市或数据未更新）' % (ds, bar)); return

    print(('TRADE %s %s' % (ds, why)).rstrip())


if __name__ == '__main__':
    main()
