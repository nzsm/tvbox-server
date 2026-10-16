#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端自动同步脚本（GitHub Actions 定时执行）
==========================================
功能：
  1. 拉取三个影视源配置：XC（yoursmile66，2423 加密）、宝盒（guot55，2423 加密）、qist（明文）
  2. 加密源自动解密（TVBox AES-CBC 2423 格式）
  3. 下载两个外部 jar（Yoursmile.jar / baohe_spider.jar）到本仓库 jar/ 目录，自托管
  4. 合并三源生成 nzsm.txt（保留各源 jar、全量去重、ghfast→gh-proxy 修复、去 md5 后缀）
  5. 配置里 jar 地址全部改写成指向本仓库（gh-proxy 前缀），对方删库不影响
  6. 兜底：任何源拉取失败 → 用仓库里已有的旧配置/旧 jar 继续，绝不中断报错

无需任何密钥：GitHub Actions 自带 GITHUB_TOKEN，自动提交推送。
运行环境：python3 + pycryptodome（工作流里已安装）。
"""
import json
import os
import re
import sys
import hashlib
import zipfile
import urllib.request

# ============ 配置区（按需修改）============
REPO_OWNER = "nzsm"            # 你的 GitHub 用户名
REPO_NAME = "tvbox-server"     # 你的仓库名
BRANCH = "main"                # 分支名（仓库默认分支，若 master 改成 master）
# 自托管 jar 的公开地址前缀（gh-proxy 代理 raw.githubusercontent.com）
SELF_PREFIX = f"https://gh-proxy.com/https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/jar/"

SOURCES = [
    {"name": "XC",   "url": "https://ghfast.top/https://raw.githubusercontent.com/yoursmile66/TVBox/main/XC.json",  "enc": True,  "repo": "https://raw.githubusercontent.com/yoursmile66/TVBox/main/"},
    {"name": "宝盒",  "url": "https://ghfast.top/https://raw.githubusercontent.com/guot55/yg/main/ygbox.json",       "enc": True,  "repo": "https://raw.githubusercontent.com/guot55/yg/main/"},
    {"name": "qist", "url": "https://raw.githubusercontent.com/qist/tvbox/master/jsm.json",                           "enc": False, "repo": "https://raw.githubusercontent.com/qist/tvbox/master/"},
]

# 外部 jar → 本仓库 jar 文件名（下载自托管 + 配置改写都靠这张表）
JAR_MAP = {
    "https://jihulab.com/yoursmile77/TVBox/-/raw/master/Yoursmile.jar": "jar/Yoursmile.jar",
    "https://gitee.com/baohe2025/bh/raw/master/spider.jar":             "jar/baohe_spider.jar",
    "https://raw.githubusercontent.com/qist/tvbox/master/jar/spider.jar": "jar/qist_spider.jar",
}
# qist 配置里用相对路径 ./jar/spider.jar 指自己的 jar（不带域名），单独映射
REL_JAR_MAP = {
    "./jar/spider.jar": "jar/qist_spider.jar",
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
OUT_FILE = "nzsm.txt"   # 生成的配置文件名（你订阅的路径就是它）
# ===========================================

def http_get(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def right_pad16(s):
    s = s[:16] if len(s) >= 16 else s + "0" * (16 - len(s))
    return s

def parse_config(text):
    """解析 TVBox 配置文本：剥离 BOM 和前导 // 注释行（XC 明文带作者注释头）。"""
    text = text.lstrip("\ufeff")
    lines = text.splitlines()
    keep = []
    for ln in lines:
        if ln.strip().startswith("//") and not keep:
            continue
        keep.append(ln)
    return json.loads("\n".join(keep))

def decrypt_2423(text):
    """TVBox 2423 加密配置解密（AES-CBC）。失败抛异常由调用方兜底。"""
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad
    t = "".join(text.split())
    if not t.startswith("2423"):
        raise ValueError("不是 2423 加密配置")
    idx = t.index("2324")
    data_hex = t[idx + 4 : -26]
    raw = bytes.fromhex(t)
    s = raw.decode("utf-8", errors="replace").lower()
    k_start = s.index("$#") + 2
    k_end = s.index("#$")
    key = right_pad16(s[k_start:k_end])
    iv = right_pad16(s[-13:])
    pt = unpad(AES.new(key.encode(), AES.MODE_CBC, iv.encode()).decrypt(bytes.fromhex(data_hex)), 16)
    return parse_config(pt.decode("utf-8"))

