1. 停止容器
docker stop <container_name_or_id>
docker rm <container_name_or_id>
2. 重新构建镜像
docker build --no-cache -t video-analyzer-backend .
3. 重新运行容器
docker run -d -p 5000:8080 --name video-backend video-analyzer-backend python main.py
