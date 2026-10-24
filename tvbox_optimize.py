#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvbox_optimize.py — 云编译 TVBoxOS 优化注入脚本（适配 q215613905/TVBoxOS 官方源码）
用途：云编译拉取官方源码后、编译前运行，自动完成两项优化：
  1. DNS 优化：DoH 解析失败自动回退系统 DNS（根治"设置 DNS 后搜不到"）+ 选项扩充（114/OneDNS/Google/AdGuard/Quad9）
  2. 广告规则：每次编译自动拉取 AdGuard 最新规则，合并旧规则，注入 APK 内置默认拦截列表
用法：python3 tvbox_optimize.py <tvboxos源码目录>
"""
import re, sys, os, json, urllib.request

SRC_DIR = sys.argv[1] if len(sys.argv) > 1 else '.'
OKGO = os.path.join(SRC_DIR, 'app/src/main/java/com/github/tvbox/osc/util/OkGoHelper.java')
APICFG = os.path.join(SRC_DIR, 'app/src/main/java/com/github/tvbox/osc/api/ApiConfig.java')

# ---- 内置广告基础列表（APK 内置 56 + 线上配置旧 118，合并去重）----
BASE_ADS = [
    "mimg.0c1q0l.cn","www.googletagmanager.com","www.google-analytics.com","mc.usihnbcq.cn","mg.g1mm3d.cn",
    "mscs.svaeuzh.cn","cnzz.hhttm.top","tp.vinuxhome.com","cnzz.mmstat.com","www.baihuillq.com","s23.cnzz.com",
    "z3.cnzz.com","c.cnzz.com","stj.v1vo.top","z12.cnzz.com","img.mosflower.cn","tips.gamevvip.com","ehwe.yhdtns.com",
    "xdn.cqqc3.com","www.jixunkyy.cn","sp.chemacid.cn","hm.baidu.com","s9.cnzz.com","z6.cnzz.com","um.cavuc.com",
    "mav.mavuz.com","wofwk.aoidf3.com","z5.cnzz.com","xc.hubeijieshikj.cn","tj.tianwenhu.com","xg.gars57.cn",
    "k.jinxiuzhilv.com","cdn.bootcss.com","ppl.xunzhuo123.com","xomk.jiangjunmh.top","img.xunzhuo123.com",
    "z1.cnzz.com","s13.cnzz.com","xg.huataisangao.cn","z7.cnzz.com","z2.cnzz.com","s96.cnzz.com","q11.cnzz.com",
    "thy.dacedsfa.cn","xg.whsbpw.cn","s19.cnzz.com","z8.cnzz.com","s4.cnzz.com","f5w.as12df.top","ae01.alicdn.com",
    "www.92424.cn","k.wudejia.com","vivovip.mmszxc.top","qiu.xixiqiu.com","cdnjs.hnfenxun.com","cms.qdwght.com",
    "cnzz.mmstat.com","puma.uc.cn","cooltv.kangkangtu.com","i.sf1-1cdn.com","p3p.cdn.vicp.io","f10.baidu.com",
    "pos.baidu.com","cpro.baidu.com","cbjs.baidu.com","c.baiducontent.com","nsclick.baidu.com","dr.163.com",
    "a1.go2yd.com","c.mipcdn.com","dimg01.c-ctrip.com","df.tanx.com","e0.gm.net","gm.mmstat.com","gtms01.alicdn.com",
    "gtms02.alicdn.com","gtms03.alicdn.com","gw.alicdn.com","g.alicdn.com","tce.alicdn.com","log.mmstat.com",
    "acjs.aliyun.com","q9.qunarimg.com","m6.bav.baidu.com","nlog.bav.baidu.com","adm.10jqka.com.cn","adtss.10jqka.com.cn",
    "file.10jqka.com.cn","js.10jqka.com.cn","zzd.sm.cn","ad.3.cn","act.video.qq.com","btrace.qq.com","cpr.qq.com",
    "adsfile.qq.com","adsmind.qq.com","gdt.qq.com","qlogo2.qq.com","rcgi.video.qq.com","static.wetv.vip","wlog.mgtv.com",
    "saxn.mgtv.com","ivalid.tc.qq.com","pgdt.gtimg.cn","adsby.yahoo.com","a1.ads.youku.com","v3-dy.ixigua.com",
    "dm.toutiao.com","lf3-cdn-tos.bytescm.com","p3-sign.douyinpic.com","analytics.toutiao.com","pangolin-sdk-toutiao.com",
    "snssdk.com","pstatp.com","toutiao.com","daily.zhihu.com","alliance.seo.piaoyun.com","q.youku.com","ad.doubleclick.net",
    "doubleclick.net","googleadservices.com","googlesyndication.com","google-analytics.com","googletagservices.com",
    "partner.googleadservices.com","adservice.google.com","pagead2.googlesyndication.com","pagead.l.google.com",
    "csi.gstatic.com","ssl.google-analytics.com","www.googleadservices.com","adserver.adtechus.com","adtechus.com",
    "adsrvr.org","adnxs.com","adsafeprotected.com","serving-sys.com","agkn.com","turn.com","flashtalking.com",
    "adform.net","adition.com","adscale.de","zetainteractive.com","crwdcntrl.net","demdex.net","omtrdc.net",
    "rlcdn.com","pubmatic.com","rubiconproject.com","contextweb.com","openx.net","spotxchange.com","taboola.com",
    "tribalfusion.com","undertone.com","yieldmanager.com","zedo.com","criteo.com","krxd.net","moatads.com",
    "scorecardresearch.com","quantserve.com","bluekai.com","exelator.com","everesttech.net","mathtag.com",
    "media6degrees.com","doubleverify.com","eyeblaster.com","specificclick.net","sitemeter.com","statcounter.com",
    "histats.com","cnzz.com","51.la","t.cn","weibo.com","umeng.com","umengcloud.com","cn.miaozhen.com","miaozhen.com",
    "simba.taobao.com","mmstat.com","umeng.com.cn","cnzz.mmstat.com","dta.com.cn","mm.cn","tapd.cn","devicemap.mobile.qq.com",
]

def fetch(url, timeout=25):
    """下载文本，直连失败自动换 ghfast 代理"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')
    except Exception:
        prox = 'https://ghfast.top/' + url
        req = urllib.request.Request(prox, headers={'User-Agent': 'Mozilla/5.0'})
        return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')

