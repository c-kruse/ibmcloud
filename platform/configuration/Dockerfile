FROM quay.io/openshift-release-dev/ocp-v4.0-art-dev@sha256:84b0aa6c19cdedba485212a5b599bdb9fd6975d5c808aaf4f115500b782ee600 AS cli
FROM registry.access.redhat.com/ubi9/python-312@sha256:5b0c67feb3f2e5637c6301ea22e2839d402dd71d88c3cec8d1fd278ce0f9e69a
USER 0
COPY --from=cli /usr/bin/oc /usr/local/bin/kubectl
RUN pip install --no-cache-dir ansible-core==2.18.11 kubernetes==34.1.0 PyYAML==6.0.3 requests==2.32.5 && \
    ansible-galaxy collection install kubernetes.core:6.2.0 skupper.v2:2.2.1 -p /opt/collections && \
    chmod -R a+rX /opt/collections
ENV ANSIBLE_COLLECTIONS_PATH=/tmp/skupper/tests/e2e/collections:/opt/collections
ENV HOME=/tmp/home
USER 1001
