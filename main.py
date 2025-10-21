import os
import subprocess
import json
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from flask_cors import CORS # 导入 CORS

load_dotenv()

app = Flask(__name__)
# 启用 CORS，允许所有来源访问所有路由
CORS(app) 

# 配置上传文件夹，使用 /tmp 是 Docker 容器内的临时且安全的位置
UPLOAD_FOLDER = '/tmp/uploads'
# 确保在容器启动时这个文件夹存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True) 

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 最大 100MB

# --- FFprobe 核心函数 ---

def analyze_video(filepath):
    """使用 FFprobe 分析视频文件，返回音视频同步、码率等信息。"""
    try:
        # FFprobe 命令：静默模式，JSON格式输出，显示流信息
        command = [
            'ffprobe',
            '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams',
            '-show_format',
            filepath
        ]
        
        # 执行命令并捕获输出
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        
        # 提取关键信息
        video_stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'video'), None)
        audio_stream = next((s for s in data.get('streams', []) if s.get('codec_type') == 'audio'), None)

        report = {
            "status": "Success",
            "metadata": {
                "format_name": data.get('format', {}).get('format_name', 'N/A'),
                "duration": float(data.get('format', {}).get('duration', 0)),
                "bit_rate": int(data.get('format', {}).get('bit_rate', 0)),
            },
            "video_stream": {
                "codec": video_stream.get('codec_name', 'N/A') if video_stream else 'N/A',
                "resolution": f"{video_stream.get('width', 'N/A')}x{video_stream.get('height', 'N/A')}" if video_stream else 'N/A',
                "duration": float(video_stream.get('duration', 0)) if video_stream and video_stream.get('duration') else 0,
            },
            "audio_stream": {
                "codec": audio_stream.get('codec_name', 'N/A') if audio_stream else 'N/A',
                "channels": audio_stream.get('channels', 'N/A') if audio_stream else 'N/A',
                "duration": float(audio_stream.get('duration', 0)) if audio_stream and audio_stream.get('duration') else 0,
            },
            "sync_check": "N/A",
            "sync_details": {}
        }
        
        # 简单同步性检查
        if video_stream and audio_stream:
            v_dur = report['video_stream']['duration']
            a_dur = report['audio_stream']['duration']
            
            # 计算时长差异（毫秒）
            duration_diff_ms = abs(v_dur - a_dur) * 1000
            report['sync_details']['duration_diff_ms'] = round(duration_diff_ms, 2)
            
            # 设定同步阈值（例如：差异在 100ms 内视为同步）
            if duration_diff_ms < 100:
                report['sync_check'] = "同步良好 (Good Sync)"
            else:
                report['sync_check'] = "存在明显差异 (Noticeable Difference)"

        return report

    except subprocess.CalledProcessError as e:
        # FFprobe 命令执行失败
        return {
            "status": "FFprobe Error",
            "message": "FFprobe command failed to execute.",
            "error_details": e.stderr.strip()
        }
    except Exception as e:
        # 其他 Python 错误
        return {
            "status": "Internal Server Error",
            "message": str(e)
        }

# --- 路由 ---

@app.route('/', methods=['GET'])
def serve_index():
    """根路由：托管并返回index.html页面"""
    # Flask会自动返回index.html
    return send_from_directory('static', 'index.html')

@app.route('/ping', methods=['GET'])
def ping():
    """健康检查接口，检查 FFprobe 是否可用"""
    try:
        subprocess.run(['ffprobe', '-version'], check=True, capture_output=True)
        return jsonify({"status": "ok", "message": "FFprobe is installed and Flask is running."}), 200
    except subprocess.CalledProcessError:
        return jsonify({"status": "error", "message": "FFprobe is not accessible."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500


@app.route('/analyze', methods=['POST'])
def analyze():
    # 1. 检查是否有文件上传
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part in the request"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400

    temp_filepath = None
    try:
        # 2. 安全保存文件到临时路径
        filename = secure_filename(file.filename)
        temp_filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(temp_filepath)
        
        # 3. 调用核心分析函数
        analysis_report = analyze_video(temp_filepath)
        
        # 4. 返回 JSON 报告
        if analysis_report.get('status') == 'Success' or analysis_report.get('status') == 'FFprobe Error':
            # 即使 FFprobe 报错，也返回 JSON 报告（状态码 200 或 500 取决于您对 FFprobe 错误的定义）
            return jsonify(analysis_report), 200
        else:
            return jsonify(analysis_report), 500 # 其他服务器内部错误

    except Exception as e:
        # 捕获文件保存或任何未预料到的错误
        app.logger.error(f"Analysis error: {str(e)}")
        return jsonify({
            "status": "Internal Server Error", 
            "message": f"An unexpected error occurred during file processing: {str(e)}"
        }), 500
    finally:
        # 5. 确保删除临时文件
        if temp_filepath and os.path.exists(temp_filepath):
            try:
                os.remove(temp_filepath)
                # app.logger.info(f"Cleaned up file: {temp_filepath}") # 生产环境中可以启用日志
            except Exception as e:
                # app.logger.error(f"Error cleaning up file: {e}")
                pass # 忽略清理错误

if __name__ == '__main__':
    # 容器环境通常不设置 debug=True
    app.run(debug=False, host='0.0.0.0', port=8080)
