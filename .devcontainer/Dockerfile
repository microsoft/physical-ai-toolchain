FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04@sha256:14f6d08d1cd4a96effbfe3101d0b56326f552c199d05e4979ee0bd616df5811b AS cuda-runtime
RUN apt-get update && \
	apt-get install -y --no-install-recommends cuda-cupti-13-0=13.0.85-1 && \
	mkdir /opt/nvpl && \
	if [ "$(dpkg --print-architecture)" = "arm64" ]; then \
		apt-get install -y --no-install-recommends libcudss0-cuda-13=0.8.0.10-1 libnvpl-blas0=0.6.0-1 libnvpl-lapack0=0.4.0.1-1 && \
		cp -a /usr/lib/*-linux-gnu/libcudss/13/libcudss*.so* /usr/lib/*-linux-gnu/libnvpl*.so* /opt/nvpl/; \
	fi && \
	rm -rf /var/lib/apt/lists/*

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04@sha256:d94c97dd9cacf183d0a6fd12a8e87b526e9e928307674ae9c94139139c0c6eae

RUN apt-get update && \
	apt-get install -y --no-install-recommends python3 python3-venv python-is-python3 && \
	rm -rf /var/lib/apt/lists/*

COPY --from=cuda-runtime /usr/local/cuda-13.0 /usr/local/cuda-13.0
COPY --from=cuda-runtime /usr/lib/*-linux-gnu/libcudnn*.so* /usr/local/cuda-13.0/lib64/
COPY --from=cuda-runtime /opt/nvpl/ /usr/local/cuda-13.0/lib64/
RUN ln -s /usr/local/cuda-13.0 /usr/local/cuda
ENV PATH="/usr/local/cuda/bin:${PATH}"
ENV LD_LIBRARY_PATH="/usr/local/cuda/lib64"

ARG NPM_CONFIG_REGISTRY=
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY:-https://registry.npmjs.org/}

ARG PIP_INDEX_URL=
ENV PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.org/simple/}

ARG UV_DEFAULT_INDEX=
# No trailing slash: uv records this URL verbatim in uv.lock, so a slash here
# makes every locally regenerated lock drift from the CI-generated one.
ENV UV_DEFAULT_INDEX=${UV_DEFAULT_INDEX:-https://pypi.org/simple}