def fetch_config(src):
    """拉配置；加密源自动解密；失败返回 None（不中断）。"""
    try:
        data = http_get(src["url"])
        if src["enc"]:
            cfg = decrypt_2423(data.decode("utf-8", errors="replace"))
        else:
            cfg = parse_config(data.decode("utf-8", errors="replace"))
        print(f"  ✓ {src['name']}: {len(cfg.get('sites', []))} 源")
        return cfg
    except Exception as e:
        print(f"  ✗ {src['name']} 拉取失败: {e}（用仓库旧版兜底）")
        return None

def fix_proxy(u):
    return u.replace("https://ghfast.top/https://", "https://gh-proxy.com/https://") if "ghfast.top" in u else u

def self_host_jar(u):
    """jar 地址改写为自托管地址；md5;后缀去掉；其余一律原样保留（含 png）。
    重要：TVBox 对带 jar 字段的 csp_ 源走"源jar→内置类回退"路径，
    清空 jar 会让源只能查顶层而挂掉（瓜子 Amns 实测教训）。"""
    if not u:
        return u
    u = str(u)
    base = u.split(";")[0].strip()          # 去掉 ;md5;xxx 后缀
    # 相对路径（qist 的 ./jar/spider.jar）：映射到自托管
    if base.startswith("./jar/"):
        for rel, local in REL_JAR_MAP.items():
            if base == rel or base.rstrip("/") == rel.rstrip("/"):
                return SELF_PREFIX + local.replace("jar/", "", 1)
        return base  # 未知相对路径：保留（通常走顶层，无害）
    # 已知外部 jar：自托管
    for src_url, local in JAR_MAP.items():
        if base == src_url or base.rstrip("/") == src_url.rstrip("/"):
            return SELF_PREFIX + local.replace("jar/", "", 1)
    return fix_proxy(u)                     # 未知 jar：至少修掉 ghfast 损坏代理

def download_rel_assets(cfgs_meta, merged):
    """自托管相对路径资源（drpy2 的 ./lib/、./js/ 文件）：
    收集合并配置里 api/ext 的相对路径 → 从来源仓库下载到本仓库同名路径。
    下载后相对路径不变（TVBox 按订阅地址解析到本仓库 lib/js/，即自托管成功）。
    """
    need = {}  # relpath -> repo 根
    for meta in cfgs_meta:
        cfg, repo = meta
        if cfg is None:
            continue
        for s in cfg.get("sites", []):
            for fld in ("api", "ext"):
                v = s.get(fld)
                if isinstance(v, str) and (v.startswith("./lib/") or v.startswith("./js/")):
                    need.setdefault(v.lstrip("./"), repo)
    for rel, repo in sorted(need.items()):
        local = rel
        if os.path.exists(local):
            continue
        url = repo + urllib.parse.quote(rel, safe="/")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
            if len(raw) < 50:
                raise ValueError("文件过小")
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, "wb") as f:
                f.write(raw)
            print(f"  ✓ 资源已自托管: {local} ({len(raw)}B)")
        except Exception as e:
            print(f"  ✗ 资源下载失败: {rel} -> {e}")


def download_jar_if_needed(local_path, src_url):
    """下载外部 jar 到本仓库 jar/；校验 zip+classes.dex；失败保留旧文件。"""
    if os.path.exists(local_path):
        print(f"  ✓ jar 已存在（跳过下载，如要强制更新删掉本地文件再跑）: {local_path}")
        return True
    try:
        raw = http_get(src_url, timeout=120)
        if len(raw) < 1000:
            raise ValueError(f"文件过小({len(raw)}B)，疑似代理损坏")
        z = zipfile.ZipFile(io_bytes(raw))
        names = z.namelist()
        if not any(n.startswith("classes") for n in names):
            raise ValueError("jar 内没有 classes.dex，不是有效 TVBox jar")
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(raw)
        print(f"  ✓ jar 已下载: {local_path} ({len(raw)}B)")
        return True
    except Exception as e:
        print(f"  ✗ jar 下载失败: {e}（保留旧文件兜底）")
        return os.path.exists(local_path)

class _BytesIO:
    pass

