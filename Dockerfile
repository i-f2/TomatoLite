FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# 骨架只含 config/（§0.1）；apps/ integrations/ mocks/ 存在后随 S0-4 追加 COPY
COPY manage.py ./
COPY config/ ./config/
EXPOSE 8000
