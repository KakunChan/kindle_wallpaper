ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app
COPY app.py /app/app.py
RUN chmod 755 /app && chmod 644 /app/app.py

ENV HOST=0.0.0.0
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

USER nobody
CMD ["python", "/app/app.py"]
