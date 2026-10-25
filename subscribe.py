# -*- coding: utf-8 -*-
"""
subscribe.py — TVBox 源全自动订阅管道
======================================
功能：抓取对方云加密配置链接 → TVBoxOS 官方算法解密 → 合并去重
      → 域名黑名单剔除确定死源 → 生成自己的 nzsm_merge.json + nzsm.txt
设计：低频随机延时 + TVBox 伪装请求头 + GitHub Actions 出口（隐身订阅）
用法：
  本地测试:  python3 subscribe.py --local
  Actions:   python3 subscribe.py            （读取 SUB_SOURCES 环境变量）
"""
import os
import sys
import json
import time
import base64
import random
import subprocess
from urllib.request import Request, urlopen

# ============================================================
# 一、订阅源配置（对方加密链接）
#    格式: 标签|链接
#    环境变量 SUB_SOURCES 优先级高于内置（Actions 里用环境变量注入）
# ============================================================
DEFAULT_SOURCES = [
    "XC|https://ghfast.top/https://raw.githubusercontent.com/yoursmile66/TVBox/main/XC.json",
    "宝盒|https://ghfast.top/https://raw.githubusercontent.com/guot55/yg/main/ygbox.json",
]

# 输出文件（相对当前目录；Actions 里即仓库根目录）
OUT_JSON = "nzsm_merge.json"
OUT_TXT = "nzsm.txt"

# 顶层 spider（自己的云 jar——指向全能引擎合并版 qist_spider.jar）
SPIDER = "https://ghfast.top/https://raw.githubusercontent.com/nzsm/tvbox-server/main/jar/qist_spider.jar"

# 基础池：保留本地手动维护的配置（如 202 源优化版），订阅只做增量
# 首次运行自动把现有 nzsm_merge.json 快照为 nzsm_merge_base.json
BASE_JSON = "nzsm_merge_base.json"

# 域名黑名单（确认整体消失的生态域名，不依赖探测、绝不误杀；探测到新死域名手动加）
DEAD_DOMAINS = [
    "ygbh.site",      # 4K强擎生态，域名整体消失
    "yggbh.site",
    "vabh.site",
]

# ============================================================
# 二、隐身抓取（TVBox 伪装 + 随机延时）
# ============================================================
def tvbox_headers(url):
    """伪装成一台 TVBox 在加载配置"""
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    return {
        "User-Agent": "okhttp/4.9.0",
        "Referer": "https://" + host + "/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
    }


def stealth_fetch(url, timeout=25):
    """抓取，带随机延时（0~900s，打破固定时间规律）"""
    delay = random.uniform(0, 900)
    if "--local" not in sys.argv:
        time.sleep(delay)          # Actions 里延时；本地测试跳过
    req = Request(url, headers=tvbox_headers(url))
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# ============================================================
# 三、TVBoxOS 官方解密（2423 格式，与 ApiConfig.FindResult 一致）
# ============================================================
def tvbox_decrypt(content):
    """解密 TVBox 云加密配置。返回解密文本；明文直接返回原内容"""
    try:
        content = content.strip()
        if content.startswith("{") or content.startswith("//"):
            return content          # 明文/带注释明文
        # 可能带 8位+** 前缀（图片 jar 型）——本管道主要为 2423 hex 格式
        content = content.replace("\\s+", "")
        if not content.startswith("2423"):
            raise ValueError("未知加密格式: " + content[:12])
        data_hex = content[content.index("2324") + 4: len(content) - 26]
        full = bytes.fromhex(content).decode("utf-8", errors="ignore").lower()
        k0 = full[full.index("$#") + 2: full.index("#$")]
        key = (k0 + "0" * 16)[:16]
        iv0 = full[-13:]
        iv = (iv0 + "0" * 16)[:16]
        from Crypto.Cipher import AES
        cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
        plain = cipher.decrypt(bytes.fromhex(data_hex))
        pad = plain[-1]
        if pad and pad < 16 and plain[-pad:] == bytes([pad]) * pad:
            plain = plain[:-pad]
        return plain.decode("utf-8", errors="ignore")
    except Exception as e:
        raise ValueError(f"解密失败: {e}")


