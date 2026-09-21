#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
转转 — 桌面托盘应用
===================================
系统托盘常驻应用，自动监控多平台目录并将加密音频文件转换为 MP3/FLAC。

支持平台：
  - 网易云音乐（.ncm）
  - QQ音乐（.mflac/.mgg/.qmc0/.qmc2/.qmc3/.qmcflac/.qmcogg）
  - 酷狗音乐（.kgm/.kgma/.vpr）
  - 酷我音乐（.kwm）

功能:
  - 系统托盘常驻，关闭窗口不退出
  - 极简设置窗口：多平台目录配置 + 开机自启动
  - 后台线程实时扫描并转换加密音频文件
  - 配置持久化存储（JSON）

技术栈: PySide6 (Qt6)
"""

import sys
import os
import json
import time
import logging
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox,
    QFileDialog, QSystemTrayIcon, QMenu, QMessageBox,
    QGroupBox, QSizePolicy, QGridLayout, QFrame,
)
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt, QThread, Signal, QObject

# ---------------------------------------------------------------------------
# 路径设置 —— 确保能 import 同目录下的 zhuanzhuan
# ---------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    APP_DIR = Path(sys.executable).parent.resolve()
else:
    APP_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(APP_DIR))

# 导入多格式解密模块
from zhuanzhuan import unlock_audio

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
APP_NAME = "转转"
APP_VERSION = "2.0.0"

# 配置存储目录（%APPDATA%/转转/）
CONFIG_DIR = Path(os.environ.get(
    'APPDATA',
    str(Path.home() / 'AppData' / 'Roaming')
)) / '转转'

# 轮询间隔（秒）
POLL_INTERVAL = 3.0

# 文件写入等待：文件大小稳定检查的间隔与超时
FILE_STABLE_INTERVAL = 0.5
FILE_STABLE_TIMEOUT = 10.0

# 平台配置：定义每个平台的显示名称和对应加密文件扩展名
PLATFORM_CONFIG: Dict[str, Dict[str, Any]] = {
    'netease': {
        'name': '网易云音乐',
        'extensions': ['.ncm'],
    },
    'qq': {
        'name': 'QQ音乐',
        'extensions': ['.mflac', '.mgg', '.qmc0', '.qmc2', '.qmc3',
                        '.qmcflac', '.qmcogg'],
    },
    'kugou': {
        'name': '酷狗音乐',
        'extensions': ['.kgm', '.kgma', '.vpr'],
    },
    'kuwo': {
        'name': '酷我音乐',
        'extensions': ['.kwm'],
    },
}

# 全局样式表
GLOBAL_STYLESHEET = """
QWidget {
    background-color: #f0f0f0;
    font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;
    font-size: 13px;
    color: #000000;
}
QGroupBox {
    font-weight: bold;
    color: #37474f;
    border: 1px solid #c0c0c0;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}
QLineEdit {
    border: 1px solid #b0b0b0;
    border-radius: 4px;
    padding: 4px 8px;
    background-color: #ffffff;
    color: #000000;
}
QLineEdit:focus {
    border-color: #4CAF50;
}
QLineEdit::placeholder {
    color: #78909c;
}
QPushButton {
    border: 1px solid #b0b0b0;
    border-radius: 4px;
    padding: 4px 12px;
    background-color: #e8e8e8;
    color: #263238;
}
QPushButton:hover {
    background-color: #dcdcdc;
}
QPushButton:pressed {
    background-color: #cccccc;
}
QCheckBox {
    color: #000000;
}
"""

# 深色模式样式表
DARK_MODE_STYLESHEET = """
QWidget {
    background-color: #2b2b2b;
    font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif;
    font-size: 13px;
    color: #e0e0e0;
}
QGroupBox {
    font-weight: bold;
    color: #b0bec5;
    border: 1px solid #555555;
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}
QLineEdit {
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 8px;
    background-color: #3c3c3c;
    color: #e0e0e0;
}
QLineEdit:focus {
    border-color: #4CAF50;
}
QLineEdit::placeholder {
    color: #888888;
}
QPushButton {
    border: 1px solid #555555;
    border-radius: 4px;
    padding: 4px 12px;
    background-color: #3c3c3c;
    color: #e0e0e0;
}
QPushButton:hover {
    background-color: #4a4a4a;
}
QPushButton:pressed {
    background-color: #555555;
}
QCheckBox {
    color: #e0e0e0;
}
"""


# ===== 日志 =====
def _setup_logging() -> None:
    """初始化日志：同时输出到文件和控制台。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = CONFIG_DIR / "tray_app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


logger = logging.getLogger(__name__)


# ===== 工具函数 =====
def wait_file_stable(file_path: Path, interval: float = FILE_STABLE_INTERVAL,
                     timeout: float = FILE_STABLE_TIMEOUT) -> bool:
    """等待文件写入完成（文件大小在 interval 秒内不再变化）。

    Args:
        file_path: 待检查的文件路径。
        interval: 两次检查之间的间隔（秒）。
        timeout: 最大等待时间（秒）。

    Returns:
        True 表示文件已稳定，False 表示超时或文件不存在。
    """
    if not file_path.exists():
        return False

    prev_size = -1
    elapsed = 0.0
    while elapsed < timeout:
        try:
            current_size = os.path.getsize(file_path)
        except OSError:
            return False
        if current_size == prev_size and prev_size > 0:
            return True
        prev_size = current_size
        time.sleep(interval)
        elapsed += interval
    return False


def _resolve_ffmpeg_path() -> Optional[str]:
    """解析 ffmpeg 路径：环境变量 > PyInstaller _MEIPASS > 系统 PATH > 默认路径。"""
    # 1. 环境变量
    env_path = os.environ.get('NCM_FFMPEG_PATH')
    if env_path and os.path.isfile(env_path):
        return env_path

    # 2. PyInstaller 打包后的临时目录
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            bundled = os.path.join(meipass, 'ffmpeg.exe')
            if os.path.isfile(bundled):
                return bundled

    # 3. 系统 PATH
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg

    # 4. 默认路径
    default_path = r'C:\Program Files\FFmpeg\bin\ffmpeg.exe'
    if os.path.isfile(default_path):
        return default_path

    return None


def normalize_to_flac(input_path: Path) -> Optional[Path]:
    """使用 ffmpeg 将非 MP3 音频转换为 FLAC 格式。

    如果输入文件已经是 MP3 或 FLAC，则不做转换。
    对于其他格式（OGG、WAV、M4A 等），调用 ffmpeg 转换为 FLAC。

    Args:
        input_path: 输入的音频文件路径。

    Returns:
        转换后的文件路径；如果转换失败则返回 None。
    """
    suffix = input_path.suffix.lower()

    # MP3 保持不变
    if suffix == '.mp3':
        return input_path

    # 已经是 FLAC 则无需转换
    if suffix == '.flac':
        return input_path

    # 检查 ffmpeg 是否可用（支持打包后路径）
    ffmpeg_path = _resolve_ffmpeg_path()
    if not ffmpeg_path:
        logger.warning("ffmpeg 未找到，无法转换 %s 为 FLAC，保留原文件", suffix)
        return input_path

    output_path = input_path.with_suffix('.flac')

    # 避免覆盖
    counter = 1
    while output_path.exists():
        output_path = input_path.parent / f"{input_path.stem}_{counter}.flac"
        counter += 1

    try:
        cmd = [
            ffmpeg_path,
            '-y',              # 覆盖输出
            '-i', str(input_path),
            '-f', 'flac',
            '-vn',             # 不处理视频流
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0 and output_path.exists():
            # 转换成功，删除原始非 MP3/FLAC 文件
            try:
                input_path.unlink()
            except OSError as exc:
                logger.warning("删除中间文件失败: %s", exc)
            logger.info("已转换 %s -> %s", input_path.name, output_path.name)
            return output_path
        else:
            stderr = result.stderr.decode('utf-8', errors='replace')[:200]
            logger.error("ffmpeg 转换失败: %s", stderr)
            # 清理可能的不完整输出
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg 转换超时: %s", input_path.name)
        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        return None
    except Exception as exc:
        logger.error("ffmpeg 调用异常: %s", exc)
        return None


# ===== 应用配置 =====
class AppConfig:
    """应用配置的持久化管理（JSON 文件）。

    新配置格式：
        {
            "platforms": {
                "netease": {"name": "网易云音乐", "dir": ""},
                "qq": {"name": "QQ音乐", "dir": ""},
                "kugou": {"name": "酷狗音乐", "dir": ""},
                "kuwo": {"name": "酷我音乐", "dir": ""}
            },
            "auto_start": false,
            "first_run": true
        }
    """

    def __init__(self) -> None:
        self.config_file: Path = CONFIG_DIR / "config.json"
        self._data: dict = self._default_config()
        self.load()

    @staticmethod
    def _default_config() -> dict:
        """生成默认配置。"""
        platforms = {}
        for key, cfg in PLATFORM_CONFIG.items():
            platforms[key] = {"name": cfg['name'], "dir": ""}
        return {
            "platforms": platforms,
            "auto_start": False,
            "first_run": True,
        }

    def load(self) -> None:
        """从 JSON 文件加载配置；文件不存在则使用默认值。

        向后兼容：如果检测到旧格式的 watch_dir 字段，
        自动迁移到 netease 平台的 dir 字段。
        """
        if not self.config_file.exists():
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception as exc:
            logger.warning("配置文件读取失败，使用默认值: %s", exc)
            return

        # 向后兼容迁移：旧配置只有一个 watch_dir
        if "watch_dir" in loaded and "platforms" not in loaded:
            old_dir = loaded.get("watch_dir", "")
            if old_dir:
                logger.info("检测到旧配置，迁移 watch_dir -> netease 平台")
                loaded["platforms"] = self._default_config()["platforms"]
                loaded["platforms"]["netease"]["dir"] = old_dir
            # 清除旧字段
            loaded.pop("watch_dir", None)

        # 合并已加载数据（保留新增平台的默认值）
        if "platforms" in loaded:
            default_platforms = self._default_config()["platforms"]
            for key in default_platforms:
                if key not in loaded["platforms"]:
                    loaded["platforms"][key] = default_platforms[key]
        else:
            loaded["platforms"] = self._default_config()["platforms"]

        self._data.update(loaded)

    def save(self) -> None:
        """将配置写入 JSON 文件。"""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error("配置文件保存失败: %s", exc)

    # --- 属性访问 ---
    @property
    def platforms(self) -> Dict[str, Dict[str, str]]:
        """返回平台配置字典。"""
        return self._data.get("platforms", self._default_config()["platforms"])

    @platforms.setter
    def platforms(self, value: Dict[str, Dict[str, str]]) -> None:
        self._data["platforms"] = value

    def get_platform_dir(self, platform_key: str) -> str:
        """获取指定平台的监控目录。"""
        return self.platforms.get(platform_key, {}).get("dir", "")

    def set_platform_dir(self, platform_key: str, directory: str) -> None:
        """设置指定平台的监控目录。"""
        if platform_key in self._data.get("platforms", {}):
            self._data["platforms"][platform_key]["dir"] = directory

    def get_active_platforms(self) -> List[Tuple[str, str, List[str]]]:
        """返回已配置目录的平台列表。

        Returns:
            [(platform_key, directory, [extensions]), ...]
        """
        active = []
        for key, pconfig in self.platforms.items():
            d = pconfig.get("dir", "").strip()
            if d:
                exts = PLATFORM_CONFIG.get(key, {}).get("extensions", [])
                active.append((key, d, exts))
        return active

    @property
    def auto_start(self) -> bool:
        return self._data.get("auto_start", False)

    @auto_start.setter
    def auto_start(self, value: bool) -> None:
        self._data["auto_start"] = value

    @property
    def first_run(self) -> bool:
        return self._data.get("first_run", True)

    @first_run.setter
    def first_run(self, value: bool) -> None:
        self._data["first_run"] = value

    # --- 开机自启动 ---
    def set_auto_start(self, enabled: bool) -> None:
        """通过 Windows 注册表 Run 键控制开机自启动。

        如果打包为 .exe，则启动 .exe 自身；
        否则通过 VBS 无窗口启动 Python 脚本。
        """
        import winreg

        exe_path = sys.executable
        if getattr(sys, "frozen", False):
            cmd = f'"{exe_path}"'
        else:
            vbs_path = CONFIG_DIR / "startup_launcher.vbs"
            self._write_startup_vbs(vbs_path, exe_path)
            cmd = f'wscript.exe "{vbs_path}"'

        reg_key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        )
        try:
            if enabled:
                winreg.SetValueEx(reg_key, APP_NAME, 0, winreg.REG_SZ, cmd)
                logger.info("开机自启动已启用")
            else:
                try:
                    winreg.DeleteValue(reg_key, APP_NAME)
                except FileNotFoundError:
                    pass
                logger.info("开机自启动已禁用")
        finally:
            winreg.CloseKey(reg_key)

        self.auto_start = enabled
        self.save()

    @staticmethod
    def _write_startup_vbs(vbs_path: Path, python_exe: str) -> None:
        """生成一个 VBS 脚本来无窗口启动 Python 脚本。"""
        script = str(APP_DIR / "ncm_tray_app.py")
        content = (
            'Set WshShell = CreateObject("WScript.Shell")\n'
            f'WshShell.Run """{python_exe}"" ""{script}""", 0, False\n'
            "Set WshShell = Nothing\n"
        )
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(content)


# ===== 图标加载 =====
def create_app_icon() -> QIcon:
    """加载应用图标。优先使用打包后的图标，否则使用开发目录图标。"""
    icon_paths = [
        getattr(sys, '_MEIPASS', None) and Path(sys._MEIPASS) / "NCM-icon-192.png",
        APP_DIR / "NCM-icon-192.png",
    ]

    for icon_path in icon_paths:
        if icon_path and icon_path.exists():
            return QIcon(str(icon_path))

    logger.warning("图标文件未找到，使用默认图标")
    return QIcon()


# ===== 后台监控线程 =====
class MonitorWorker(QThread):
    """后台监控线程 —— 多平台目录监控与加密音频转换。

    遍历所有已配置的平台，扫描其目录中对应格式的加密文件，
    使用 zhuanzhuan.unlock_audio() 进行解密，并对非 MP3 输出
    通过 ffmpeg 转换为 FLAC。
    """

    file_converted = Signal(str, str)   # (原始文件名, 输出文件名)
    file_failed = Signal(str, str)      # (文件名, 错误信息)
    status_changed = Signal(str)        # 状态文本

    def __init__(self, platforms_config: Dict[str, Dict[str, Any]],
                 parent: QObject = None) -> None:
        """初始化监控线程。

        Args:
            platforms_config: 平台配置字典，格式为
                {"platform_key": {"name": ..., "dir": ..., "extensions": [...]}}
        """
        super().__init__(parent)
        self._platforms: Dict[str, Dict[str, Any]] = platforms_config
        self._running: bool = True
        self._config_changed: bool = False

        # 每个平台独立维护处理状态
        # {platform_key: {"processed": {file_key: size}, "failed": {file_key: timestamp}}}
        self._state: Dict[str, Dict] = {}
        for key in self._platforms:
            self._state[key] = {"processed": {}, "failed": {}}

    def stop(self) -> None:
        """请求线程停止。"""
        self._running = False

    def update_config(self, platforms_config: Dict[str, Dict[str, Any]]) -> None:
        """动态更新平台配置（线程在下次循环时感知变更）。"""
        self._platforms = platforms_config
        # 确保新平台有状态条目
        for key in platforms_config:
            if key not in self._state:
                self._state[key] = {"processed": {}, "failed": {}}
        self._config_changed = True

    def run(self) -> None:
        """线程入口：持续扫描所有已配置平台的目录直到 stop() 被调用。"""
        self._running = True
        logger.info("监控线程启动")

        while self._running:
            self._config_changed = False

            # 收集活跃平台（有目录配置的）
            active_platforms = []
            for key, pcfg in self._platforms.items():
                directory = pcfg.get("dir", "").strip()
                if directory:
                    active_platforms.append((key, pcfg))

            if not active_platforms:
                self.status_changed.emit("未配置监控目录")
                self._sleep(POLL_INTERVAL)
                continue

            active_names = ", ".join(
                pcfg.get("name", key) for _, pcfg in active_platforms
            )
            self.status_changed.emit(f"监控中: {active_names}")

            # 对每个活跃平台执行一次扫描
            for key, pcfg in active_platforms:
                if not self._running or self._config_changed:
                    break
                directory = pcfg.get("dir", "").strip()
                extensions = pcfg.get("extensions", [])
                if not directory or not extensions:
                    continue
                try:
                    self._scan_platform_once(key, Path(directory), extensions)
                except Exception as exc:
                    logger.error("扫描平台 %s 异常: %s", key, exc)

            self._sleep(POLL_INTERVAL)

        self.status_changed.emit("已停止")
        logger.info("监控线程已退出")

    def _scan_platform_once(self, platform_key: str, watch_dir: Path,
                            extensions: List[str]) -> None:
        """扫描指定平台目录一次，处理新增/变更的加密文件。

        Args:
            platform_key: 平台标识。
            watch_dir: 监控目录。
            extensions: 该平台对应的加密文件扩展名列表。
        """
        if not watch_dir.exists():
            return

        state = self._state.get(platform_key, {"processed": {}, "failed": {}})
        processed: dict = state["processed"]
        failed: dict = state["failed"]

        # 收集所有匹配的文件
        files_to_check: List[Path] = []
        for ext in extensions:
            files_to_check.extend(watch_dir.glob(f"*{ext}"))

        for enc_file in files_to_check:
            if not self._running:
                break

            file_key = str(enc_file.resolve())

            try:
                file_size = os.path.getsize(enc_file)
            except OSError:
                continue

            # 已处理且大小未变 → 跳过
            if file_key in processed and processed[file_key] == file_size:
                continue

            # 失败后 300 秒内不重试
            if file_key in failed:
                fail_time = failed[file_key]
                if datetime.now().timestamp() - fail_time < 300:
                    continue

            # 等待文件写入完成
            if not wait_file_stable(enc_file):
                continue

            size_mb = file_size / (1024 * 1024)
            logger.info("[%s] 发现文件: %s (%.1f MB)",
                        platform_key, enc_file.name, size_mb)

            # 调用统一解密函数
            result = self._process_file(enc_file, watch_dir, platform_key)

            if result.get("success"):
                output_path = result["output"]
                logger.info("[%s] 转换成功: %s -> %s",
                            platform_key, enc_file.name, output_path.name)
                processed[file_key] = file_size
                failed.pop(file_key, None)

                # 删除原始加密文件
                try:
                    enc_file.unlink()
                    logger.info("[%s] 已删除原文件: %s",
                                platform_key, enc_file.name)
                except Exception as exc:
                    logger.warning("[%s] 删除原文件失败: %s",
                                   platform_key, exc)

                song_name = result.get("song_name", enc_file.stem)
                self.file_converted.emit(song_name, output_path.name)
            else:
                err = result.get("error", "Unknown")
                logger.warning("[%s] 转换失败: %s — %s",
                               platform_key, enc_file.name, err)
                failed[file_key] = datetime.now().timestamp()
                self.file_failed.emit(enc_file.name, err)

        # 持久化状态
        self._state[platform_key] = {
            "processed": processed,
            "failed": failed,
        }

    def _process_file(self, input_path: Path, output_dir: Path,
                      platform_key: str) -> Dict[str, Any]:
        """处理单个加密文件：解密 + 格式标准化。

        1. 调用 zhuanzhuan.unlock_audio() 解密
        2. 如果输出不是 MP3，通过 ffmpeg 转为 FLAC

        Args:
            input_path: 加密文件路径。
            output_dir: 输出目录。
            platform_key: 平台标识（用于日志）。

        Returns:
            包含 success/output/error/song_name 的结果字典。
        """
        try:
            result = unlock_audio(input_path, output_dir)
        except Exception as exc:
            return {"success": False, "error": f"解密异常: {exc}"}

        if not result.get("success"):
            return result

        output_path = result.get("output")
        if not output_path:
            return {"success": False, "error": "解密成功但无输出文件"}

        # 格式标准化：非 MP3 转 FLAC
        fmt = result.get("format", "unknown")
        if fmt != "mp3":
            normalized = normalize_to_flac(output_path)
            if normalized:
                output_path = normalized
                fmt = "flac"
            # 如果 normalize 返回 None（ffmpeg 失败），保留原文件

        return {
            "success": True,
            "output": output_path,
            "format": fmt,
            "song_name": input_path.stem,
        }

    def _sleep(self, seconds: float) -> None:
        """可中断的睡眠，每 0.5 秒检查一次停止标志。"""
        elapsed = 0.0
        while elapsed < seconds and self._running:
            time.sleep(min(0.5, seconds - elapsed))
            elapsed += 0.5


# ===== 设置窗口 =====
class SettingsWindow(QWidget):
    """极简设置窗口：多平台目录配置 + 开机自启动。"""

    def __init__(
        self,
        config: AppConfig,
        on_config_changed: Optional[callable] = None,
        parent: QWidget = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._on_config_changed = on_config_changed
        self._dir_edits: Dict[str, QLineEdit] = {}
        self._init_ui()
        self._load_from_config()

    def _init_ui(self) -> None:
        """初始化界面布局与控件。"""
        self.setWindowTitle(APP_NAME)
        self.setFixedSize(520, 380)
        self.setWindowFlags(
            self.windowFlags()
            & ~Qt.WindowContextHelpButtonHint
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(10)

        # ---- 标题 ----
        title_label = QLabel(APP_NAME)
        title_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #263238;"
        )
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)

        # ---- 监控目录设置 ----
        dir_group = QGroupBox("监控目录")
        dir_group_layout = QVBoxLayout(dir_group)
        dir_group_layout.setSpacing(6)

        # 提示文字
        hint = QLabel("支持网易云/QQ音乐/酷狗/酷我 加密音频自动转换")
        hint.setStyleSheet("color: #455a64; font-size: 11px; margin-bottom: 4px;")
        hint.setWordWrap(True)
        dir_group_layout.addWidget(hint)

        # 平台网格布局
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)  # 输入框列可拉伸

        for row_idx, (key, pcfg) in enumerate(PLATFORM_CONFIG.items()):
            platform_name = pcfg['name']

            # 平台名称标签
            name_label = QLabel(platform_name)
            name_label.setStyleSheet("font-weight: bold; color: #000000;")
            name_label.setFixedWidth(80)
            name_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(name_label, row_idx, 0)

            # 目录输入框
            dir_edit = QLineEdit()
            dir_edit.setPlaceholderText("点击浏览选择下载目录...")
            dir_edit.setMinimumWidth(240)
            grid.addWidget(dir_edit, row_idx, 1)
            self._dir_edits[key] = dir_edit

            # 浏览按钮
            browse_btn = QPushButton("浏览")
            browse_btn.setFixedWidth(56)
            browse_btn.setFixedHeight(28)
            browse_btn.clicked.connect(
                lambda checked, k=key: self._on_browse(k)
            )
            grid.addWidget(browse_btn, row_idx, 2)

        dir_group_layout.addLayout(grid)
        main_layout.addWidget(dir_group)

        # ---- 基本设置 ----
        auto_start_row = QHBoxLayout()
        auto_start_row.setContentsMargins(4, 0, 4, 0)

        self._auto_start_cb = QCheckBox("开机自动启动")
        auto_start_row.addWidget(self._auto_start_cb)
        auto_start_row.addStretch()

        main_layout.addLayout(auto_start_row)

        # ---- 分割线 ----
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #c0c0c0; max-height: 1px;")
        main_layout.addWidget(line)

        # ---- 底部按钮 ----
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_save = QPushButton("保存")
        btn_save.setFixedWidth(100)
        btn_save.setFixedHeight(32)
        btn_save.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "font-weight: bold; border: none; border-radius: 4px; "
            "font-size: 13px; }"
            "QPushButton:hover { background-color: #45a049; }"
            "QPushButton:pressed { background-color: #3d8b40; }"
        )
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(100)
        btn_close.setFixedHeight(32)
        btn_close.clicked.connect(self._on_close)
        btn_row.addWidget(btn_close)

        main_layout.addLayout(btn_row)
        main_layout.addStretch()

    def _load_from_config(self) -> None:
        """从配置对象加载值到界面控件。"""
        for key, edit in self._dir_edits.items():
            dir_value = self._config.get_platform_dir(key)
            edit.setText(dir_value)

        self._auto_start_cb.blockSignals(True)
        self._auto_start_cb.setChecked(self._config.auto_start)
        self._auto_start_cb.blockSignals(False)

    def _on_browse(self, platform_key: str) -> None:
        """弹出目录选择对话框。

        Args:
            platform_key: 对应的平台标识。
        """
        edit = self._dir_edits.get(platform_key)
        if not edit:
            return

        current = edit.text().strip()
        if not current or not Path(current).exists():
            current = str(Path.home())
        folder = QFileDialog.getExistingDirectory(
            self, f"选择{PLATFORM_CONFIG[platform_key]['name']}下载目录", current
        )
        if folder:
            edit.setText(folder)

    def _on_save(self) -> None:
        """保存所有平台目录设置。"""
        new_platforms = {}
        has_any_dir = False

        for key, pcfg in PLATFORM_CONFIG.items():
            edit = self._dir_edits.get(key)
            dir_value = edit.text().strip() if edit else ""

            new_platforms[key] = {
                "name": pcfg['name'],
                "dir": dir_value,
            }
            if dir_value:
                has_any_dir = True

        # 验证至少有一个目录
        if not has_any_dir:
            QMessageBox.warning(
                self, "提示",
                "请至少选择一个平台的监控目录"
            )
            return

        # 验证每个填写的目录是否存在
        for key, pcfg in new_platforms.items():
            dir_value = pcfg["dir"]
            if dir_value and not Path(dir_value).exists():
                reply = QMessageBox.question(
                    self,
                    "目录不存在",
                    f"{pcfg['name']} 的目录不存在：\n{dir_value}\n是否创建？",
                    QMessageBox.Yes | QMessageBox.No,
                )
                if reply == QMessageBox.Yes:
                    try:
                        Path(dir_value).mkdir(parents=True, exist_ok=True)
                    except Exception as exc:
                        QMessageBox.warning(self, "错误", f"创建失败:\n{exc}")
                        return
                else:
                    return

        # 保存配置
        self._config.platforms = new_platforms
        self._config.first_run = False
        self._config.save()

        # 通知主程序配置已变更
        if self._on_config_changed:
            self._on_config_changed()

        logger.info("配置已保存")
        self.hide()

    def _on_close(self) -> None:
        """关闭窗口（隐藏而非销毁）。"""
        self.hide()

    def closeEvent(self, event) -> None:
        """重写关闭事件：隐藏窗口而不是退出程序。"""
        event.ignore()
        self.hide()


