# hermes.Dockerfile
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y \
    curl git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash

ENV PATH="/root/.local/bin:${PATH}"

CMD ["sleep", "infinity"]