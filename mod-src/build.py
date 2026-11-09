#!/usr/bin/env python3
"""Build X Unhook from an X .apkm bundle.

    python3 -I mod-src/build.py --apkm /path/to/com.twitter.android_*.apkm [--no-clone] [--bump]

Pipeline: extract -> apktool decode (smali only) -> fingerprint -> hooks -> accessor layer ->
clone manifest/resources -> compile mod -> apktool build -> splits -> zipalign + sign -> bundle.
Outputs land in build/out/ (base.apk, split_*.apk, X-Unhook-v<N>.apks, xmod-release.json).
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from xmodpatch import accessors, axml                      # noqa: E402
from xmodpatch.fingerprints import Resolver, FingerprintError  # noqa: E402
from xmodpatch.hooks import Patcher                         # noqa: E402

TOOLS = ROOT / "tools"
SECRETS = ROOT / "secrets"
JAVA_HOME = Path(glob.glob(str(TOOLS / "jdk-*"))[0])
BT = Path(glob.glob(str(TOOLS / "bt" / "*"))[0])
ANDROID_JAR = Path(glob.glob(str(TOOLS / "plat" / "*" / "android.jar"))[0])
APKTOOL = TOOLS / "apktool.jar"
ORIG_PACKAGE = "com.twitter.android"


def run(cmd, **kw):
    env = dict(os.environ, JAVA_HOME=str(JAVA_HOME), PATH=f"{JAVA_HOME}/bin:{os.environ['PATH']}")
    print("  $", " ".join(str(c) for c in cmd)[:200], flush=True)
    subprocess.run([str(c) for c in cmd], check=True, env=env, **kw)


def step(msg):
    print(f"\n==> {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apkm", required=True)
    ap.add_argument("--no-clone", action="store_true", help="keep package name com.twitter.android")
    ap.add_argument("--bump", action="store_true", help="increment mod_version in xmod.json first")
    ap.add_argument("--work", default=str(ROOT / "work"))
    ap.add_argument("--out", default=str(ROOT / "build" / "out"))
    a = ap.parse_args()

    cfg_path = HERE / "xmod.json"
    cfg = json.loads(cfg_path.read_text())
    if a.bump:
        cfg["mod_version"] += 1
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
    clone = not a.no_clone
    package = cfg["package"] if clone else ORIG_PACKAGE
    work, out = Path(a.work), Path(a.out)

    step("extract bundle")
    shutil.rmtree(work, ignore_errors=True)
    (work / "apkm").mkdir(parents=True)
    with zipfile.ZipFile(a.apkm) as z:
        for n in z.namelist():
            if n.endswith(".apk") or n == "info.json":
                z.extract(n, work / "apkm")
    info = json.loads((work / "apkm" / "info.json").read_text()) if (work / "apkm" / "info.json").exists() else {}
    x_version = info.get("release_version", "?").split("-")[0]
    x_code = int(info.get("versioncode", 0))
    print(f"  X {x_version} ({x_code}), mod v{cfg['mod_version']}, package {package}")

    step("decode base.apk (smali only)")
    dec = work / "decoded"
    run(["java", "-Xmx8g", "-jar", APKTOOL, "d", "-r", "-f", "-o", dec, work / "apkm" / "base.apk"],
        stdout=subprocess.DEVNULL)

    step("fingerprint")
    try:
        mapping = Resolver(dec).resolve()
    except FingerprintError as e:
        sys.exit(f"FINGERPRINT FAILED: {e}\nThis X version changed something the mod depends on.")
    (work / "mapping.json").write_text(json.dumps(mapping, indent=1))
    print(f"  resolved {len(mapping)} items -> work/mapping.json")

    step("hooks")
    p = Patcher(dec, mapping)
    touched = p.apply()
    print(f"  patched {len(touched)} classes, {p.mapper_calls} mapper call sites")

    # patched classes + accessor layer go to a fresh dex (the original dexes are full)
    idx = [1] + [int(d.name[len("smali_classes"):]) for d in dec.glob("smali_classes*")]
    newdir = dec / f"smali_classes{max(idx) + 1}"
    for f in touched:
        rel = f.relative_to(next(d for d in f.parents if d.parent == dec))
        dst = newdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(dst))
    a_smali, a_java = accessors.generate(mapping)
    (newdir / "xmod").mkdir(parents=True, exist_ok=True)
    (newdir / "xmod" / "A.smali").write_text(a_smali)
    print(f"  moved patched classes + xmod/A.smali -> {newdir.name}")

    if clone:
        step(f"clone package -> {package}")
        is_class = lambda s: any((d / (s.replace(".", "/") + ".smali")).exists()  # noqa: E731
                                 for d in dec.iterdir() if d.name.startswith("smali"))
        man = axml.AXml((dec / "AndroidManifest.xml").read_bytes())
        changed = axml.rename_package(man, ORIG_PACKAGE, package, is_class)
        labels = axml.set_app_label(man, cfg.get("label", "X Unhook"))
        man.add_uses_permission("android.permission.REQUEST_INSTALL_PACKAGES")
        (dec / "AndroidManifest.xml").write_bytes(man.build())
        res = axml.ResTable((dec / "resources.arsc").read_bytes())
        acct = 0
        for i, s in enumerate(res.pool.strings):
            if s == ORIG_PACKAGE + ".auth.login":
                res.pool.strings[i] = package + ".auth.login"
                acct += 1
        (dec / "resources.arsc").write_bytes(res.build())
        print(f"  manifest: {len(changed)} names renamed, {labels} labels; account type renamed: {acct}")
    else:
        man = axml.AXml((dec / "AndroidManifest.xml").read_bytes())
        if man.add_uses_permission("android.permission.REQUEST_INSTALL_PACKAGES"):
            (dec / "AndroidManifest.xml").write_bytes(man.build())

    step("compile mod")
    gen, classes, stubs, dexdir = work / "gen", work / "classes", work / "stubs", work / "dex"
    for d in (gen, classes, stubs, dexdir):
        d.mkdir(parents=True, exist_ok=True)
    (gen / "xmod").mkdir(exist_ok=True)
    (gen / "xmod" / "A.java").write_text(a_java)
    token = (SECRETS / "read_token").read_text().strip() if (SECRETS / "read_token").exists() else ""
    (gen / "xmod" / "Cfg.java").write_text(
        "package xmod;\n\nfinal class Cfg {\n"
        f"    static final int MOD_VERSION = {int(cfg['mod_version'])};\n"
        f"    static final String X_VERSION = {json.dumps(x_version)};\n"
        f"    static final String REPO = {json.dumps(cfg.get('repo', ''))};\n"
        f"    static final String TOKEN = {json.dumps(token)};\n"
        "}\n")
    boot = f"{ANDROID_JAR}:{BT / 'core-lambda-stubs.jar'}"
    run(["javac", "-nowarn", "-Xlint:none", "-source", "8", "-target", "8", "-bootclasspath", boot,
         "-d", stubs, gen / "xmod" / "A.java"], stderr=subprocess.DEVNULL)
    srcs = sorted(glob.glob(str(HERE / "src" / "xmod" / "*.java"))) + [str(gen / "xmod" / "Cfg.java")]
    run(["javac", "-nowarn", "-Xlint:none", "-source", "8", "-target", "8", "-bootclasspath", boot,
         "-cp", stubs, "-d", classes] + srcs)
    run(["java", "-cp", BT / "lib" / "d8.jar", "com.android.tools.r8.D8", "--release", "--min-api", "29",
         "--lib", ANDROID_JAR, "--classpath", stubs, "--output", dexdir]
        + sorted(glob.glob(str(classes / "xmod" / "*.class"))))

    step("build base.apk")
    unsigned = work / "base-unsigned.apk"
    run(["java", "-Xmx10g", "-jar", APKTOOL, "b", "-f", "-j", "8", "-o", unsigned, dec],
        stdout=subprocess.DEVNULL)
    with zipfile.ZipFile(unsigned) as z:
        n = len([x for x in z.namelist() if x.startswith("classes") and x.endswith(".dex")])
    shutil.copy(dexdir / "classes.dex", dexdir / f"classes{n + 1}.dex")
    run(["zip", "-q", "-0", unsigned.resolve(), f"classes{n + 1}.dex"], cwd=dexdir)
    print(f"  mod dex -> classes{n + 1}.dex")

    step("splits")
    splits = sorted((work / "apkm").glob("split_*.apk"))
    staged = work / "staged"
    staged.mkdir()
    shutil.copy(unsigned, staged / "base.apk")
    for sp in splits:
        dst = staged / sp.name
        if not clone:
            shutil.copy(sp, dst)
            continue
        with zipfile.ZipFile(sp) as zi, zipfile.ZipFile(dst, "w") as zo:
            for item in zi.infolist():
                if item.filename.startswith("META-INF/") and item.filename.split(".")[-1] in ("SF", "RSA", "EC", "MF"):
                    continue
                data = zi.read(item.filename)
                if item.filename == "AndroidManifest.xml":
                    doc = axml.AXml(data)
                    axml.rename_package(doc, ORIG_PACKAGE, package, lambda s: False)
                    data = doc.build()
                zi2 = zipfile.ZipInfo(item.filename, date_time=item.date_time)
                zi2.compress_type = item.compress_type
                zi2.external_attr = item.external_attr
                zo.writestr(zi2, data)
    print(f"  {len(splits)} splits")

    step("align + sign")
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    ks = SECRETS / "xmod.keystore"
    if not ks.exists():
        run(["keytool", "-genkeypair", "-keystore", ks, "-storepass", "xmodpass", "-keypass", "xmodpass",
             "-alias", "xmod", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=X Unhook"])
    for apk in sorted(staged.glob("*.apk")):
        dst = out / apk.name
        run([BT / "zipalign", "-P", "16", "-f", "4", apk, dst])
        run([BT / "apksigner", "sign", "--ks", ks, "--ks-pass", "pass:xmodpass", "--ks-key-alias", "xmod", dst])
        run([BT / "apksigner", "verify", dst], stdout=subprocess.DEVNULL)
    for f in out.glob("*.idsig"):
        f.unlink()

    meta = {"mod_version": cfg["mod_version"], "x_version": x_version, "x_version_code": x_code,
            "package": package}
    (out / "xmod-release.json").write_text(json.dumps(meta, indent=1) + "\n")
    bundle = out / f"X-Unhook-v{cfg['mod_version']}-X{x_version}.apks"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_STORED) as z:
        for apk in sorted(out.glob("*.apk")):
            z.write(apk, apk.name)
    print(f"\nDONE: {bundle}  ({bundle.stat().st_size / 1048576:.0f} MB)")


if __name__ == "__main__":
    main()