ADGUARD_SOURCES = [
    ('adguard_cn', 'https://raw.githubusercontent.com/AdguardTeam/AdguardFilters/master/ChineseFilter/sections/adservers.txt'),
    ('adguard_mobile', 'https://raw.githubusercontent.com/AdguardTeam/AdguardFilters/master/MobileFilter/sections/adservers.txt'),
]

def pull_latest_ads():
    hosts = set(BASE_ADS)
    for name, url in ADGUARD_SOURCES:
        try:
            txt = fetch(url)
            n = 0
            for line in txt.splitlines():
                line = line.strip()
                if not line or line.startswith('!') or line.startswith('#'):
                    continue
                m = re.match(r'^\|\|([a-z0-9\-\.]+)\^', line)
                if m:
                    hosts.add(m.group(1).lower())
                    n += 1
            print(f'  [{name}] 行数={len(txt.splitlines())} 提取域名={n}')
        except Exception as e:
            print(f'  [{name}] 拉取失败: {e}')
    return sorted(hosts)

# ---------------- DNS 优化（适配官方 TVBoxOS 结构） ----------------
def patch_dns():
    with open(OKGO, encoding='utf-8') as f:
        src = f.read()
    changed = []

    # 1. 扩充内置 DNS 配置 JSON：原 3 个 → 8 个（国内优先，国外兜底可选）
    new_json = (
        '    private static final String dnsConfigJson = "["\n'
        '            + "{\\"name\\": \\"腾讯\\", \\"url\\": \\"https://doh.pub/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"阿里\\", \\"url\\": \\"https://dns.alidns.com/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"360\\", \\"url\\": \\"https://doh.360.cn/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"114\\", \\"url\\": \\"https://114.114.114.114/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"OneDNS\\", \\"url\\": \\"https://doh.onedns.net/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"Google\\", \\"url\\": \\"https://dns.google/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"AdGuard\\", \\"url\\": \\"https://dns.adguard.com/dns-query\\"},"\n'
        '            + "{\\"name\\": \\"Quad9\\", \\"url\\": \\"https://dns.quad9.net/dns-query\\"}"\n'
        '            + "]";'
    )
    dns_pattern = re.compile(r'private static final String dnsConfigJson = "\["(?:\s*\+\s*"(?:[^"\\]|\\.)*")*\s*\+\s*"\]";')
    if 'doh.onedns.net' not in src:
        if dns_pattern.search(src):
            src = dns_pattern.sub(lambda m: new_json, src)
            changed.append('dnsConfigJson 扩充为 8 项')
        else:
            raise SystemExit('[FAIL] 未找到官方 dnsConfigJson 结构，DNS 配置扩充失败')

    # 2. CustomDns.lookup 加 try-catch：DoH 失败自动回退系统 DNS
    lookup_pattern = re.compile(
        r'Dns dns = dnsOverHttps != null \? dnsOverHttps : Dns\.SYSTEM;\s+return\s+dns\.lookup\(hostname\);')
    if 'catch (Throwable e)' not in src or 'Dns.SYSTEM.lookup(hostname)' not in src:
        if lookup_pattern.search(src):
            new_lookup = ('Dns dns = dnsOverHttps != null ? dnsOverHttps : Dns.SYSTEM;\n'
                          '                try {\n'
                          '                    return dns.lookup(hostname);\n'
                          '                } catch (Throwable e) {\n'
                          '                    return Dns.SYSTEM.lookup(hostname);\n'
                          '                }')
            src = lookup_pattern.sub(lambda m: new_lookup, src)
            changed.append('CustomDns.lookup 加失败回退')
        else:
            raise SystemExit('[FAIL] 未找到官方 CustomDns.lookup 结构，回退注入失败')

    with open(OKGO, 'w', encoding='utf-8') as f:
        f.write(src)

    # 验证
    if '114.114.114.114' not in src or 'doh.onedns.net' not in src or 'catch (Throwable e)' not in src:
        raise SystemExit('[FAIL] DNS 注入后验证未通过')
    print('DNS 优化完成:', changed)

