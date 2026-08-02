FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true
COPY . .
ENV PEAR_HOST=0.0.0.0 PEAR_PORT=8080 PEAR_DATA=/data
EXPOSE 8080
VOLUME ["/data"]
CMD ["python", "-m", "service.app"]
