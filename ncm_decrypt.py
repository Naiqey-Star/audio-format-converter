#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
NCM 文件解密工具
将网易云音乐的 .ncm 文件解密为 MP3/FLAC
输出到 D:\工作\BGM
"""

import struct
import json
import base64
import os
import sys
import glob
import argparse
from pathlib import Path

try:
    from Crypto.Cipher import AES
except ImportError:
    print("[错误] 缺少依赖: pycryptodome")
    print("请运行: pip install pycryptodome")
    sys.exit(1)

try:
    from mutagen.flac import FLAC, Picture as FlacPicture
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, APIC
    MUTAGEN_OK = True
except ImportError:
    MUTAGEN_OK = False

# ===== AES 密钥 =====
CORE_KEY = bytes([
    0x68, 0x7A, 0x48, 0x52, 0x41, 0x6D, 0x73, 0x6F,
    0x35, 0x6B, 0x49, 0x6E, 0x62, 0x61, 0x78, 0x57
])
META_KEY = bytes([
    0x23, 0x31, 0x34, 0x6C, 0x6A, 0x6B, 0x5F, 0x21,
    0x5C, 0x5D, 0x26, 0x30, 0x55, 0x3C, 0x27, 0x28
])

DEFAULT_OUTPUT = None  # 默认输出到输入文件同目录


def _resolve_ffmpeg_path() -> str:
    """
    解析 ffmpeg 可执行文件路径。
    优先级：
      1. 环境变量 NCM_FFMPEG_PATH
      2. PyInstaller 打包时嵌入的 ffmpeg（sys._MEIPASS/ffmpeg.exe）
      3. 系统路径中的 ffmpeg
      4. 默认硬编码路径
    """
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
    import shutil
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg

    # 4. 默认路径（兜底）
    return r'C:\Program Files\FFmpeg\bin\ffmpeg.exe'


def unpad(data):
    """PKCS7 去填充（安全版本）"""
    if not data:
        return data
    padding_len = data[-1]
    if padding_len > 16 or padding_len == 0:
        return data  # 无效的填充，返回原数据
    # 验证填充字节是否一致
    if data[-padding_len:] == bytes([padding_len] * padding_len):
        return data[:-padding_len]
    return data  # 填充不一致，返回原数据


def aes_decrypt(data, key):
    """AES-128-ECB 解密（自动处理填充）"""
    # 确保数据长度是 16 的倍数
    block_size = 16
    padding_needed = block_size - (len(data) % block_size)
    if padding_needed != block_size:
        data = data + bytes([padding_needed] * padding_needed)
    cipher = AES.new(key, AES.MODE_ECB)
    decrypted = cipher.decrypt(data)
    return unpad(decrypted)


def build_key_box(key):
    """构建修改版 RC4 key box"""
    box = list(range(256))
    key_len = len(key)
    j = 0
    for i in range(256):
        j = (box[i] + j + key[i % key_len]) & 0xFF
        box[i], box[j] = box[j], box[i]
    return box


def decrypt_audio(data, key_box):
    """
    解密音频数据 (NCM 修改版 RC4)
    正确的算法：静态 box，复合索引
    """
    result = bytearray()
    for i, byte in enumerate(data):
        j = (i + 1) & 0xFF
        k = (key_box[j] + key_box[(j + key_box[j]) & 0xFF]) & 0xFF
        result.append(byte ^ key_box[k])
    return bytes(result)


def verify_audio(data):
    """验证解密后的音频数据格式"""
    if len(data) < 4:
        return False, "数据太短"

    magic = data[:4]

    # MP3 (MPEG Audio Frame)
    if magic[0] == 0xFF and (magic[1] & 0xE0) == 0xE0:
        return True, "MP3"

    # MP3 with ID3 tag
    if magic[:3] == b'ID3':
        return True, "MP3(ID3)"

    # FLAC
    if magic == b'fLaC':
        return True, "FLAC"

    # OGG
    if magic[:4] == b'OggS':
        return True, "OGG"

    # M4A
    if magic[4:8] == b'ftyp':
        return True, "M4A"

    # WAV
    if magic[:4] == b'RIFF':
        return True, "WAV"

    return False, f"未知格式 (magic: {magic.hex()})"


def process_ncm(input_path, output_dir, keep_cover=False):
    """
    处理单个 NCM 文件

    Returns:
        dict: {success, output, format, valid, meta, cover, error}
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(input_path, 'rb') as f:
            # === 文件头 ===
            header = f.read(8)
            if header != b'CTENFDAM':
                # 某些旧版本可能有不同的魔数，尝试回退到 3 字节模式
                f.seek(0)
                header = f.read(3)
                if header != b'CTE':
                    print(f"[警告] 未知的 NCM 魔数: {header.hex()}，继续尝试...")
                f.seek(2, 1)  # 旧格式：跳过 2 bytes
            else:
                # 新格式：跳过 2 bytes
                f.seek(2, 1)

            # === Key 区域 ===
            key_len = struct.unpack('<I', f.read(4))[0]
            key_data = bytearray(f.read(key_len))

            # XOR 0x64
            for i in range(len(key_data)):
                key_data[i] ^= 0x64

            # AES-128-ECB 解密 (CORE_KEY)
            decrypted_key = aes_decrypt(bytes(key_data), CORE_KEY)
            # 去掉前 17 bytes ("neteasecloudmusic" 变体前缀)
            core_key = decrypted_key[17:]

            # 调试：检查 core_key 是否有效
            if not core_key:
                raise ValueError(f"core_key is empty, decrypted_key={decrypted_key[:50]!r}, len={len(decrypted_key)}")

            # === Meta 区域 ===
            meta_len = struct.unpack('<I', f.read(4))[0]
            meta_info = {}

            if meta_len > 0:
                meta_data = bytearray(f.read(meta_len))

                # XOR 0x63
                for i in range(len(meta_data)):
                    meta_data[i] ^= 0x63

                # 去掉前缀 "163 key(Don't modify):"
                meta_prefix = bytes(meta_data)[:22]
                meta_str = bytes(meta_data)[22:]

                # Base64 解码
                try:
                    meta_b64 = base64.b64decode(meta_str)
                except Exception as e:
                    raise ValueError(f"Meta base64 decode failed: {e}, prefix={meta_prefix!r}, meta_str={meta_str[:50]!r}")

                # AES-128-ECB 解密 (META_KEY)
                meta_decrypted = aes_decrypt(meta_b64, META_KEY)

                # JSON 解析
                try:
                    meta_text = meta_decrypted.decode('utf-8')
                    # NCM meta 格式可能有前缀如 "music:" 或 "3g:"
                    # 需要找到 JSON 的起始位置
                    json_start = meta_text.find('{')
                    if json_start >= 0:
                        meta_text = meta_text[json_start:]
                    meta_info = json.loads(meta_text)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    text_preview = meta_text[:100] if 'meta_text' in locals() else 'N/A'
                    raise ValueError(f"Meta JSON parse failed: {e}, decrypted={meta_decrypted[:100]!r}, text={text_preview!r}")

            # === CRC32 (4 字节) ===
            f.read(4)

            # === 跳过 8 字节 (album_id + 其他字段) ===
            f.read(8)

            # === 封面图 ===
            # 查找 JPEG/PNG 魔数来确定封面图起始位置
            image_data = None
            peek = f.read(20)
            f.seek(-20, 1)  # 回退
            
            # 查找 JPEG 魔数 (ffd8ff) 或 PNG 魔数 (89504e47)
            jpeg_pos = peek.find(b'\xff\xd8\xff')
            png_pos = peek.find(b'\x89PNG')
            
            if jpeg_pos >= 0:
                # 跳过 gap 字节
                f.seek(jpeg_pos, 1)
                # 读取 JPEG 数据直到 ffd9 结束标记
                image_start = f.tell()
                # 读取足够大的块来查找结束标记
                image_block = f.read(2 * 1024 * 1024)  # 最多 2MB
                jpeg_end = image_block.find(b'\xff\xd9')
                if jpeg_end > 0:
                    image_data = image_block[:jpeg_end + 2]
                    f.seek(image_start + jpeg_end + 2)
            elif png_pos >= 0:
                # 跳过 gap 字节
                f.seek(png_pos, 1)
                # PNG 没有简单的结束标记，读取直到遇到音频数据
                # 这里简化处理：读取直到 IEND chunk
                image_start = f.tell()
                image_block = f.read(2 * 1024 * 1024)
                iend_pos = image_block.find(b'IEND')
                if iend_pos > 0:
                    # IEND chunk 后还有 4 bytes CRC
                    image_data = image_block[:iend_pos + 8]
                    f.seek(image_start + iend_pos + 8)

            # === 音频数据 ===
            audio_data = bytearray(f.read())

            if len(audio_data) == 0:
                return {
                    'success': False,
                    'error': '音频数据为空',
                    'input': input_path
                }

            # 解密音频
            key_box = build_key_box(core_key)
            decrypted_audio = decrypt_audio(audio_data, key_box)
            valid, fmt_detected = verify_audio(decrypted_audio)

            # 确定输出格式 (优先使用 meta 中的格式)
            fmt = meta_info.get('format', '')
            if not fmt:
                # 根据检测到的格式确定扩展名
                if fmt_detected == "FLAC":
                    fmt = "flac"
                elif fmt_detected in ("MP3", "MP3(ID3)"):
                    fmt = "mp3"
                elif fmt_detected == "OGG":
                    fmt = "ogg"
                elif fmt_detected == "M4A":
                    fmt = "m4a"
                elif fmt_detected == "WAV":
                    fmt = "wav"
                else:
                    fmt = "mp3"  # 默认

            # 生成输出文件名
            # 优先使用歌曲名
            song_name = meta_info.get('musicName', '')
            artist_name = ''
            if meta_info.get('artist'):
                if isinstance(meta_info['artist'], list) and len(meta_info['artist']) > 0:
                    artist_name = meta_info['artist'][0][0] if isinstance(meta_info['artist'][0], list) else str(meta_info['artist'][0])
                else:
                    artist_name = str(meta_info['artist'])

            if song_name:
                # 清理文件名中的非法字符
                safe_name = str(song_name).replace('/', '-').replace('\\', '-').replace(':', '-').replace('*', '-').replace('?', '-').replace('"', '-').replace('<', '-').replace('>', '-').replace('|', '-')
                if artist_name:
                    safe_artist = str(artist_name).replace('/', '-').replace('\\', '-').replace(':', '-').replace('*', '-').replace('?', '-').replace('"', '-').replace('<', '-').replace('>', '-').replace('|', '-')
                    output_name = f"{safe_artist} - {safe_name}.{fmt}"
                else:
                    output_name = f"{safe_name}.{fmt}"
            else:
                output_name = input_path.stem + '.' + fmt

            output_path = output_dir / output_name

            # 避免文件名冲突
            counter = 1
            original_output_path = output_path
            while output_path.exists():
                stem = original_output_path.stem
                output_path = output_dir / f"{stem}_{counter}.{fmt}"
                counter += 1

            # 写入文件
            with open(output_path, 'wb') as out:
                out.write(decrypted_audio)

            # 如果是 MP3 格式，转换为 FLAC 以获得更好的封面支持
            if fmt == 'mp3' and image_data:
                import subprocess
                import tempfile

                # 临时 MP3 文件
                temp_mp3 = output_path.with_suffix('.tmp.mp3')
                output_path.rename(temp_mp3)

                # 获取 ffmpeg 路径
                ffmpeg_exe = _resolve_ffmpeg_path()

                # 用完整版 ffmpeg 转换为 FLAC
                flac_path = output_path.with_suffix('.flac')
                try:
                    result = subprocess.run(
                        [ffmpeg_exe, '-y', '-i', str(temp_mp3), '-c:a', 'flac', str(flac_path)],
                        capture_output=True, text=True, check=True
                    )
                    temp_mp3.unlink()  # 删除临时 MP3
                    output_path = flac_path
                    fmt = 'flac'
                    fmt_detected = 'FLAC (converted from MP3)'
                except subprocess.CalledProcessError as e:
                    # 转换失败，恢复原 MP3
                    print(f"[警告] MP3 转 FLAC 失败: {e.stderr[-200:] if e.stderr else 'N/A'}")
                    flac_path.unlink(missing_ok=True)
                    temp_mp3.rename(output_path)

            # 嵌入封面图到音频文件中
            if MUTAGEN_OK and image_data:
                try:
                    mime = 'image/jpeg'
                    if image_data[:4] == b'\x89PNG':
                        mime = 'image/png'
                    elif image_data[:2] == b'\xff\xd8':
                        mime = 'image/jpeg'

                    if fmt == 'flac' or fmt_detected.startswith('FLAC'):
                        audio = FLAC(str(output_path))
                        pic = FlacPicture()
                        pic.type = 3
                        pic.mime = mime
                        pic.desc = 'Cover'
                        pic.data = image_data
                        audio.add_picture(pic)
                        audio.save()
                    elif fmt == 'mp3':
                        audio = MP3(str(output_path))
                        if audio.tags is None:
                            audio.add_tags()
                        audio.tags['APIC'] = APIC(
                            encoding=3,
                            mime=mime,
                            type=3,
                            desc='Cover',
                            data=image_data
                        )
                        audio.save()
                except Exception as e:
                    print(f"[警告] 封面嵌入失败: {e}")

            # 保存封面图 (可选)
            cover_path = None
            if keep_cover and image_data:
                # 尝试检测图片格式
                if image_data[:2] == b'\xff\xd8':
                    cover_ext = 'jpg'
                elif image_data[:4] == b'\x89PNG':
                    cover_ext = 'png'
                else:
                    cover_ext = 'jpg'

                cover_path = output_dir / (output_path.stem + f'_cover.{cover_ext}')
                with open(cover_path, 'wb') as cf:
                    cf.write(image_data)

            return {
                'success': True,
                'output': output_path,
                'format': fmt,
                'valid': valid,
                'detected': fmt_detected,
                'meta': meta_info,
                'cover': cover_path,
                'input': input_path
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'input': input_path
        }


