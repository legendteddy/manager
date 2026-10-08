# syntax=docker/dockerfile:1.7
# For a release candidate, supply PYTHON_IMAGE as an organization-approved immutable digest.
ARG PYTHON_IMAGE=python:3.14-slim

FROM ${PYTHON_IMAGE} AS builder
WORKDIR /src
COPY runtime/python /src/runtime/python
RUN python -m pip install --no-cache-dir --disable-pip-version-check setuptools==84.0.0 \
    && python -m pip wheel --disable-pip-version-check --no-deps --no-build-isolation --wheel-dir /wheels /src/runtime/python

FROM ${PYTHON_IMAGE} AS runtime
ARG MANAGER_UID=10001
ARG MANAGER_GID=10001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/nonexistent \
    MANAGER_DEPLOY_DATA_DIR=/var/lib/manager \
    MANAGER_DEPLOY_TMP_DIR=/var/run/manager \
    MANAGER_DEPLOY_SECRETS_DIR=/run/secrets
RUN groupadd --gid "${MANAGER_GID}" manager \
    && useradd --uid "${MANAGER_UID}" --gid "${MANAGER_GID}" --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin manager \
    && install -d -o manager -g manager -m 0750 /var/lib/manager /var/lib/manager/state /var/run/manager /run/secrets
COPY --from=builder /wheels/ /tmp/wheels/
RUN python -m pip install --no-cache-dir --disable-pip-version-check --no-deps /tmp/wheels/*.whl \
    && rm -rf /tmp/wheels
USER ${MANAGER_UID}:${MANAGER_GID}
WORKDIR /var/lib/manager
ENTRYPOINT ["manager-deployment"]
CMD ["validate-config"]
