# -*- coding: utf-8 -*-
"""云编译主入口：干净基座 + V8 独有类 -> 双 jar（全量 / 精简）

【自包含版】本文件不再 import merge_engine.py，四个素材也由脚本自己下载并校验。
仓库里只要放这一个 py 文件 + 一个 workflow 就能跑，缺什么自动补什么。

素材（自动下载，逐个 dex 比 MD5，不对就失败）：
    qist_spider_v8.jar     V8 全量 (7 dex)      22,087,575 B
    qist_spider_nop.jar    干净基座 (5 dex)     13,454,716 B
    tools_all.jar          baksmali+smali fat   5,086,583 B
    jc-1.48.jar            jcommander 1.48        63,504 B

为什么必须比 dex MD5、不能只比文件大小：
    仓库里那份 jar/all_qist_spider.jar 同样有 7 个 dex、每个 dex 大小也逐个相同，
    但 MD5 全不一致 —— 它是汇编往返过的版本，混淆类被重排，同名类里装的是另一份
    实现（实测 63 个类方法数对不上：Hxq 少 46 个、merge/J/i 多 16 个）。
    用它编出来的包类名齐全、重复类也是 0，但内容不是同一份。只看大小会漏掉这个。

一条命令跑完全流程：
    python3 build_clean.py

阶段可单跑：
    python3 build_clean.py disasm     # 逐 dex 反汇编
    python3 build_clean.py combine    # 剔加壳 + 按类名合并去重
    python3 build_clean.py build      # 分 dex 汇编 + 打两个 jar
    python3 build_clean.py verify     # 成品自检（重复类必须为 0）

环境变量：
    JAVA_MEM            默认 1600m
    DEX_METHOD_TARGET   默认 22000
    V8_JAR / NOP_JAR / TOOL_ALL / JC_JAR   覆盖素材路径（会先校验，不合格就不用）
"""
import os, sys, re, shutil, subprocess, hashlib, zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(BASE, ".merge_tmp")
OUTDIR = os.path.join(BASE, "jar")

# ---------------- 素材定义：URL + 逐 dex 指纹 ----------------
ASSETS = {
    "qist_spider_v8.jar": (
        "https://aka.doubaocdn.com/s/oU9Tx6Nytj",
        {
            "classes.dex":  (8328632, "f391d1cb9458385802193690adbdda37"),
            "classes2.dex": (5529248, "b256e1b6d44377a72fa6e47bc327d989"),
            "classes3.dex": (3768200, "3861714a1c25df4bd0ff1df055ccf8c9"),
            "classes4.dex": (3767656, "aca8891fac66c30d86ed2916865da262"),
            "classes5.dex": (3977380, "10c6e6d3b2bc7d274e33046288ca3590"),
            "classes6.dex": (3170720, "979f4e06a490027563efd3cd3862cfcc"),
            "classes7.dex": (1963196, "02139bd3379dd1a57b4127df21c33469"),
        },
        None),
    "qist_spider_nop.jar": (
        "https://aka.doubaocdn.com/s/qX5YV6QqKY",
        {
            "classes.dex":  (4815112, "23f09e9f6960b1539cd5fd828f9ef230"),
            "classes2.dex": (6330456, "319a6cec8216dcf8f3ad4493812d095c"),
            "classes3.dex": (6322260, "d9d0c0e11461f8d850004a7864f41b8f"),
            "classes4.dex": (10453960, "14fd2fe996d7415ef9fec0b1d9fde268"),
            "classes5.dex": (8444644, "10041e55889f0fe42966ee427965680c"),
        },
        None),
    "tools_all.jar": ("https://aka.doubaocdn.com/s/zaIORwEYQY", None, 5086583),
    "jc-1.48.jar": ("https://aka.doubaocdn.com/s/6PniGnFxs8", None, 63504),
}


