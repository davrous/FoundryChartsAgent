FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --create-home charts && chown -R charts:charts /app
USER charts
ENV PORT=8088 HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 8088
CMD ["python", "main.py"]
