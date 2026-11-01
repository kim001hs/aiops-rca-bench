FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt app.py ./
RUN pip install --no-cache-dir -r requirements.txt
USER 65532:65532
EXPOSE 8080
CMD ["python", "app.py"]
