#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转转 — 一键打包脚本

用法：
    pip install -r requirements.txt
    python build.py                 # 打包出 dist\转转.exe
    python build.py --skip-ffmpeg   # 不内嵌 ffmpeg（要求目标机 PATH 里有 ffmpeg）

产物：
    dist\\转转.exe                   单文件 GUI 主程序（无控制台窗口）

再用 Inno Setup 编译安装包：
    iscc installer\\setup.iss       → dist\\转转_Setup_2.0.0.exe

设计说明（为什么不用写死路径）：
  · 解释器   用「正在运行本脚本的解释器」sys.executable
  · 库数据   libtakiyasha 的数据文件用 import 出来的真实安装位置定位
  · ffmpeg   依次尝试 环境变量 FFMPEG_EXE → PATH → 常见安装位置
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
BUILD = HERE / "build"
APP_NAME = "转转"
ENTRY = HERE / "ncm_tray_app.py"
ICON = HERE / "assets" / "NCM-icon-192.png"

HIDDEN_IMPORTS = [
    "ncm_decrypt", "ncm_bg_service", "zhuanzhuan",
    "Crypto.Cipher.AES", "Crypto.Cipher._mode_ecb",
    "mutagen", "mutagen.flac", "mutagen.mp3", "mutagen.id3",
    "libtakiyasha", "libtakiyasha.ncm", "libtakiyasha.qmc",
    "libtakiyasha.kgmvpr", "libtakiyasha.kwm", "pyaes",
]


def find_ffmpeg() -> str:
    """定位 ffmpeg.exe：环境变量 → 系统 PATH → 常见安装位置。"""
    env = os.environ.get("FFMPEG_EXE")
    if env and Path(env).is_file():
        return env
    which = shutil.which("ffmpeg")
    if which:
        return which
    for cand in (
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\FFmpeg\bin\ffmpeg.exe",
        r"D:\FFmpeg\bin\ffmpeg.exe",
    ):
        if Path(cand).is_file():
            return cand
    raise SystemExit(
        "找不到 ffmpeg.exe。请任选一种方式解决：\n"
        "  1) 设置环境变量 FFMPEG_EXE=C:\\path\\to\\ffmpeg.exe\n"
        "  2) 把 ffmpeg 加进 PATH\n"
        "  3) 用 --skip-ffmpeg 打包（要求目标机自己装 ffmpeg）"
    )


def libtakiyasha_data_files():
    """收集 libtakiyasha 内嵌的二进制数据文件（打包后解密需要）。"""
    try:
        import libtakiyasha
    except ImportError:
        print("[警告] 未安装 libtakiyasha，将无法解密 QMC/KGM/KWM 等格式")
        print("       请先 pip install -r requirements.txt")
        return []

    pkg = Path(libtakiyasha.__file__).resolve().parent
    pairs = []
    specific = [
        (pkg / "_binaries" / "ncm" / "empty.flac", "libtakiyasha/_binaries/ncm"),
        (pkg / "_binaries" / "qmc" / "_qmcconsts" / "Key256MappingData",
         "libtakiyasha/_binaries/qmc/_qmcconsts"),
    ]
    for src, dst in specific:
        if src.is_file():
            pairs.append((str(src), dst))
    if not pairs:
        # 兜底：整个 _binaries 目录一起带上
        whole = pkg / "_binaries"
        if whole.is_dir():
            pairs.append((str(whole), "libtakiyasha/_binaries"))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ffmpeg", action="store_true",
                    help="不内嵌 ffmpeg（要求目标机 PATH 里有 ffmpeg）")
    ap.add_argument("--name", default=APP_NAME, help="产物名（默认 转转）")
    a = ap.parse_args()

    if not ENTRY.is_file():
        raise SystemExit(f"找不到入口脚本 {ENTRY}")

    DIST.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                 # GUI 应用，不要控制台窗口
        "--name", a.name,
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(HERE),
    ]
    if ICON.is_file():
        cmd += ["--icon", str(ICON)]

    for hi in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", hi]

    # 图标资源（程序运行时会从 _MEIPASS 或自身目录查找）
    for png in sorted((HERE / "assets").glob("NCM-icon-*.png")):
        cmd += ["--add-data", f"{png}{os.pathsep}."]

    if not a.skip_ffmpeg:
        cmd += ["--add-binary", f"{find_ffmpeg()}{os.pathsep}."]

    for src, dst in libtakiyasha_data_files():
        cmd += ["--add-data", f"{src}{os.pathsep}{dst}"]

    cmd.append(str(ENTRY))

    print("=" * 70)
    print("打包 %s" % a.name)
    print("=" * 70)
    print("解释器 :", sys.executable)
    print("输出目录:", DIST)
    print("命令   :", " ".join(cmd))
    print()

    rc = subprocess.run(cmd, cwd=HERE).returncode
    if rc != 0:
        print("\n打包失败，返回码 %s" % rc)
        return rc

    exe = DIST / f"{a.name}.exe"
    print("\n" + "=" * 70)
    if exe.is_file():
        print("打包成功：%s  (%.1f MB)" % (exe, exe.stat().st_size / 1048576))
        print("\n下一步：用 Inno Setup 生成安装包")
        print(r"   iscc installer\setup.iss")
        print(r"   → dist\%s_Setup_2.0.0.exe" % a.name)
    else:
        print("打包完成，但没找到 %s" % exe)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
