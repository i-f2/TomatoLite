FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# S0-4：apps/ 落地（§0.1 八 app）；integrations/ 在 S0-4 Step 4 建，mocks/ 自 M1 起追加
COPY manage.py ./
COPY config/ ./config/
COPY apps/ ./apps/
COPY integrations/ ./integrations/
EXPOSE 8000
