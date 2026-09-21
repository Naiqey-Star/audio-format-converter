#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NCM 转换工具 - 独立打包版本
自动检测运行环境，找到正确的 ffmpeg 路径
"""

import os
import sys
from pathlib import Path

def get_ffmpeg_path():
    """获取 ffmpeg 可执行文件路径"""
    # 1. 检查是否在打包环境中（exe 同目录）
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的环境
        exe_dir = Path(sys.executable).parent
        ffmpeg_path = exe_dir / "ffmpeg.exe"
        if ffmpeg_path.exists():
            return str(ffmpeg_path)
    
    # 2. 检查开发环境（scripts 目录的上级 tools/bin）
    script_dir = Path(__file__).parent
    dev_ffmpeg = script_dir.parent / "tools" / "bin" / "ffmpeg.exe"
    if dev_ffmpeg.exists():
        return str(dev_ffmpeg)
    
    # 3. 检查系统 PATH 中的 ffmpeg
    for path_dir in os.environ.get('PATH', '').split(os.pathsep):
        ffmpeg_in_path = Path(path_dir) / "ffmpeg.exe"
        if ffmpeg_in_path.exists():
            return str(ffmpeg_in_path)
    
    # 4. 检查常见安装位置
    common_paths = [
        r"C:\Program Files\FFmpeg\bin\ffmpeg.exe",
        r"C:\FFmpeg\bin\ffmpeg.exe",
        r"D:\FFmpeg\bin\ffmpeg.exe",
    ]
    for path in common_paths:
        if os.path.exists(path):
            return path
    
    # 5. 最后尝试直接调用（依赖系统 PATH）
    return "ffmpeg"


def main():
    """主入口"""
    # 设置 ffmpeg 路径到环境变量，供 ncm_decrypt.py 使用
    ffmpeg_path = get_ffmpeg_path()
    os.environ['NCM_FFMPEG_PATH'] = ffmpeg_path
    
    # 导入并运行后台服务
    from ncm_bg_service import main as service_main
    service_main()


if __name__ == '__main__':
    main()
