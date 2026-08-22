FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin memory-router
USER 10001

EXPOSE 8080

CMD ["memory-router"]
