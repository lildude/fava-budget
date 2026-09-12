# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS build

WORKDIR /build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends bison build-essential flex \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir "build>=1.3" \
    && python -m build \
    && python -m pip wheel --wheel-dir /wheelhouse dist/*.whl

FROM scratch AS artifacts

COPY --from=build /build/dist /

FROM python:${PYTHON_VERSION}-slim AS runtime

COPY --from=build /wheelhouse /wheelhouse

RUN python -m pip install --no-cache-dir --no-index --find-links=/wheelhouse fava-budget \
    && rm -rf /wheelhouse

EXPOSE 5000

ENTRYPOINT ["fava", "-H", "0.0.0.0", "-p", "5000"]
CMD ["/data/ledger.bean"]

FROM python:${PYTHON_VERSION}-slim AS dev

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends bison build-essential flex \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY tests ./tests

RUN python -m pip install --no-cache-dir --editable ".[dev]"

CMD ["bash"]

FROM runtime AS test

COPY tests /tests

RUN bean-check /tests/fixtures/budget.bean \
    && python -m unittest discover -s /tests -v -b

FROM runtime AS default