def io_bytes(b):
    import io
    return io.BytesIO(b)

def merge_configs(cfgs):
    """模拟 v4.34 合并逻辑：记录来源 spider → 无 jar 的 csp_ 源回填 → 去重 → jar 自托管改写。"""
    pool = []
    for cfg in cfgs:
        if cfg is None:
            continue
        src_jar = cfg.get("spider", "") or ""
        for s in cfg.get("sites", []):
            s2 = dict(s)
            if str(s2.get("api", "")).startswith("csp_") and not s2.get("jar"):
                s2["_srcJar"] = src_jar
            pool.append(s2)
    seen = set()
    sites = []
    for s in pool:
        key = (s.get("key"), s.get("api"))
        if key in seen:
            continue
        seen.add(key)
        if not s.get("jar") and s.get("_srcJar"):
            s["jar"] = s["_srcJar"]
        s.pop("_srcJar", None)
        if s.get("jar"):
            s["jar"] = self_host_jar(s["jar"])
        sites.append(s)
    # v4.36.2 Douban 类修复：baohe jar 的 Douban 走 frodo.douban.com 被豆瓣风控（HTTP 400 invalid_request_997），
    # csp_Douban 源无分类无内容；Yoursmile jar 的 Douban 走 m.douban.com（200 正常）。强制 Douban 类源用 Yoursmile，
    # 并修正 csp_DouBan 大小写（jar 内实际类是 Douban）。
    DOUBAN_JAR = "https://gh-proxy.com/https://raw.githubusercontent.com/nzsm/tvbox-server/main/jar/Yoursmile.jar"
    for s in sites:
        api = str(s.get("api", ""))
        if api in ("csp_Douban", "csp_DouBan"):
            s["jar"] = DOUBAN_JAR
            if api == "csp_DouBan":
                s["api"] = "csp_Douban"
    # 顶层 spider：取第一个有 spider 的配置（与 v4.34 一致），同样自托管改写
    spider = ""
    for cfg in cfgs:
        if cfg and cfg.get("spider"):
            spider = self_host_jar(cfg["spider"])
            break
    return {"spider": spider, "sites": sites}

def main():
    print("== 云端自动同步开始 ==")
    # 1) 拉三源（失败兜底用仓库旧版）
    cfgs = [fetch_config(s) for s in SOURCES]
    have = [c for c in cfgs if c]
    if not have:
        print("!! 三个源全部拉取失败。若仓库已有 nzsm.txt 则保留现状，不覆盖。")
        return 0 if os.path.exists(OUT_FILE) else 1
    # 2) 下载自托管 jar（失败保留旧文件；qist 相对路径 jar 从 qist 仓库拉）
    all_jars = dict(JAR_MAP)
    for rel, local in REL_JAR_MAP.items():
        all_jars.setdefault("https://raw.githubusercontent.com/qist/tvbox/master/jar/" + local.split("/")[-1], local)
    for src_url, local in all_jars.items():
        print(f"  jar 自托管: {src_url}")
        download_jar_if_needed(local, src_url)
    # 3) 自托管 drpy2 相对路径资源（lib/js 文件，来源仓库下载）
    cfgs_meta = [(c, SOURCES[i]["repo"]) for i, c in enumerate(cfgs)]
    download_rel_assets(cfgs_meta, None)
    # 4) 合并
    merged = merge_configs(cfgs)
    # 5) 写 nzsm.txt
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    # 6) 统计
    jar_cnt = sum(1 for s in merged["sites"] if s.get("jar"))
    own_cnt = sum(1 for s in merged["sites"] if s.get("jar") and SELF_PREFIX.rstrip("/") in s["jar"])
    ghfast_left = sum(1 for s in merged["sites"] if s.get("jar") and "ghfast" in s["jar"])
    drpy_cnt = sum(1 for s in merged["sites"] if "./lib/" in str(s.get("api", "")))
    print(f"\n== 完成 ==")
    print(f"  nzms.txt 源数: {len(merged['sites'])}  |  有 jar: {jar_cnt}  |  自托管: {own_cnt}  |  ghfast 残留: {ghfast_left}")
    print(f"  drpy2 JS 源: {drpy_cnt}（lib/js 已自托管到本仓库）")
    print(f"  顶层 spider: {merged['spider']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
