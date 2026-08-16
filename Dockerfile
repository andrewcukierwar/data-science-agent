FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 1000 analysis \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin analysis \
    && pip install --no-cache-dir \
        duckdb \
        matplotlib \
        numpy \
        pandas \
        plotly \
        pyarrow \
        scipy \
        statsmodels

ENV MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /workspace
USER analysis

CMD ["python"]
