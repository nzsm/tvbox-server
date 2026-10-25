#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# merge_engine.py —— 全能引擎 jar 云编译合并脚本（双产物版）
# 一次运行产出两个 jar：
#   1) jar/all_qist_spider.jar  全量版：把能拿到的所有源 jar 合并、类级去重，
#      保留全部类与原生库（不论文件多大），作为"防删库"兜底。
#   2) jar/qist_spider.jar      完美版：依赖闭包裁剪——只保留"配置实际引用的
#      csp 爬虫类 + 它们的全部依赖链"，既完整又体积可控。
#
# 特性：
#   * 自动发现：扫描仓库配置里每个源引用的 jar（"jar"/"spider" 字段），
#     自动加入合并源（含 flaky 域名的容错跳过），提高覆盖率。
#   * 类级去重：多 jar 里重复的类只保留最高优先级那份。
#   * dex 64K 上限：按方法数分组（每组 < 22000），用 smali 重汇编。
#   * 最小内存：所有 java 步骤用 -Xmx<JAVA_MEM>（默认 2g），不堆内存。
#
# 用法：python3 merge_engine.py
#   MODE=both|all|closure   生成哪些（默认 both）
#   JAVA_MEM=2g             内存上限（默认 2g）
#   DEX_METHOD_TARGET=22000 每个 dex 方法数上限
# ============================================================
import os, sys, subprocess, zipfile, shutil, hashlib, re

WORK = os.path.abspath(os.path.dirname(__file__))
TMP  = os.path.join(WORK, ".merge_tmp")
TOOL = os.path.join(WORK, ".merge_tools")
MODE = os.environ.get("MODE", "both")            # both | all | closure
JAVA_MEM = os.environ.get("JAVA_MEM", "2g")      # 最小内存
DEX_METHOD_TARGET = int(os.environ.get("DEX_METHOD_TARGET", "22000"))
PROTECT_PREFIXES = ("com/github/catvod/spider/", "com/github/catvod/crawler/Spider")
JAVA_XMX = f"-Xmx{JAVA_MEM}"

OUT_ALL     = os.path.join(WORK, "jar", "all_qist_spider.jar")
OUT_CLOSURE = os.path.join(WORK, "jar", "qist_spider.jar")

# ---------- 1) 工具链自举（Maven Central，可运行期下载；构建 fat jar） ----------
BAKSMALI_MAIN = "https://repo1.maven.org/maven2/org/smali/baksmali/2.5.2/baksmali-2.5.2.jar"
SMALI_MAIN    = "https://repo1.maven.org/maven2/org/smali/smali/2.5.2/smali-2.5.2.jar"
DEPS = [
    "https://repo1.maven.org/maven2/org/smali/dexlib2/2.5.2/dexlib2-2.5.2.jar",
    "https://repo1.maven.org/maven2/com/google/guava/guava/27.1-android/guava-27.1-android.jar",
    "https://repo1.maven.org/maven2/com/google/code/gson/gson/2.8.5/gson-2.8.5.jar",
    "https://repo1.maven.org/maven2/commons-cli/commons-cli/1.4/commons-cli-1.4.jar",
    "https://repo1.maven.org/maven2/com/beust/jcommander/1.64/jcommander-1.64.jar",
    "https://repo1.maven.org/maven2/org/antlr/antlr-runtime/3.5.2/antlr-runtime-3.5.2.jar",
    "https://repo1.maven.org/maven2/org/antlr/antlr/3.5.2/antlr-3.5.2.jar",
]

def http_get(url, timeout=120):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; merge_engine)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def download(url, dest, min_size=1000):
    try:
        data = http_get(url)
        if len(data) >= min_size:
            open(dest, "wb").write(data)
            return True
        print(f"  ⚠ 数据过小 {len(data)}B: {url}")
    except Exception as e:
        print(f"  ⚠ 下载失败: {os.path.basename(url)} ({type(e).__name__})")
    return False