def main():
    parser = argparse.ArgumentParser(
        description='NCM 文件解密工具 - 将网易云音乐 .ncm 转换为 MP3/FLAC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python ncm_decrypt.py song.ncm
  python ncm_decrypt.py song1.ncm song2.ncm
  python ncm_decrypt.py C:\\Users\\Music\\*.ncm
  python ncm_decrypt.py --output E:\\Music song.ncm
  python ncm_decrypt.py --cover song.ncm
        """
    )
    parser.add_argument('files', nargs='+', help='NCM 文件路径 (支持通配符 *)')
    parser.add_argument('-o', '--output', default=None, help='输出目录 (默认: 输入文件同目录)')
    parser.add_argument('-c', '--cover', action='store_true', help='同时保存封面图')
    parser.add_argument('-v', '--verbose', action='store_true', help='显示详细信息')

    args = parser.parse_args()
    global_output = Path(args.output) if args.output else None

    print("=" * 60)
    print(" NCM 文件解密工具")
    print("=" * 60)
    print(f"输出目录: {global_output if global_output else '输入文件同目录'}")
    print()

    success_count = 0
    fail_count = 0

    def get_output_dir(file_path):
        if global_output:
            global_output.mkdir(parents=True, exist_ok=True)
            return global_output
        d = file_path.parent
        d.mkdir(parents=True, exist_ok=True)
        return d

    for pattern in args.files:
        matched_files = list(glob.glob(pattern)) if '*' in pattern or '?' in pattern else [pattern]

        for file_path in matched_files:
            file_path = Path(file_path)

            if not file_path.exists():
                print(f"[跳过] 文件不存在: {file_path}")
                continue

            if file_path.is_dir():
                # 递归处理目录
                ncm_files = list(file_path.rglob('*.ncm'))
                print(f"[目录] {file_path} - 找到 {len(ncm_files)} 个 NCM 文件")
                for ncm_file in ncm_files:
                    result = process_ncm(ncm_file, get_output_dir(ncm_file), args.cover)
                    if result['success']:
                        success_count += 1
                        status = "成功" if result['valid'] else "可能失败"
                        song = result['meta'].get('musicName', '') if result['meta'] else ''
                        print(f"  [{status}] {ncm_file.name}")
                        print(f"         -> {result['output'].name}")
                        if args.verbose and result['meta']:
                            artist = result['meta'].get('artist', '')
                            album = result['meta'].get('album', '')
                            print(f"         歌手: {artist}, 专辑: {album}, 格式: {result['detected']}")
                    else:
                        fail_count += 1
                        print(f"  [失败] {ncm_file.name}: {result['error']}")
                continue

            if file_path.suffix.lower() != '.ncm':
                print(f"[跳过] 非 NCM 文件: {file_path}")
                continue

            result = process_ncm(file_path, get_output_dir(file_path), args.cover)

            if result['success']:
                success_count += 1
                status = "成功" if result['valid'] else "可能失败"
                song = result['meta'].get('musicName', '') if result['meta'] else ''
                print(f"[{status}] {file_path.name}")
                print(f"       -> {result['output'].name}")
                if song:
                    print(f"       歌曲: {song}")
                print(f"       格式: {result['detected']}")
                if args.verbose and result['meta']:
                    artist = result['meta'].get('artist', '')
                    album = result['meta'].get('album', '')
                    print(f"       歌手: {artist}, 专辑: {album}")
                if result['cover']:
                    print(f"       封面: {result['cover'].name}")
            else:
                fail_count += 1
                print(f"[失败] {file_path.name}: {result['error']}")

            print()

    print("=" * 60)
    print(f"处理完成: 成功 {success_count} 个, 失败 {fail_count} 个")
    print(f"输出目录: {global_output if global_output else '输入文件同目录'}")
    print("=" * 60)


if __name__ == '__main__':
    main()
