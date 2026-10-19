# The Silver job: the same CLI a laptop runs, as the Fargate task's entry point.
#
# Code and contracts only. No data, no credential and no account value is ever part of the image;
# the task gets its catalog and landing location from its definition, and AWS access from its role.

FROM python:3.12-slim

# Fail fast and log immediately: a buffered stdout in a scheduled task means the log arrives after
# the interesting part is over.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not re-resolve them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 silver

# DuckDB reads landing from S3 through httpfs and the AWS credential chain. Installing both
# extensions here, as the user that runs the job, means a run never downloads one.
USER silver
RUN python -c "import duckdb; duckdb.sql('INSTALL httpfs'); duckdb.sql('INSTALL aws')"
USER root

COPY src/ ./src/
COPY contracts/ ./contracts/
RUN chown -R silver:silver /app

USER silver
ENTRYPOINT ["python", "-m", "src.silver.rebuild"]
