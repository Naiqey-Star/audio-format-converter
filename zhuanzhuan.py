#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一的多格式加密音频解密模块
============================
支持格式：
- NCM（网易云音乐）
- QMCv1/QMCv2（QQ音乐 .qmc* .mflac .mgg）
- KGM/VPR（酷狗音乐 .kgm .vpr）
- KWM（酷我音乐 .kwm）

依赖：libtakiyasha 2.1.1+
"""

import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from libtakiyasha.ncm import NCM
    from libtakiyasha.qmc import QMCv1, QMCv2
    from libtakiyasha.kgmvpr import KGMorVPR
    from libtakiyasha.kwm import KWM
    LIBTAKIYASHA_OK = True
except ImportError:
    LIBTAKIYASHA_OK = False
    print("[警告] libtakiyasha 未安装，部分格式解密不可用")

# 保留原有 NCM 解密作为备用
from ncm_decrypt import process_ncm as process_ncm_legacy

# 支持的格式映射
SUPPORTED_EXTENSIONS = {
    '.ncm': 'NCM',
    '.mflac': 'QMC',
    '.mgg': 'QMC',
    '.qmc0': 'QMC',
    '.qmc2': 'QMC',
    '.qmc3': 'QMC',
    '.qmcflac': 'QMC',
    '.qmcogg': 'QMC',
    '.kgm': 'KGM',
    '.kgma': 'KGM',
    '.vpr': 'KGM',
    '.kwm': 'KWM',
}

# 默认密钥（从旧版客户端提取，可能需要更新）
# 这些密钥可能因版本不同而失效，需要用户提供或从配置加载
QMC_DEFAULT_KEYS = {
    'simple_key': bytes.fromhex('94b26f4f8e6774b0b085b08f6e3b2b4a'),  # 示例密钥，可能需要替换
    'mix_key1': None,  # 需要用户提供
    'mix_key2': None,  # 需要用户提供
}


def detect_format(file_path: Path) -> str:
    """根据文件扩展名检测加密格式"""
    ext = file_path.suffix.lower()
    return SUPPORTED_EXTENSIONS.get(ext, 'UNKNOWN')


def _detect_audio_format(data: bytes) -> str:
    """检测解密后的音频格式"""
    if len(data) < 4:
        return 'unknown'
    
    magic = data[:4]
    
    # FLAC
    if magic == b'fLaC':
        return 'flac'
    
    # MP3 (MPEG Audio Frame)
    if magic[0] == 0xFF and (magic[1] & 0xE0) == 0xE0:
        return 'mp3'
    
    # MP3 with ID3 tag
    if magic[:3] == b'ID3':
        return 'mp3'
    
    # OGG
    if magic[:4] == b'OggS':
        return 'ogg'
    
    # WAV
    if magic[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return 'wav'
    
    # M4A
    if len(data) >= 8 and data[4:8] == b'ftyp':
        return 'm4a'
    
    return 'unknown'


def unlock_ncm(input_path: Path, output_dir: Path, **kwargs) -> Dict[str, Any]:
    """使用 libtakiyasha 解密 NCM 文件"""
    try:
        # 优先尝试 libtakiyasha
        ncm = NCM.from_file(input_path, core_key=kwargs.get('ncm_core_key'))
        audio_data = ncm.getvalue()
        
        # 检测输出格式
        fmt = _detect_audio_format(audio_data)
        if fmt == 'unknown':
            fmt = 'mp3'  # 默认
        
        # 生成输出文件名
        output_name = input_path.stem + '.' + fmt
        output_path = output_dir / output_name
        
        # 避免覆盖
        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{input_path.stem}_{counter}.{fmt}"
            counter += 1
        
        # 写入文件
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        
        return {
            'success': True,
            'output': output_path,
            'format': fmt,
            'method': 'libtakiyasha',
        }
    except Exception as e:
        # 回退到原有实现
        return process_ncm_legacy(input_path, output_dir)


def unlock_qmc(input_path: Path, output_dir: Path, **kwargs) -> Dict[str, Any]:
    """解密 QQ音乐 QMC 格式（.mflac/.mgg/.qmc*）"""
    if not LIBTAKIYASHA_OK:
        return {'success': False, 'error': 'libtakiyasha 未安装'}
    
    try:
        # 尝试 QMCv2（新格式）
        try:
            qmc = QMCv2.from_file(
                input_path,
                simple_key=kwargs.get('qmc_simple_key', QMC_DEFAULT_KEYS['simple_key']),
                mix_key1=kwargs.get('qmc_mix_key1', QMC_DEFAULT_KEYS['mix_key1']),
                mix_key2=kwargs.get('qmc_mix_key2', QMC_DEFAULT_KEYS['mix_key2']),
            )
        except ValueError:
            # 回退到 QMCv1（旧格式）
            qmc = QMCv1.from_file(input_path)
        
        audio_data = qmc.getvalue()
        fmt = _detect_audio_format(audio_data)
        if fmt == 'unknown':
            fmt = 'flac' if '.flac' in input_path.name.lower() else 'mp3'
        
        output_name = input_path.stem.rsplit('.', 1)[0] + '.' + fmt
        output_path = output_dir / output_name
        
        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{input_path.stem.rsplit('.', 1)[0]}_{counter}.{fmt}"
            counter += 1
        
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        
        return {
            'success': True,
            'output': output_path,
            'format': fmt,
            'method': 'libtakiyasha',
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def unlock_kgm(input_path: Path, output_dir: Path, **kwargs) -> Dict[str, Any]:
    """解密酷狗音乐 KGM/VPR 格式"""
    if not LIBTAKIYASHA_OK:
        return {'success': False, 'error': 'libtakiyasha 未安装'}
    
    try:
        kgm = KGMorVPR.from_file(input_path)
        audio_data = kgm.getvalue()
        
        fmt = _detect_audio_format(audio_data)
        if fmt == 'unknown':
            # 根据原文件名推断
            if '.flac' in input_path.name.lower():
                fmt = 'flac'
            else:
                fmt = 'mp3'
        
        output_name = input_path.name.split('.')[0] + '.' + fmt
        output_path = output_dir / output_name
        
        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{input_path.name.split('.')[0]}_{counter}.{fmt}"
            counter += 1
        
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        
        return {
            'success': True,
            'output': output_path,
            'format': fmt,
            'method': 'libtakiyasha',
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def unlock_kwm(input_path: Path, output_dir: Path, **kwargs) -> Dict[str, Any]:
    """解密酷我音乐 KWM 格式"""
    if not LIBTAKIYASHA_OK:
        return {'success': False, 'error': 'libtakiyasha 未安装'}
    
    try:
        kwm = KWM.from_file(input_path)
        audio_data = kwm.getvalue()
        
        fmt = _detect_audio_format(audio_data)
        if fmt == 'unknown':
            fmt = 'flac' if '.flac' in input_path.name.lower() else 'mp3'
        
        output_name = input_path.stem + '.' + fmt
        output_path = output_dir / output_name
        
        counter = 1
        while output_path.exists():
            output_path = output_dir / f"{input_path.stem}_{counter}.{fmt}"
            counter += 1
        
        with open(output_path, 'wb') as f:
            f.write(audio_data)
        
        return {
            'success': True,
            'output': output_path,
            'format': fmt,
            'method': 'libtakiyasha',
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def unlock_audio(input_path: Path, output_dir: Path, **kwargs) -> Dict[str, Any]:
    """
    统一解密入口
    自动检测格式并调用相应的解密函数
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    fmt = detect_format(input_path)
    
    if fmt == 'NCM':
        return unlock_ncm(input_path, output_dir, **kwargs)
    elif fmt == 'QMC':
        return unlock_qmc(input_path, output_dir, **kwargs)
    elif fmt == 'KGM':
        return unlock_kgm(input_path, output_dir, **kwargs)
    elif fmt == 'KWM':
        return unlock_kwm(input_path, output_dir, **kwargs)
    else:
        return {'success': False, 'error': f'不支持的格式: {input_path.suffix}'}


def get_supported_extensions() -> list:
    """返回所有支持的加密文件扩展名"""
    return list(SUPPORTED_EXTENSIONS.keys())


if __name__ == '__main__':
    # 命令行测试
    import argparse
    
    parser = argparse.ArgumentParser(description='多格式加密音频解密工具')
    parser.add_argument('files', nargs='+', help='加密音频文件路径')
    parser.add_argument('-o', '--output', default=None, help='输出目录')
    args = parser.parse_args()
    
    output_dir = Path(args.output) if args.output else None
    
    for file_path in args.files:
        file_path = Path(file_path)
        if not file_path.exists():
            print(f"[跳过] 文件不存在: {file_path}")
            continue
        
        out_dir = output_dir or file_path.parent
        print(f"解密: {file_path.name} ... ", end='')
        
        result = unlock_audio(file_path, out_dir)
        
        if result['success']:
            print(f"✓ -> {result['output'].name}")
        else:
            print(f"✗ {result.get('error', 'Unknown error')}")
