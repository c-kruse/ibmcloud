FROM registry.k8s.io/kubectl:v1.35.0 AS kubectl
FROM docker.io/library/python:3.13-slim
RUN pip install --no-cache-dir PyYAML==6.0.2
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY update.py /opt/updater/update.py
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 1001
ENTRYPOINT ["python3", "/opt/updater/update.py"]
