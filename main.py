import os
import subprocess
import json
import uuid
import tempfile
from flask import Flask, jsonify, request
from werkzeug.utils import secure_filename

# =================================================================
# Flask 配置
# =================================================================
app = Flask(__name__)
# 修复中文显示为 Unicode (Day 1 修复)
app.json.ensure_ascii = False

# 允许上传的文件扩展名
ALLOWED_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv'}
# 设置最大上传文件大小 (例如：100 MB)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024

# =================================================================
# 辅助函数
# =================================================================

def allowed_file(filename):
    """检查文件扩展名是否在允许的列表中"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def run_ffprobe(filepath):
    """
    使用 ffprobe 命令分析视频文件，并以 JSON 格式返回结果。
    
    参数:
        filepath (str): 视频文件的完整路径。
        
    返回:
        dict: ffprobe 输出的 JSON 数据。
    """
    # -v error: 只输出错误信息
    # -select_streams v: 仅选择视频流 (用于获取视频元数据)
    # -show_format: 显示容器格式信息
    # -show_streams: 显示所有流的信息 (音频和视频)
    # -of json: 以 JSON 格式输出
    command = [
        'ffprobe',
        '-v', 'error',
        '-show_format',
        '-show_streams',
        '-of', 'json',
        filepath
    ]
    
    try:
        # 执行 ffprobe 命令
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        app.logger.error(f"FFprobe 执行失败: {e.stderr}")
        return {"error": "FFprobe analysis failed", "details": e.stderr}
    except json.JSONDecodeError:
        app.logger.error("FFprobe 返回了无效的 JSON")
        return {"error": "Invalid JSON response from FFprobe"}

def analyze_sync_and_metadata(ffprobe_data):
    """
    解析 ffprobe 数据，提取元信息并判断音视频同步。
    
    参数:
        ffprobe_data (dict): ffprobe 命令返回的 JSON 数据。
        
    返回:
        dict: 结构化的分析报告。
    """
    # 错误检查
    if 'error' in ffprobe_data:
        return {"sync_status": "分析失败", "message": ffprobe_data['error'], "details": ffprobe_data.get('details', '')}

    # 提取 streams 和 format
    streams = ffprobe_data.get('streams', [])
    format_info = ffprobe_data.get('format', {})

    video_stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    audio_stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)

    # 1. 检查音视频同步 (仅检查是否存在和起始时间)
    sync_status = "OK: 音视频同步存在"
    sync_message = "文件包含音视频流。"
    
    if not video_stream:
        sync_status = "警告: 缺少视频流"
        sync_message = "文件不包含视频轨道。"
    elif not audio_stream:
        sync_status = "警告: 缺少音频流"
        sync_message = "文件不包含音频轨道。"
    else:
        # 尝试获取起始时间戳 (start_time)
        v_start = float(video_stream.get('start_time', 0))
        a_start = float(audio_stream.get('start_time', 0))
        
        # 检查起始时间差，超过 0.1 秒通常视为不同步 (经验值)
        if abs(v_start - a_start) > 0.1:
            sync_status = "警告: 音视频起始时间不同步"
            sync_message = f"视频起始时间差: {abs(v_start - a_start):.3f} 秒。建议检查音轨是否延迟。"
        
    # 2. 提取文件元信息
    report = {
        "sync_status": sync_status,
        "sync_message": sync_message,
        "filename": format_info.get('filename', '未知'),
        "duration": float(format_info.get('duration', 0)), # 文件时长 (秒)
        "size_bytes": int(format_info.get('size', 0)), # 文件大小 (字节)
        "format_name": format_info.get('format_name', '未知'), # 容器格式 (mp4, mov等)
        "bit_rate": int(format_info.get('bit_rate', 0)), # 总比特率
    }
    
    # 3. 提取编解码和异常信息
    if video_stream:
        report["video"] = {
            "codec": video_stream.get('codec_name', '未知'),
            "width": video_stream.get('width'),
            "height": video_stream.get('height'),
            "avg_frame_rate": video_stream.get('avg_frame_rate', '未知'),
            "codec_tag_string": video_stream.get('codec_tag_string', '未知')
        }
    if audio_stream:
        report["audio"] = {
            "codec": audio_stream.get('codec_name', '未知'),
            "sample_rate": audio_stream.get('sample_rate'),
            "channels": audio_stream.get('channels'),
            "channel_layout": audio_stream.get('channel_layout', '未知')
        }
        
    # 4. 简化的异常判断 (例如：流缺失)
    if not video_stream or not audio_stream:
        report["codec_exception"] = "警告: 缺少关键流"
    else:
        report["codec_exception"] = "OK"

    return report

# =================================================================
# 路由定义
# =================================================================

@app.route('/ping', methods=['GET'])
def ping():
    """
    健康检查接口 (Day 1 任务)。
    检查 FFprobe (视频分析的核心工具) 是否可用。
    """
    ffprobe_status = "OK"
    try:
        # 尝试运行一个简单的 ffprobe 命令来验证其可用性
        subprocess.run(['ffprobe', '-version'], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        ffprobe_status = "错误: FFprobe 未找到或执行失败。"
        
    return jsonify({
        "status": "服务器正在运行 (Pong!)",
        "ffprobe_检查结果": ffprobe_status
    })

@app.route('/analyze', methods=['POST'])
def analyze_video():
    """
    核心接口 (Day 2 任务)。
    接收视频文件，调用 FFprobe 分析，返回结构化 JSON 报告。
    """
    # 1. 检查请求中是否包含文件
    if 'video' not in request.files:
        return jsonify({"message": "请求中必须包含 'video' 文件"}), 400
    
    file = request.files['video']

    # 2. 检查文件名是否为空或扩展名不被允许
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({"message": "文件名无效或文件类型不被支持"}), 400

    # 3. 存储文件到临时目录
    # 使用 secure_filename 保证文件名安全
    # 使用 tempfile 确保文件保存在系统临时目录，且安全唯一
    filename = secure_filename(file.filename)
    unique_filename = f"{uuid.uuid4()}_{filename}"
    
    temp_dir = tempfile.gettempdir()
    filepath = os.path.join(temp_dir, unique_filename)
    
    analysis_report = {}
    
    try:
        # 保存文件到临时路径
        file.save(filepath)
        app.logger.info(f"文件已保存到: {filepath}")

        # 4. 调用 FFprobe 进行原始分析
        raw_analysis_data = run_ffprobe(filepath)
        
        # 5. 结构化分析报告
        analysis_report = analyze_sync_and_metadata(raw_analysis_data)
        
        return jsonify({
            "status": "分析完成",
            "summary": analysis_report,
            # "full_ffprobe_data": raw_analysis_data # 调试时可以返回完整的原始数据
        })

    except Exception as e:
        app.logger.error(f"处理文件时发生未预期的错误: {e}")
        return jsonify({"status": "内部服务器错误", "message": str(e)}), 500
        
    finally:
        # 6. 清理：无论成功与否，都要删除临时文件
        if os.path.exists(filepath):
            os.remove(filepath)
            app.logger.info(f"临时文件已删除: {filepath}")


if __name__ == '__main__':
    # 从环境变量获取端口，如果未设置，则默认使用 8080 (适用于云部署)
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