def fat_jar(out, main_url, main_class):
    """从 Maven Central 下载主 jar + 依赖，合并成可直接 java -jar 的 fat jar。"""
    os.makedirs(TOOL, exist_ok=True)
    jar_dir = os.path.join(TOOL, "jars"); os.makedirs(jar_dir, exist_ok=True)
    files = {"main.jar": main_url}
    for i, u in enumerate(DEPS):
        files[f"d{i}.jar"] = u
    local = {}
    for name, u in files.items():
        p = os.path.join(jar_dir, name)
        if not (os.path.exists(p) and os.path.getsize(p) > 1000):
            if not download(u, p):
                print(f"  ✗ 关键依赖 {name} 下载失败")
                return False
        local[name] = p
    merged = {}
    for name in local:
        with zipfile.ZipFile(local[name]) as z:
            for n in z.namelist():
                if n.startswith("META-INF/"):
                    continue
                if n not in merged:
                    merged[n] = z.read(n)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF",
                   b"Manifest-Version: 1.0\r\nMain-Class: " + main_class.encode() + b"\r\n\r\n")
        for n, d in merged.items():
            z.writestr(n, d)
    print(f"  ✓ 工具就绪 {os.path.basename(out)} ({len(merged)} 类)")
    return True

def ensure_tools():
    """优先用仓库内已提交的离线 fat 工具（直接 jar 或 .jar.zip）；其次缓存目录；最后 Maven。"""
    committed = lambda n: os.path.join(WORK, "tools", n)
    def unzip_if_needed(name):
        zp = committed(name + ".zip")
        if os.path.exists(zp) and os.path.getsize(zp) > 1000:
            import zipfile as _z
            with _z.ZipFile(zp) as z:
                member = z.namelist()[0]
                with z.open(member) as src, open(committed(name), "wb") as dst:
                    dst.write(src.read())
            print(f"  ✓ 解包离线工具 {name} from {name}.zip")
            return True
        return False
    def pick(name, maven_url, main_cls):
        # 1) 仓库已提交 tools/xxx.jar
        if os.path.exists(committed(name)) and os.path.getsize(committed(name)) > 1000:
            return committed(name)
        # 2) 仓库 tools/xxx.jar.zip -> 解包
        if unzip_if_needed(name):
            return committed(name)
        # 3) 缓存/上次构建的 .merge_tools
        cached = os.path.join(TOOL, name)
        if os.path.exists(cached) and os.path.getsize(cached) > 1000:
            return cached
        # 4) Maven 自举兜底
        if not fat_jar(cached, maven_url, main_cls):
            raise RuntimeError(f"{name} fat jar 构建失败")
        return cached
    bsm = pick("baksmali.jar", BAKSMALI_MAIN, "org.jf.baksmali.Main")
    sml = pick("smali.jar", SMALI_MAIN, "org.jf.smali.Main")
    return bsm, sml

# ---------- 2) 源 jar 清单：核心（高优先级）+ 配置自动发现（低优先级） ----------
CORE_SOURCES = [
    "https://gitee.com/baohe2025/bh/raw/master/spider.jar",
    "https://raw.githubusercontent.com/qist/tvbox/master/jar/spider.jar",
    "https://jihulab.com/yoursmile77/TVBox/-/raw/master/Yoursmile.jar",
    "https://ghfast.top/https://raw.githubusercontent.com/666tv/69/main/jar/pg.jar",
    "https://ghfast.top/https://raw.githubusercontent.com/666tv/69/main/jar/66tv.jar",
    "https://ghfast.top/https://raw.githubusercontent.com/gaotianliuyun/gao/master/jar/pg.jar",
    "https://ghfast.top/https://raw.githubusercontent.com/gaotianliuyun/gao/master/jar/XBPQ.jar",
    "https://ghfast.top/https://raw.githubusercontent.com/gaotianliuyun/gao/master/jar/XYQ.jar",
    "jar/spider_all_final.jar",
    "jar/baohe_spider.jar",
    "jar/Yoursmile.jar",
]

MIRROR_PREFIXES = [
    "https://ghfast.top/", "https://gh-proxy.com/", "https://mirror.ghproxy.com/",
    "https://ghproxy.net/", "https://github.moeyy.xyz/", "https://raw.kkgithub.com/",
    "https://raw.bgithub.xyz/", "https://ghproxy.com/", "https://mirror.ghproxy.net/",
]
def normalize_url(u):
    u = u.strip().split(";")[0].strip()
    for p in MIRROR_PREFIXES:
        if u.startswith(p):
            u = u[len(p):]
            break
    return u

