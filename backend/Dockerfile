FROM python:3.12-slim

WORKDIR /app

# faiss-cpu（Milvus Lite 的检索内核）依赖 libgomp
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
# 兼容 Windows 检出的 CRLF，并确保入口脚本可执行
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh && mkdir -p /app/data

ENV PYTHONPATH=/app/src
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "ecommerce_cs.main:app", "--host", "0.0.0.0", "--port", "8000"]
