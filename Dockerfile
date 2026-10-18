# The staging directory is assembled from allowlisted runtime files only.
FROM --platform=linux/amd64 node:24.10.0-bookworm-slim@sha256:b8d2197aff9129d16c801a3e3e1b2a873c4946480f5a310f38056df2268c38d9 AS node
FROM --platform=linux/amd64 golang:1.25.1-bookworm@sha256:c423747fbd96fd8f0b1102d947f51f9b266060217478e5f9bf86f145969562ee AS go
RUN go install golang.org/x/tools/gopls@v0.20.0
FROM --platform=linux/amd64 python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep curl ca-certificates iptables \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=go /usr/local/go /usr/local/go
COPY --from=go /go/bin/gopls /usr/local/bin/gopls
COPY --from=go /root/.cache/go-build /tmp/go-build
ENV PATH="/usr/local/go/bin:/opt/node-tools/node_modules/.bin:${PATH}" GOTOOLCHAIN=local \
    GOCACHE=/tmp/go-build GOPATH=/opt/go-modules PYTHONDONTWRITEBYTECODE=1
COPY engine-requirements.lock /opt/engine-requirements.lock
COPY runtime-node /opt/node-tools
RUN pip install --no-cache-dir -r /opt/engine-requirements.lock pytest==8.4.2 pytest-json-report==1.5.0 click==8.5.0 pytest-cov==7.0.0 \
    && npm ci --prefix /opt/node-tools --ignore-scripts \
    && npm cache clean --force
RUN ln -s /usr/local/go/bin/go /usr/local/bin/go && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt \
    && ln -s /opt/node-tools/node_modules/.bin/pyright /usr/local/bin/pyright \
    && ln -s /opt/node-tools/node_modules/.bin/pyright-langserver /usr/local/bin/pyright-langserver \
    && ln -s /opt/node-tools/node_modules/.bin/typescript-language-server /usr/local/bin/typescript-language-server \
    && printf '/task/src\n/task\n' > /usr/local/lib/python3.12/site-packages/benchmark-task.pth
COPY public /opt/benchmark-public
COPY engine /opt/engine
RUN cp /opt/benchmark-public/dotenv_cli.py /usr/local/bin/dotenv && chmod +x /usr/local/bin/dotenv
RUN useradd --create-home --uid 1000 bench && mkdir -p /task /ipc /opt/benchmark /opt/go-modules \
    && chown -R bench:bench /task /ipc /opt/engine /opt/go-modules /tmp
ENV PYTHONPATH="/opt/engine:/task/src:/task"
WORKDIR /task
USER bench
