# 使用官方 Python 基础镜像
FROM python:3.10.13-slim

# 设置工作目录
WORKDIR /app

# 1. 关键步骤：安装 FFmpeg/FFprobe
# 这是视频分析的核心工具。Dockerfile 确保部署环境始终拥有它。
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 2. 复制依赖文件并安装 Python 库
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 复制应用代码
COPY . .

# 4. 暴露端口 (云服务通常使用 8080)
EXPOSE 8080

# 5. 运行应用
CMD ["python", "main.py"]
