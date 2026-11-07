# -*- coding: utf-8 -*-
"""云编译主入口：干净基座 + V8 独有类 -> 双 jar（全量 / 精简）

仓库根必须有的 6 个文件（手机上传时全部放根目录，无需建子目录）：
    build_clean.py            本文件
    merge_engine.py           只借用里面的 build_dex / pack_jar / _methods_in
    tools_all.jar             baksmali + smali 合并、剔除 com/beust 的 fat jar
    jc-1.48.jar               jcommander 1.48（smali 汇编器唯一能跑通的版本）
    qist_spider_nop.jar       干净基座（实测能 load success）
    qist_spider_v8.jar        V8 全量（含加壳，仅用来取独有类）
    jar/                      产物输出目录（脚本自动创建）

也兼容子目录布局：inputs/qist_spider_nop.jar、tools/tools_all.jar 等。

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
    NOP_JAR / V8_JAR    覆盖输入路径
    TOOL_ALL / JC_JAR   覆盖工具路径
"""
import os, sys, re, shutil, subprocess, hashlib, zipfile
BASE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(BASE, ".merge_tmp")
OUTDIR = os.path.join(BASE, "jar")


def _pick(env_name, *candidates):
    p = os.environ.get(env_name)
    if p:
        return p
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


NOP_JAR = _pick("NOP_JAR",
                os.path.join(BASE, "inputs", "qist_spider_nop.jar"),
                os.path.join(BASE, "qist_spider_nop.jar"))
V8_JAR = _pick("V8_JAR",
               os.path.join(BASE, "inputs", "qist_spider_v8.jar"),
               os.path.join(BASE, "qist_spider_v8.jar"),
               os.path.join(BASE, "qist_spider_full_1305classes.jar"))
TOOL_ALL = _pick("TOOL_ALL",
                 os.path.join(BASE, "tools", "tools_all.jar"),
                 os.path.join(BASE, "tools_all.jar"))
JC_JAR = _pick("JC_JAR",
               os.path.join(BASE, "tools", "jc-1.48.jar"),
               os.path.join(BASE, "jc-1.48.jar"))

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
    bad = []
    for name, p in (("干净基座", NOP_JAR), ("V8 全量", V8_JAR),
                    ("tools_all.jar", TOOL_ALL), ("jc-1.48.jar", JC_JAR)):
        if not os.path.exists(p):
            bad.append("%s 缺失: %s" % (name, p))
    if bad:
        print("✗ 前置检查失败：")
        for b in bad:
            print("   " + b)
        sys.exit(2)
    print("✓ 输入就绪：")
    print("   nop  %s (%.2f MB)" % (NOP_JAR, os.path.getsize(NOP_JAR) / 1048576))
    print("   v8   %s (%.2f MB)" % (V8_JAR, os.path.getsize(V8_JAR) / 1048576))
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
            print("    ✗ %s" % r.stderr.decode("utf-8", "ignore")[-400:])
        else:
            ok += 1
    if ok != len(dexes):
        print("✗ 反汇编不全：%d/%d 成功" % (ok, len(dexes)))
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
        print("✗ 缺少反汇编结果，先跑 disasm")
        sys.exit(3)

    print("① 复制 V8 并删除加壳目录")
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
print("② 传递删除引用了加壳类的文件")
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

    print("③ 干净基座 + V8 独有类 -> 合并区")
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

    print("④ 按类名全局去重（同名只留一份）")
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
    print("✓ combine 完成")


# ---------------- 3) 分 dex 汇编 + 打包 ---------------------------
def stage_build():
    if not os.path.isdir(DIR_COMB):
        print("✗ 缺少合并结果，先跑 combine")
        sys.exit(3)
    os.environ["SMALI_CP"] = SMALI_CP
    os.environ["JAVA_MEM"] = JAVA_MEM
    os.environ["DEX_METHOD_TARGET"] = str(DEX_METHOD_TARGET)
    sys.path.insert(0, BASE)
    import merge_engine as me

    # 精简版：干净基座按类名去重后的类集
    seen = {}
    for p in smali_files(DIR_NOP):
c = class_of(p)
        if c and c not in seen:
            seen[c] = os.path.relpath(p, DIR_NOP).replace(os.sep, "/")
    nop_rels = list(seen.values())
    print("精简版基座类: %d" % len(nop_rels))
    dex_lite = me.build_dex(set(nop_rels), DIR_NOP, "l_", None, "精简版")
    if not dex_lite:
        print("✗ 精简版汇编失败")
        sys.exit(4)
    os.makedirs(OUTDIR, exist_ok=True)
    me.pack_jar(os.path.join(OUTDIR, "qist_spider_lite.jar"), dex_lite, {}, strip_so=True)

    # 全量版：合并区全部类
    all_rels = [os.path.relpath(p, DIR_COMB).replace(os.sep, "/")
                for p in smali_files(DIR_COMB)]
    print("全量版类: %d" % len(all_rels))
    dex_full = me.build_dex(set(all_rels), DIR_COMB, "f_", None, "全量版")
    if not dex_full:
        print("✗ 全量版汇编失败")
        sys.exit(4)
    me.pack_jar(os.path.join(OUTDIR, "qist_spider.jar"), dex_full, {}, strip_so=True)

    for f in ("qist_spider_lite.jar", "qist_spider.jar"):
        p = os.path.join(OUTDIR, f)
        if os.path.exists(p):
            print("  %s  %.2f MB  MD5 %s" % (
f, os.path.getsize(p) / 1048576,
                hashlib.md5(open(p, "rb").read()).hexdigest()))
    print("✓ build 完成")


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
                print("  ✗ %s 反汇编失败: %s" % (d, r.stderr.decode("utf-8", "ignore")[-200:]))
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
            print("✗ 缺少产物 %s" % p)
            sys.exit(5)
        report.append(verify_jar(p))
    bad = [r for r in report if r["dup"] > 0]
    if bad:
        print("✗ 去重不彻底：%s" % ", ".join(
            "%s 重复 %d" % (r["jar"], r["dup"]) for r in bad))
        sys.exit(6)
    print("✓ 两个 jar 重复类均为 0")


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