def discover_extra_jars():
    """扫描仓库配置里的 "jar"/"spider" 字段，收集外部 .jar 源 URL。"""
    found = set()
    jar_re = re.compile(r'"(jar|spider)"\s*:\s*"([^"]+)"')
    for root, dirs, files in os.walk(WORK):
        dirs[:] = [d for d in dirs if d not in (".git", ".merge_tmp", ".merge_tools")]
        for f in files:
            if not f.endswith((".txt", ".json", ".m3u")):
                continue
            try:
                txt = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for m in jar_re.finditer(txt):
                val = m.group(2).strip()
                if not val or val.startswith("./") or val.startswith(".c/"):
                    continue
                norm = normalize_url(val)
                if norm.endswith(".jar") and ("://" in norm or "coding.net" in norm):
                    found.add(norm)
    return sorted(found)

# ---------- 3) 工具函数 ----------
def _count_smali(d):
    return sum(1 for _, _, fs in os.walk(d) for f in fs if f.endswith(".smali"))

def _methods_in(d):
    out = []
    for root, _, fs in os.walk(d):
        for f in fs:
            if f.endswith(".smali"):
                rel = os.path.relpath(os.path.join(root, f), d)
                m = sum(1 for ln in open(os.path.join(root, f), encoding="utf-8", errors="ignore")
                        if ln.lstrip().startswith(".method"))
                out.append((rel, m))
    return out

def is_asset(n):
    if n.startswith("META-INF/"):
        return False
    if n.startswith("classes") and n.endswith(".dex"):
        return False
    return True