def _download(url, dst):
    r = subprocess.run(["curl", "-fL", "--retry", "3", "--retry-delay", "2",
                        "--connect-timeout", "20", "-o", dst, url],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return r.returncode == 0


def _check(p, dexexp, size_only):
    """返回 (ok, 说明)"""
    if not os.path.exists(p):
        return False, "文件不存在"
    if dexexp:
        try:
            with zipfile.ZipFile(p) as z:
                bad = []
                for dn, (sz, md5) in dexexp.items():
                    if dn not in z.namelist():
                        bad.append("%s 缺失" % dn); continue
                    d = z.read(dn)
                    if (len(d), hashlib.md5(d).hexdigest()) != (int(sz), md5):
                        bad.append("%s 指纹不符(%s)" % (dn, hashlib.md5(d).hexdigest()[:10]))
                if bad:
                    return False, "; ".join(bad[:3])
                return True, "%d dex 指纹全对（原始素材）" % len(dexexp)
        except Exception as e:
            return False, "不是合法 jar: %s" % e
    got = os.path.getsize(p)
    if abs(got - size_only) > size_only * 0.2:
        return False, "尺寸 %d，期望 %d" % (got, size_only)
    return True, "%d B" % got


def ensure_assets():
    """确保四件素材存在且指纹正确。不合格就重新下载；再不合格就退出。"""
    paths = {}
    ok_all = True
    for fname, (url, dexexp, size_only) in ASSETS.items():
        p = os.environ.get({"qist_spider_v8.jar": "V8_JAR",
                            "qist_spider_nop.jar": "NOP_JAR",
                            "tools_all.jar": "TOOL_ALL",
                            "jc-1.48.jar": "JC_JAR"}[fname])
        if not p:
            p = os.path.join(BASE, fname)
        ok, msg = _check(p, dexexp, size_only)
        if ok:
            print("OK   %s %s" % (fname, msg))
            paths[fname] = p
            continue
        print("     %s %s -> 重新下载" % (fname, msg))
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
        if not _download(url, p):
            print("FAIL %s 下载失败" % fname)
            ok_all = False
            continue
        ok, msg = _check(p, dexexp, size_only)
        if not ok:
            print("FAIL %s 下载后仍不对: %s" % (fname, msg))
            ok_all = False
        else:
            print("OK   %s %s" % (fname, msg))
            paths[fname] = p
    if not ok_all:
        print("x 素材校验未通过，停止（不会用错素材凑合编译）")
        sys.exit(2)
    return paths


PATHS = ensure_assets()
NOP_JAR = PATHS["qist_spider_nop.jar"]
V8_JAR = PATHS["qist_spider_v8.jar"]
TOOL_ALL = PATHS["tools_all.jar"]
JC_JAR = PATHS["jc-1.48.jar"]

JAVA = os.environ.get("JAVA", "java")
JAVA_MEM = os.environ.get("JAVA_MEM", "1600m")
JAVA_XMX = "-Xmx" + JAVA_MEM
DEX_METHOD_TARGET = int(os.environ.get("DEX_METHOD_TARGET", "22000"))
SMALI_CP = TOOL_ALL + ":" + JC_JAR

DIR_NOP = os.path.join(TMP, "smali_nop")
DIR_V8 = os.path.join(TMP, "smali_v8")
DIR_CLEAN = os.path.join(TMP, "smali_clean")
DIR_COMB = os.path.join(TMP, "smali_combined")

CLS_RE = re.compile(r'^\.class\s+(?:[^\n]*?\s)?(L[^;]+;)\s*$', re.M)
KILL_PREFIX = ("np/", "com/github/catvod/parser/merge/",
               "com/github/catvod/spider/merge/",
               "com/github/catvod/spider/mergeguard/")
KILL_REF = re.compile(
    r'L(?:np/|com/github/catvod/parser/merge/'
    r'|com/github/catvod/spider/merge/|com/github/catvod/spider/mergeguard/)[^;]*;')


def smali_files(root):
    for dp, _, fn in os.walk(root):
        for f in fn:
            if f.endswith(".smali"):
                yield os.path.join(dp, f)


def class_of(p):
    try:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            head = fh.read(3000)
    except Exception:
        return None
    m = CLS_RE.search(head)
    return m.group(1) if m else None


def preflight():
    print("输入就绪：")
    print("   nop   %s (%.2f MB)" % (NOP_JAR, os.path.getsize(NOP_JAR) / 1048576))
    print("   v8    %s (%.2f MB)" % (V8_JAR, os.path.getsize(V8_JAR) / 1048576))
    print("   tools %s / %s" % (os.path.basename(TOOL_ALL), os.path.basename(JC_JAR)))


# ---------------- 1) 逐 dex 反汇编 ----------------------------
def disasm_one(jar, outdir):
    if os.path.exists(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir, exist_ok=True)
    dexdir = os.path.join(TMP, "_dex_" + os.path.basename(outdir))
    shutil.rmtree(dexdir, ignore_errors=True)
    os.makedirs(dexdir, exist_ok=True)
    with zipfile.ZipFile(jar) as z:
        dexes = sorted([n for n in z.namelist() if n.endswith(".dex")])
        for n in dexes:
            z.extract(n, dexdir)
    print("  %s: %d 个 dex -> 逐个反汇编" % (os.path.basename(jar), len(dexes)))
    ok = 0
    for i, n in enumerate(dexes, 1):
        d = os.path.join(dexdir, n)
        o = os.path.join(outdir, "dex%d" % i)
        r = subprocess.run([JAVA, JAVA_XMX, "-cp", SMALI_CP,
                            "org.jf.baksmali.Main", "d", "-o", o, d],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        cnt = sum(len(fs) for _, _, fs in os.walk(o)) if os.path.isdir(o) else 0
        print("    dex%d -> %d smali rc=%d" % (i, cnt, r.returncode))
        if r.returncode != 0:
            print("    x %s" % r.stderr.decode("utf-8", "ignore")[-400:])
        else:
            ok += 1
    if ok != len(dexes):
        print("x 反汇编不全：%d/%d 成功" % (ok, len(dexes)))
        sys.exit(3)
    total = sum(len(fs) for _, _, fs in os.walk(outdir))
    print("  %s 合计 %d smali" % (os.path.basename(outdir), total))


def stage_disasm():
    os.makedirs(TMP, exist_ok=True)
    disasm_one(NOP_JAR, DIR_NOP)
    disasm_one(V8_JAR, DIR_V8)


# ---------------- 2) 剔加壳 + 合并去重 ---------------------------
def stage_combine():
    if not (os.path.isdir(DIR_NOP) and os.path.isdir(DIR_V8)):
        print("x 缺少反汇编结果，先跑 disasm")
        sys.exit(3)

    print("[1] 复制 V8 并删除加壳目录")
    shutil.rmtree(DIR_CLEAN, ignore_errors=True)
    shutil.copytree(DIR_V8, DIR_CLEAN)
    killed = 0
    for dp, dn, fn in list(os.walk(DIR_CLEAN)):
        rel = os.path.relpath(dp, DIR_CLEAN).replace(os.sep, "/")
        if rel == ".":
            continue
        for pre in KILL_PREFIX:
            pre = pre.rstrip("/")
            if rel == pre or rel.startswith(pre + "/"):
                shutil.rmtree(dp, ignore_errors=True)
                killed += 1
                break
    print("   删除加壳目录数: %d" % killed)

    print("[2] 传递删除引用了加壳类的文件")
    it = 0
    while True:
        it += 1
        n = 0
        for p in list(smali_files(DIR_CLEAN)):
            rel = os.path.relpath(p, DIR_CLEAN).replace(os.sep, "/")
            if any(rel.startswith(x) for x in KILL_PREFIX):
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            if KILL_REF.search(txt):
                os.remove(p)
                n += 1
        print("   第 %d 轮删除 %d" % (it, n))
        if n == 0 or it >= 10:
            break

    print("[3] 干净基座 + V8 独有类 -> 合并区")
    shutil.rmtree(DIR_COMB, ignore_errors=True)
    shutil.copytree(DIR_NOP, DIR_COMB)
    nop_cls = set()
    for p in smali_files(DIR_NOP):
        c = class_of(p)
        if c:
            nop_cls.add(c)
    added = 0
    for p in smali_files(DIR_CLEAN):
        c = class_of(p)
        if not c or c in nop_cls:
            continue
        rel = os.path.relpath(p, DIR_CLEAN)
        dst = os.path.join(DIR_COMB, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(p, dst)
        nop_cls.add(c)
        added += 1
    print("   补入 V8 独有类: %d" % added)

    print("[4] 按类名全局去重（同名只留一份）")
    seen = {}
    removed = 0
    for p in list(smali_files(DIR_COMB)):
        c = class_of(p)
        if not c:
            continue
        if c in seen:
            os.remove(p)
            removed += 1
        else:
            seen[c] = p
    print("   去重删除: %d | 最终唯一类: %d" % (removed, len(seen)))
    print("OK combine 完成")


# ---------------- 3) 分 dex 汇编 + 打包 ---------------------------
def _methods_in(d):
    out = []
    for root, _, fs in os.walk(d):
        for f in fs:
            if f.endswith(".smali"):
                rel = os.path.relpath(os.path.join(root, f), d).replace("\\", "/")
                m = sum(1 for ln in open(os.path.join(root, f), encoding="utf-8", errors="ignore")
                        if ln.lstrip().startswith(".method"))
                out.append((rel, m))
    return out


def assemble_group(gdir, outdex):
    for attempt in range(1, 7):
        r = subprocess.run([JAVA, JAVA_XMX, "-cp", SMALI_CP,
                            "org.jf.smali.Main", "assemble", "-o", outdex, gdir],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if r.returncode == 0 and os.path.exists(outdex):
            return True
        err = r.stderr.decode("utf-8", "ignore")
        # 一次清掉本轮错误里出现的所有毒类，避免每轮只删一个导致重试轮耗尽
        poisons = set(re.findall(r"Lcom/[\w/$]+;", err))
        removed = 0
        for pcls in poisons:
            poison = os.path.join(gdir, pcls.replace("L", "", 1).replace(";", ".smali"))
            if os.path.isfile(poison):
                print("  ! 删除毒类 %s" % pcls)
                os.remove(poison)
                removed += 1
        if removed:
            continue
        print("  x 汇编失败(第%d轮): %s" % (attempt, err[-500:]))
        break
    return False


def build_dex(rel_list, merged_smali, prefix, tag):
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
    print("  %s: %d 类 / %d 方法，分 %d 组 %s" % (
        tag, len(files), sum(m for _, m in files), len(groups),
        [sum(m for _, m in g) for g in groups]))
    dex_files = []
    for gi, g in enumerate(groups, 1):
        gdir = os.path.join(TMP, "%sg%d" % (prefix, gi))
        # 必须先清空旧组目录，否则上一轮残留的类会被一起汇编进 dex
        if os.path.isdir(gdir):
            shutil.rmtree(gdir, ignore_errors=True)
        os.makedirs(gdir, exist_ok=True)
        for rel, _ in g:
            dst = os.path.join(gdir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.join(merged_smali, rel), dst)
        outdex = os.path.join(TMP, "%sclasses%s.dex" % (prefix, "" if gi == 1 else str(gi)))
        if assemble_group(gdir, outdex):
            dex_files.append(outdex)
    return dex_files


def pack_jar(out, dex_files, assets, strip_so=False):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # dex 统一标准命名 classes.dex / classes2.dex ...（TVBox/Android 只认这套）
        for i, df in enumerate(dex_files, 1):
            z.write(df, "classes.dex" if i == 1 else "classes%d.dex" % i)
        for n, d in assets.items():
            if strip_so and (".so" in n.lower() or n.endswith(".so")):
                continue
            z.writestr(n, d)
    md5 = hashlib.md5(open(out, "rb").read()).hexdigest()
    print("  OK %s: %.2fMB | %d dex | MD5 %s%s" % (
        os.path.basename(out), os.path.getsize(out) / 1048576, len(dex_files), md5,
        " | 已去.so" if strip_so else ""))
    open(out + ".md5", "w").write(md5)


def stage_build():
    if not os.path.isdir(DIR_COMB):
        print("x 缺少合并结果，先跑 combine")
        sys.exit(3)

    # 精简版：干净基座按类名去重后的类集
    seen = {}
    for p in smali_files(DIR_NOP):
        c = class_of(p)
        if c and c not in seen:
            seen[c] = os.path.relpath(p, DIR_NOP).replace(os.sep, "/")
    nop_rels = list(seen.values())
    print("精简版基座类: %d" % len(nop_rels))
    dex_lite = build_dex(set(nop_rels), DIR_NOP, "l_", "精简版")
    if not dex_lite:
        print("x 精简版汇编失败")
        sys.exit(4)
    os.makedirs(OUTDIR, exist_ok=True)
    pack_jar(os.path.join(OUTDIR, "qist_spider_lite.jar"), dex_lite, {}, strip_so=True)

    # 全量版：合并区全部类
    all_rels = [os.path.relpath(p, DIR_COMB).replace(os.sep, "/")
                for p in smali_files(DIR_COMB)]
    print("全量版类: %d" % len(all_rels))
    dex_full = build_dex(set(all_rels), DIR_COMB, "f_", "全量版")
    if not dex_full:
        print("x 全量版汇编失败")
        sys.exit(4)
    pack_jar(os.path.join(OUTDIR, "qist_spider.jar"), dex_full, {}, strip_so=True)
    print("OK build 完成")


# ---------------- 4) 成品自检（重复类必须为 0）---------------
def verify_jar(jar):
    print("=" * 60)
    print("%s  %.2f MB" % (os.path.basename(jar), os.path.getsize(jar) / 1048576))
    with zipfile.ZipFile(jar) as z:
        dexes = [n for n in z.namelist() if n.endswith(".dex")]
        so = [n for n in z.namelist() if n.lower().endswith(".so")]
        tmp = os.path.join(TMP, "_verify_" + os.path.basename(jar).replace(".jar", ""))
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        allcls = {}
        dup = 0
        for d in dexes:
            p = os.path.join(tmp, d)
            with open(p, "wb") as fh:
                fh.write(z.read(d))
            o = p + "_smali"
            r = subprocess.run([JAVA, JAVA_XMX, "-cp", SMALI_CP,
                                "org.jf.baksmali.Main", "d", "-o", o, p],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if r.returncode != 0:
                print("  x %s 反汇编失败: %s" % (d, r.stderr.decode("utf-8", "ignore")[-200:]))
                continue
            cnt = 0
            for dp, _, fn in os.walk(o):
                for f in fn:
                    if not f.endswith(".smali"):
                        continue
                    cnt += 1
                    c = class_of(os.path.join(dp, f))
                    if not c:
                        continue
                    if c in allcls:
                        dup += 1
                    else:
                        allcls[c] = True
            print("  %s: %d smali" % (d, cnt))
    sp = sum(1 for c in allcls if c.startswith("Lcom/github/catvod/spider/"))
    print("  dex=%d | .so=%s | 唯一类=%d | 重复类=%d | spider 包类=%d"
          % (len(dexes), so if so else "无", len(allcls), dup, sp))
    return {"jar": os.path.basename(jar), "dex": len(dexes), "so": len(so),
            "classes": len(allcls), "dup": dup, "spider": sp}


def stage_verify():
    report = []
    for f in ("qist_spider_lite.jar", "qist_spider.jar"):
        p = os.path.join(OUTDIR, f)
        if not os.path.exists(p):
            print("x 缺少产物 %s" % p)
            sys.exit(5)
        report.append(verify_jar(p))
    bad = [r for r in report if r["dup"] > 0]
    if bad:
        print("x 去重不彻底：%s" % ", ".join(
            "%s 重复 %d" % (r["jar"], r["dup"]) for r in bad))
        sys.exit(6)
    print("OK 两个 jar 重复类均为 0")


STAGES = {"disasm": stage_disasm, "combine": stage_combine,
          "build": stage_build, "verify": stage_verify}


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    preflight()
    if stage == "all":
        for s in ("disasm", "combine", "build", "verify"):
            print("\n########## %s ##########" % s)
            STAGES[s]()
    else:
        STAGES[stage]()


if __name__ == "__main__":
    main()