# ---------------- 广告规则注入 ----------------
def patch_ads():
    with open(APICFG, encoding='utf-8') as f:
        src = f.read()
    ads = pull_latest_ads()
    print(f'  合并后广告域名总数: {len(ads)}')
    inner = ','.join('\\"%s\\"' % h for h in ads)
    new_block = '\\"ads\\":[' + inner + ']'
    pattern = re.compile(r'\\"ads\\":\[[^\]]*\]')
    m = pattern.search(src)
    if not m:
        raise SystemExit('[FAIL] 未找到内置 ads 数组（ApiConfig 结构变化），广告注入失败')
    src = src.replace(m.group(0), new_block)
    with open(APICFG, 'w', encoding='utf-8') as f:
        f.write(src)
    print(f'  已注入 {len(ads)} 条内置广告规则 → ApiConfig.java')
    return True

if __name__ == '__main__':
    if not os.path.exists(OKGO):
        print(f'[FAIL] 未找到 OkGoHelper.java: {OKGO}')
        sys.exit(1)
    print('== 1/2 DNS 优化 ==')
    patch_dns()
    print('== 2/2 广告规则注入 ==')
    if os.path.exists(APICFG):
        patch_ads()
    else:
        print('[SKIP] ApiConfig.java 不存在，跳过广告注入（DNS 已注入）')
    print('== 完成 ==')
#（注：内容由AI生成）