# ---------- 4) 汇编一组类到 dex（含毒类重试，最小内存） ----------
def assemble_group(gdir, outdex, sml):
    for attempt in range(1, 7):
        r = subprocess.run([os.environ.get("JAVA", "java"), JAVA_XMX, "-jar", sml,
                            "assemble", "-o", outdex, gdir],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode == 0 and os.path.exists(outdex):
            return True
        err = r.stderr.decode("utf-8", "ignore")
        m = re.search(r"Lcom/[\w/$]+;", err)
        if m:
            poison = os.path.join(gdir, m.group(0).replace("L", "", 1).replace(";", ".smali"))
            if os.path.isfile(poison):
                print(f"  ⚠ 删除毒类 {m.group(0)}")
                os.remove(poison)
                continue
        print(f"  ✗ 汇编失败(第{attempt}轮): {err[-500:]}")
        break
    return False

def build_dex(rel_list, merged_smali, prefix, sml, tag):
    """把 rel_list 里的类按方法数分组并汇编为 dex，返回 dex 路径列表。"""
    methods = dict(_methods_in(merged_smali))
    files = [(rel, methods[rel]) for rel in rel_list if rel in methods]
    files.sort()
    groups, cur, cm = [], [], 0
    for rel, m in files:
        if cur and cm + m > DEX_METHOD_TARGET:
            groups.append(cur); cur = []; cm = 0
        cur.append((rel, m)); cm += m
    if cur:
        groups.append(cur)
    print(f"  {tag}: {len(files)} 类 / {sum(m for _, m in files)} 方法，分 {len(groups)} 组"
          f" {[sum(m for _, m in g) for g in groups]}")
    dex_files = []
    for gi, g in enumerate(groups, 1):
        gdir = os.path.join(TMP, f"{prefix}g{gi}")
        os.makedirs(gdir, exist_ok=True)
        for rel, _ in g:
            dst = os.path.join(gdir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(merged_smali, rel), dst)
        outdex = os.path.join(TMP, f"{prefix}classes{gi}.dex" if gi > 1 else f"{prefix}classes.dex")
        if assemble_group(gdir, outdex, sml):
            dex_files.append(outdex)
    return dex_files

def pack_jar(out, dex_files, assets, strip_so=False):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # dex 统一用标准命名 classes.dex / classes2.dex ...（TVBox/Android 只认这套）
        for i, df in enumerate(dex_files, 1):
            z.write(df, f"classes{i}.dex" if i > 1 else "classes.dex")
        for n, d in assets.items():
            if strip_so and (".so" in n.lower() or n.endswith(".so")):
                continue
            z.writestr(n, d)
    md5 = hashlib.md5(open(out, "rb").read()).hexdigest()
    print(f"  ✅ {os.path.basename(out)}: {os.path.getsize(out)/1048576:.2f}MB | "
          f"{len(dex_files)} dex | MD5 {md5}" + (" | 已去.so" if strip_so else ""))
    open(out + ".md5", "w").write(md5)

# ---------- 5) 主流程 ----------
def main():
    bsm, sml = ensure_tools()
    shutil.rmtree(TMP, ignore_errors=True)
    os.makedirs(TMP, exist_ok=True)

    extra = discover_extra_jars()
    cap = int(os.environ.get("DISCOVER_CAP", "50"))
    if 0 < cap < len(extra):
        print(f"配置自动发现外部 jar {len(extra)} 个，按 DISCOVER_CAP={cap} 截取（防止爆盘/拖慢）")
        extra = extra[:cap]
    else:
        print(f"配置自动发现外部 jar: {len(extra)} 个")
    sources = CORE_SOURCES + extra
    inputs = []          # [(priority, name, local_path)]
    n_core = len(CORE_SOURCES)
    for p, src in enumerate(sources):
        local = src if os.path.isfile(os.path.join(WORK, src)) else None
        if local:
            inputs.append((p, os.path.basename(src), os.path.join(WORK, src)))
            continue
        dest = os.path.join(TMP, f"in_{p}.jar")
        # 核心源给长超时，自动发现的给短超时（避免 flaky 域名拖慢整轮）
        to = 60 if p < n_core else 20
        try:
            data = http_get(src, timeout=to)
            if len(data) > 200 and data[:2] == b"PK":
                open(dest, "wb").write(data)
                inputs.append((p, os.path.basename(src).split("?")[0], dest))
                print(f"  ✓ [{p}] {os.path.basename(src).split('?')[0][-40:]} ({len(data)//1024}KB)")
            else:
                print(f"  ⚠ 跳过（非有效zip）[{p}] {src[:60]}")
        except Exception as e:
            print(f"  ⚠ 跳过 [{p}] {src[:60]} ({type(e).__name__})")
    print(f"实际参与合并 {len(inputs)} 个 jar")

    per_source = []
    assets = {}
    total_classes_all = 0
    for p, name, jar in inputs:
        outd = os.path.join(TMP, f"smali_{p}")
        if not os.path.isdir(outd):
            r = subprocess.run([os.environ.get("JAVA", "java"), JAVA_XMX, "-jar", bsm, "d",
                                "-o", outd, jar],
                               check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if r.returncode != 0:
                print(f"  ⚠ 反汇编失败 [{p}] {name[:40]} (rc={r.returncode}): "
                      f"{r.stderr.decode('utf-8','ignore')[-300:]}")
        classes = {}
        if os.path.isdir(outd):
            classes = {os.path.relpath(os.path.join(r, f), outd): 0
                       for r, _, fs in os.walk(outd) for f in fs if f.endswith(".smali")}
            for rel in classes:
                pth = os.path.join(outd, rel.replace("/", os.sep))
                classes[rel] = sum(1 for ln in open(pth, encoding="utf-8", errors="ignore")
                                   if ln.lstrip().startswith(".method"))
        per_source.append((p, name, classes))
        total_classes_all += len(classes)
        print(f"  → {name[:40]}: {len(classes)} 类")
        try:
            with zipfile.ZipFile(jar) as z:
                for n in z.namelist():
                    if is_asset(n) and n not in assets:
                        assets[n] = z.read(n)
        except Exception:
            pass
        # 释放磁盘：已反汇编完的临时下载 jar 立即删除（本地仓库内的源保留）
        if jar.startswith(TMP):
            try:
                os.remove(jar)
            except Exception:
                pass
    if total_classes_all == 0:
        raise RuntimeError("没有任何 jar 反汇编出类（baksmali 全部失败），无法合并")

    chosen = {}; seen = set()
    for p, name, classes in per_source:
        for rel in classes:
            if rel not in seen:
                seen.add(rel); chosen[rel] = (p, classes[rel])
    print(f"去重后唯一类: {len(chosen)}，总方法: {sum(m for _, m in chosen.values())}")

    merged_smali = os.path.join(TMP, "merged")
    os.makedirs(merged_smali, exist_ok=True)
    for p, name, classes in per_source:
        for rel in list(chosen):
            if rel in classes:
                src = os.path.join(TMP, f"smali_{p}", rel.replace("/", os.sep))
                dst = os.path.join(merged_smali, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)
                del chosen[rel]
    all_rels = set(os.path.relpath(os.path.join(r, f), merged_smali)
                   for r, _, fs in os.walk(merged_smali) for f in fs if f.endswith(".smali"))
    print(f"merged_smali 实写类: {len(all_rels)}")

    # ============ 产物 A：全量 all_qist_spider.jar ============
    if MODE in ("all", "both"):
        print("\n[1/2] 生成全量 all_qist_spider.jar（所有类，不论大小）")
        dex_all = build_dex(all_rels, merged_smali, "all_", sml, "全量")
        if dex_all:
            pack_jar(OUT_ALL, dex_all, assets, os.environ.get("STRIP_SO_ALL", "0") == "1")

    # ============ 产物 B：完美版 qist_spider.jar（依赖闭包）============
    if MODE in ("closure", "both"):
        print("\n[2/2] 生成完美版 qist_spider.jar（依赖闭包裁剪）")
        roots = _config_spider_roots(all_rels)
        print(f"  闭包根（配置引用 csp 爬虫类）: {len(roots)}")
        TYPE_RE = re.compile(r"L[a-zA-Z0-9_/$]+;")
        graph = {}
        for root, _, fs in os.walk(merged_smali):
            for f in fs:
                if not f.endswith(".smali"):
                    continue
                rel = os.path.relpath(os.path.join(root, f), merged_smali)
                txt = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                deps = set()
                for t in TYPE_RE.findall(txt):
                    dr = t[1:-1] + ".smali"
                    if dr in all_rels and dr != rel:
                        deps.add(dr)
                graph[rel] = deps
        keep = set(); stack = list(roots)
        while stack:
            rel = stack.pop()
            if rel in keep:
                continue
            keep.add(rel)
            for d in graph.get(rel, ()):
                stack.append(d)
        print(f"  闭包保留 {len(keep)} 类")
        dex_c = build_dex(keep, merged_smali, "c_", sml, "完美版")
        if dex_c:
            pack_jar(OUT_CLOSURE, dex_c, assets, os.environ.get("STRIP_SO_CLOSURE", "1") == "1")

    print("\n完成。产物：")
    made = 0
    for o in (OUT_ALL, OUT_CLOSURE):
        if os.path.exists(o):
            made += 1
            print(f"  {o} ({os.path.getsize(o)/1048576:.2f}MB)")
    if made == 0:
        raise RuntimeError("本次未产出任何 jar，构建视为失败（请查看上方反汇编/汇编日志）")
    return True

def _config_spider_roots(all_rels):
    pat = re.compile(r'"api"\s*:\s*"csp_([A-Za-z0-9_]+)"')
    names = set()
    for root, dirs, files in os.walk(WORK):
        dirs[:] = [d for d in dirs if d not in (".git", ".merge_tmp", ".merge_tools")]
        for f in files:
            if not f.endswith((".txt", ".json", ".m3u")):
                continue
            try:
                txt = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for m in pat.finditer(txt):
                if m.group(1):
                    names.add(m.group(1))
    extra = set()
    for n in names:
        extra.add(re.sub(r"_2+$", "", n).rstrip("_"))
    names |= extra
    roots = []
    for name in names:
        rel = f"com/github/catvod/spider/{name}.smali"
        if rel in all_rels:
            roots.append(rel)
    return roots

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ 合并失败: {e}")
        sys.exit(1)
