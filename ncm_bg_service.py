#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NCM 后台自动转换服务
====================
无窗口运行，开机自启，可绑定网易云进程。

用法:
    python ncm_bg_service.py          # 后台持续监视模式
    python ncm_bg_service.py --bind   # 绑定网易云：网易云启动时才运行
    python ncm_bg_service.py --reset  # 清除状态，重新处理所有文件

注册开机自启:
    python ncm_bg_service.py --install

移除开机自启:
    python ncm_bg_service.py --uninstall
"""
import os
import sys
import time
import json
import subprocess
import ctypes
from pathlib import Path
from datetime import datetime

# 隐藏控制台窗口
if sys.platform == 'win32':
    try:
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass

# 导入统一解密模块
sys.path.insert(0, str(Path(__file__).parent))
from zhuanzhuan import unlock_audio, get_supported_extensions

# ===== 配置 =====
def _default_watch_dir() -> Path:
    """确定要监视的下载目录（网易云默认下载目录）。

    优先级：
      1. 环境变量 ZHUANZHUAN_WATCH_DIR
      2. 常见安装位置（存在即用）
      3. 用户目录 %USERPROFILE%\\Music\\CloudMusic\\VipSongsDownload

    写成这样是为了不把某台机器的磁盘路径写死；目录不存在时程序不会崩，
    只是扫描不到文件而已。
    """
    env = os.environ.get("ZHUANZHUAN_WATCH_DIR")
    if env:
        return Path(env)
    home_music = Path.home() / "Music" / "CloudMusic" / "VipSongsDownload"
    for cand in (
        Path(r"D:\CloudMusic\VipSongsDownload"),
        Path(r"C:\CloudMusic\VipSongsDownload"),
        home_music,
    ):
        if cand.is_dir():
            return cand
    return home_music


WATCH_DIR = _default_watch_dir()
STATE_FILE = WATCH_DIR / ".ncm_auto_state.json"
LOG_FILE = WATCH_DIR / "ncm_auto_convert.log"
POLL_INTERVAL = 2
FILE_STABLE_WAIT = 3
DELETE_ORIGINAL = True
NETEASE_EXE = "cloudmusic.exe"
CHECK_PROCESS_INTERVAL = 5
STARTUP_TASK_NAME = "转转"


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_state():
    """加载状态，返回 {path: size} dict, failed dict"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            processed = data.get('processed', {})
            failed = data.get('failed', {})
            # 兼容旧格式: list -> dict
            if isinstance(processed, list):
                processed = {p: 0 for p in processed}
            return processed, failed
        except Exception:
            pass
    return {}, {}


def save_state(processed, failed):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'processed': processed,
                'failed': failed,
                'updated': datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def wait_file_stable(filepath, timeout=30):
    start = time.time()
    last_size = -1
    stable_count = 0
    while time.time() - start < timeout:
        try:
            size = os.path.getsize(filepath)
            if size == last_size and size > 0:
                stable_count += 1
                if stable_count >= FILE_STABLE_WAIT:
                    return True
            else:
                stable_count = 0
                last_size = size
        except OSError:
            return False
        time.sleep(1)
    return False


def scan_and_convert(processed, failed):
    """扫描目录并转换新文件，返回更新后的 processed, failed"""
    # 扫描所有支持的加密格式
    all_encrypted = []
    for ext in get_supported_extensions():
        all_encrypted.extend(WATCH_DIR.glob(f"*{ext}"))
    
    changed = False

    for encrypted_file in all_encrypted:
        file_key = str(encrypted_file.resolve())
        file_size = os.path.getsize(encrypted_file)

        # 检查是否已处理：同名且大小相同才算已处理
        if file_key in processed:
            if processed[file_key] == file_size:
                continue
            else:
                log(f"Re-downloaded: {encrypted_file.name} (size changed)")

        if file_key in failed:
            fail_time = failed[file_key]
            if datetime.now().timestamp() - fail_time < 300:
                continue

        if not wait_file_stable(encrypted_file):
            continue

        size_mb = file_size / (1024 * 1024)
        log(f"New file: {encrypted_file.name} ({size_mb:.1f} MB)")

        result = unlock_audio(encrypted_file, WATCH_DIR)

        if result['success']:
            log(f"  OK -> {result['output'].name} [{result.get('format', 'unknown')}]")
            processed[file_key] = file_size
            failed.pop(file_key, None)
            changed = True

            if DELETE_ORIGINAL:
                try:
                    encrypted_file.unlink()
                    log(f"  Deleted: {encrypted_file.name}")
                except Exception as e:
                    log(f"  Delete failed: {e}")
        else:
            err = result.get('error', 'Unknown')
            log(f"  FAIL -> {err}")
            failed[file_key] = datetime.now().timestamp()
            changed = True

    if changed:
        save_state(processed, failed)

    return processed, failed