# ===== 托盘应用控制器 =====
class TrayApp(QObject):
    """托盘应用主控制器。

    管理：系统托盘图标 / 右键菜单 / 设置窗口 / 后台监控线程。
    """

    def __init__(self) -> None:
        super().__init__()
        self._config = AppConfig()
        self._icon: QIcon = create_app_icon()
        self._worker: Optional[MonitorWorker] = None
        self._tray_icon: Optional[QSystemTrayIcon] = None
        self._settings_window: Optional[SettingsWindow] = None

    def start(self) -> None:
        """初始化并启动所有组件。"""
        logger.info("=== %s v%s 启动 ===", APP_NAME, APP_VERSION)

        # 1. 构建监控线程的平台配置
        platforms_for_worker = self._build_worker_platforms()

        # 2. 启动后台监控线程
        self._start_monitor(platforms_for_worker)

        # 3. 创建设置窗口
        self._settings_window = SettingsWindow(
            config=self._config,
            on_config_changed=self._on_config_changed,
        )

        # 4. 创建系统托盘
        self._create_tray_icon()

        # 5. 首次运行提示
        if self._config.first_run:
            active = self._config.get_active_platforms()
            if active:
                dir_list = ", ".join(
                    f"{name}: {d}" for _, d, _ in active
                    for name in [PLATFORM_CONFIG.get(_, {}).get("name", _)]
                )
            else:
                dir_list = "未配置"
            self._tray_icon.showMessage(
                APP_NAME,
                f"程序正在后台运行\n监控目录: {dir_list}\n右键托盘图标可进行设置",
                QSystemTrayIcon.Information,
                5000,
            )
            self._config.first_run = False
            self._config.save()

        # 6. 启动时显示设置窗口
        self._show_settings()

    def _build_worker_platforms(self) -> Dict[str, Dict[str, Any]]:
        """从 AppConfig 构建传给 MonitorWorker 的平台配置。

        Returns:
            {platform_key: {"name": ..., "dir": ..., "extensions": [...]}}
        """
        result = {}
        for key, pcfg in self._config.platforms.items():
            platform_def = PLATFORM_CONFIG.get(key, {})
            result[key] = {
                "name": pcfg.get("name", key),
                "dir": pcfg.get("dir", ""),
                "extensions": platform_def.get("extensions", []),
            }
        return result

    def _create_tray_icon(self) -> None:
        """创建系统托盘图标与右键菜单。"""
        self._tray_icon = QSystemTrayIcon(self._icon, self)
        self._tray_icon.setToolTip(f"{APP_NAME} — 监控中")

        menu = QMenu()

        action_settings = QAction("设置", menu)
        action_settings.triggered.connect(self._show_settings)
        menu.addAction(action_settings)

        menu.addSeparator()

        action_quit = QAction("退出", menu)
        action_quit.triggered.connect(self._quit_app)
        menu.addAction(action_quit)

        self._tray_icon.setContextMenu(menu)

        self._tray_icon.activated.connect(self._on_tray_activated)
        self._tray_icon.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """托盘图标被激活（双击打开设置）。"""
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_settings()

    def _show_settings(self) -> None:
        """显示设置窗口。"""
        if self._settings_window:
            self._settings_window.show()
            self._settings_window.raise_()
            self._settings_window.activateWindow()

    def _start_monitor(self, platforms_config: Dict[str, Dict[str, Any]]) -> None:
        """创建并启动后台监控线程。"""
        self._worker = MonitorWorker(platforms_config)
        self._worker.file_converted.connect(self._on_file_converted)
        self._worker.file_failed.connect(self._on_file_failed)
        self._worker.status_changed.connect(self._on_status_changed)
        self._worker.start()
        logger.info("监控线程已启动")

    def _on_config_changed(self) -> None:
        """用户在设置中变更了平台配置。"""
        logger.info("用户更新了平台配置")
        if self._worker:
            new_config = self._build_worker_platforms()
            self._worker.update_config(new_config)

    def _on_file_converted(self, name: str, output: str) -> None:
        """文件转换成功的回调。"""
        logger.info("转换成功: %s -> %s", name, output)
        if self._tray_icon:
            self._tray_icon.showMessage(
                "转换成功",
                f"{name}\n→ {output}",
                QSystemTrayIcon.Information,
                3000,
            )

    def _on_file_failed(self, name: str, error: str) -> None:
        """文件转换失败的回调。"""
        logger.warning("转换失败: %s — %s", name, error)
        if self._tray_icon:
            self._tray_icon.showMessage(
                "转换失败",
                f"{name}\n错误: {error}",
                QSystemTrayIcon.Warning,
                3000,
            )

    def _on_status_changed(self, status: str) -> None:
        """监控状态变化的回调。"""
        if self._tray_icon:
            self._tray_icon.setToolTip(f"{APP_NAME} — {status}")

    def _quit_app(self) -> None:
        """完全退出应用。"""
        logger.info("正在退出...")

        if self._worker:
            self._worker.stop()
            self._worker.wait(5000)
            if self._worker.isRunning():
                logger.warning("监控线程未能在 5 秒内退出，强制终止")
                self._worker.terminate()

        if self._tray_icon:
            self._tray_icon.hide()

        if self._settings_window:
            self._settings_window.close()

        QApplication.quit()
        logger.info("=== 应用已退出 ===")


# ===== 深色模式检测 =====
def is_dark_mode() -> bool:
    """检测 Windows 是否启用了深色模式。
    
    通过读取注册表 HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize
    中的 AppsUseLightTheme 值来判断。0 表示深色模式，1 表示浅色模式。
    
    Returns:
        True 表示深色模式，False 表示浅色模式。
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        # 如果读取失败，默认使用浅色模式
        return False


# ===== 入口 =====
def main() -> None:
    """应用入口。"""
    _setup_logging()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    
    # 检测深色模式并应用对应的样式
    if is_dark_mode():
        app.setStyleSheet(DARK_MODE_STYLESHEET)
        logger.info("检测到深色模式，已应用深色模式样式")
    else:
        app.setStyleSheet(GLOBAL_STYLESHEET)
        logger.info("检测到浅色模式，已应用浅色模式样式")

    tray_app = TrayApp()
    tray_app.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
