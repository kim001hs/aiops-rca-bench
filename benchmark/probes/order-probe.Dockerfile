FROM python:3.12-alpine
WORKDIR /app
COPY order_probe.py .
USER 10001
EXPOSE 8080
CMD ["python", "order_probe.py"]