def is_netease_running():
    try:
        r = subprocess.run(
            ['tasklist', '/FI', f'IMAGENAME eq {NETEASE_EXE}', '/NH'],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return NETEASE_EXE in r.stdout
    except Exception:
        return False


def run_normal_mode(reset=False):
    """普通后台模式：一直监视"""
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    log("Service started (normal mode)")

    if reset:
        processed, failed = set(), {}
        save_state(processed, failed)
        log("State cleared")
    else:
        processed, failed = load_state()

    log(f"Loaded: {len(processed)} processed, {len(failed)} failed")

    while True:
        try:
            processed, failed = scan_and_convert(processed, failed)
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log("Service stopped")
            break
        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(5)


def run_bind_mode():
    """绑定网易云模式：检测网易云启动时才开始监视"""
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    log("Service started (bind mode)")
    log(f"Monitoring process: {NETEASE_EXE}")

    processed, failed = load_state()
    netease_was_running = False

    while True:
        try:
            netease_running = is_netease_running()

            if netease_running and not netease_was_running:
                log("NetEase Cloud Music started, activating watcher...")
                netease_was_running = True

            if netease_running and netease_was_running:
                processed, failed = scan_and_convert(processed, failed)
                time.sleep(POLL_INTERVAL)
                continue

            if not netease_running and netease_was_running:
                log("NetEase Cloud Music closed, pausing watcher...")
                netease_was_running = False

            time.sleep(CHECK_PROCESS_INTERVAL)

        except KeyboardInterrupt:
            log("Service stopped")
            break
        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(5)


def install_startup():
    """注册 Windows 计划任务，开机自启"""
    import tempfile

    script_path = Path(__file__).resolve()
    python_path = Path(sys.executable).resolve()

    # 创建启动器 VBS（无窗口）
    vbs_path = Path(tempfile.gettempdir()) / "ncm_service_launcher.vbs"
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "\"{python_path}\" \"{script_path}\"", 0, False
Set WshShell = Nothing
'''
    with open(vbs_path, 'w', encoding='utf-8') as f:
        f.write(vbs_content)

    # 删除旧任务
    subprocess.run(
        ['schtasks', '/delete', '/tn', STARTUP_TASK_NAME, '/f'],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW
    )

    # 创建新任务：用户登录时启动，无窗口
    cmd = [
        'schtasks', '/create',
        '/tn', STARTUP_TASK_NAME,
        '/tr', f'"{vbs_path}"',
        '/sc', 'onlogon',
        '/rl', 'limited',
        '/f'
    ]

    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode == 0:
        print(f"[OK] 开机自启已注册: {STARTUP_TASK_NAME}")
        print(f"     启动器: {vbs_path}")
        print(f"     下次登录 Windows 时自动启动")
    else:
        print(f"[错误] 注册失败: {r.stderr}")


def uninstall_startup():
    r = subprocess.run(
        ['schtasks', '/delete', '/tn', STARTUP_TASK_NAME, '/f'],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW
    )
    if r.returncode == 0:
        print(f"[OK] 开机自启已移除: {STARTUP_TASK_NAME}")
    else:
        print(f"[信息] 任务不存在或已移除")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="NCM 后台自动转换服务")
    parser.add_argument('--bind', action='store_true', help='绑定网易云：只在网易云运行时工作')
    parser.add_argument('--reset', action='store_true', help='清除状态，重新处理所有文件')
    parser.add_argument('--install', action='store_true', help='注册开机自启')
    parser.add_argument('--uninstall', action='store_true', help='移除开机自启')
    args = parser.parse_args()

    if args.install:
        install_startup()
        return

    if args.uninstall:
        uninstall_startup()
        return

    if args.bind:
        run_bind_mode()
    else:
        run_normal_mode(reset=args.reset)


if __name__ == '__main__':
    main()
