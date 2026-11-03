
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 先安装依赖，利用 Docker 构建缓存
COPY requirements.txt ./requirements.txt

RUN python -m pip install --no-cache-dir \
    -r requirements.txt

# 复制项目代码与配置
COPY . .

# 使用非 root 用户运行
RUN useradd --create-home appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