def strip_comments(txt):
    """去掉 // 注释行后解析 JSON"""
    lines = [l for l in txt.split("\n") if not l.strip().startswith("//")]
    return json.loads("\n".join(lines))


# ============================================================
# 四、合并 + 域名黑名单剔除 + 生成
# ============================================================
def merge_sources(decoded_list, base_cfg=None):
    """订阅源 + 基础池 合并去重（按 key，订阅优先），统一剔除黑名单域名"""
    merged = {"spider": SPIDER, "sites": []}
    seen = set()
    pools = []
    for tag, cfg in decoded_list:
        pools.append((tag, cfg.get("sites", [])))
    if base_cfg:
        pools.append(("基础池", base_cfg.get("sites", [])))
    for tag, sites in pools:
        for s in sites:
            key = s.get("key") or s.get("name")
            if key in seen:
                continue
            seen.add(key)
            raw = json.dumps(s, ensure_ascii=False)
            if any(d in raw for d in DEAD_DOMAINS):
                continue
            merged["sites"].append(s)
    return merged


def load_base():
    """基础池：存在 base 文件用它；否则把现有 nzsm_merge.json 快照为 base"""
    if os.path.exists(BASE_JSON):
        try:
            return json.load(open(BASE_JSON, encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ 基础池读取失败({e})，跳过", flush=True)
            return None
    if os.path.exists(OUT_JSON):
        try:
            cfg = json.load(open(OUT_JSON, encoding="utf-8"))
            json.dump(cfg, open(BASE_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            print(f"📸 已把现有 {OUT_JSON}（{len(cfg.get('sites', []))} 源）快照为基础池 {BASE_JSON}",
                  flush=True)
            return cfg
        except Exception as e:
            print(f"⚠️ 快照失败({e})", flush=True)
    return None


def build_txt(cfg):
    """生成 TVBox txt 版（去掉 json 注释性字段，TVBox 也支持全量 JSON）"""
    return json.dumps(cfg, ensure_ascii=False)


def run():
    # 1. 读取订阅源
    env = os.environ.get("SUB_SOURCES", "")
    sources = []
    if env:
        for item in env.split(";"):
            if "|" in item:
                tag, url = item.split("|", 1)
                sources.append((tag.strip(), url.strip()))
    else:
        for item in DEFAULT_SOURCES:
            if "|" in item:
                tag, url = item.split("|", 1)
                sources.append((tag, url))
    if not sources:
        print("❌ 没有订阅源：请设置 SUB_SOURCES 环境变量或填 DEFAULT_SOURCES")
        sys.exit(1)

    # 2. 逐源抓取+解密
    decoded = []
    for tag, url in sources:
        print(f"[{tag}] 抓取 {url[:60]}...", flush=True)
        raw = stealth_fetch(url)
        print(f"[{tag}] 抓到 {len(raw)} 字符, 解密...", flush=True)
        txt = tvbox_decrypt(raw)
        cfg = strip_comments(txt)
        n = len(cfg.get("sites", []))
        decoded.append((tag, cfg))
        print(f"[{tag}] ✅ {n} 个源", flush=True)

    # 3. 合并（订阅源 + 基础池）
    base = load_base()
    merged = merge_sources(decoded, base)
    n_all = len(merged["sites"])
    print(f"合并后: {n_all} 个源（含基础池，已剔除黑名单域名源）", flush=True)

    # 4. 写文件
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(build_txt(merged))
    print(f"✅ 已生成 {OUT_JSON}（{os.path.getsize(OUT_JSON)}B） + {OUT_TXT}", flush=True)

    # 5. 报告
    spider_ok = "/main/jar/" in merged.get("spider", "")
    print(f"顶层 spider: {merged.get('spider')}", flush=True)
    print(f"spider 地址校验: {'✅ 含 main/jar/ 路径' if spider_ok else '⚠️ 检查地址'}",
          flush=True)
    print("完成", flush=True)


if __name__ == "__main__":
    run()
