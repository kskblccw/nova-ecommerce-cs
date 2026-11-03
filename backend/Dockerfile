FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
# 兼容 Windows 检出的 CRLF，并确保入口脚本可执行
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

ENV PYTHONPATH=/app/src
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "ecommerce_cs.main:app", "--host", "0.0.0.0", "--port", "8000"]
