# crmeb-ai-mall AI 微服务镜像（窗口6：纳入 compose 一键部署）
# 构建：docker compose build ai（国内网络可用 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple）
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

COPY app ./app

EXPOSE 8000

# 配置经 compose env_file（宿主 ai-service/.env，由 .env.example 复制而来）注入进程环境，
# 真实 API key 只存在于宿主 .env，不进镜像不进仓库
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
