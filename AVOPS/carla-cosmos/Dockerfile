ARG COSMOS_IMAGE=cosmos-transfer2.5:local
FROM ${COSMOS_IMAGE}

ARG SCENARIO_RUNNER_REF=v0.9.15

USER root
WORKDIR /workspace
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl ffmpeg git libegl1 libgl1 libglib2.0-0 libglvnd0 procps && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${SCENARIO_RUNNER_REF}" \
        https://github.com/carla-simulator/scenario_runner.git \
        /opt/scenario_runner

# ScenarioRunner imports CARLA's navigation `agents`, which ship with the simulator release rather than the pip wheel.
RUN git clone --depth 1 --filter=blob:none --sparse --branch 0.9.15 \
        https://github.com/carla-simulator/carla.git /tmp/carla-src && \
    git -C /tmp/carla-src sparse-checkout set PythonAPI/carla/agents && \
    mkdir -p /opt/carla-pythonapi && \
    cp -r /tmp/carla-src/PythonAPI/carla/agents /opt/carla-pythonapi/agents && \
    rm -rf /tmp/carla-src

COPY src/ /workspace/carla_cosmos/
COPY scenarios/ /workspace/scenarios/
COPY prompts/ /workspace/prompts/
COPY scenario_map.json /workspace/scenario_map.json
RUN uv python install 3.10 && \
    uv venv --python 3.10 /opt/carla-venv && \
    uv pip install --python /opt/carla-venv/bin/python \
        "carla==0.9.15" \
        "numpy<2" \
        "setuptools<81" \
        scipy \
        opencv-python-headless \
        "networkx<3" \
        "Shapely<2" \
        "xmlschema<2" \
        psutil \
        py-trees==0.8.3 \
        ephem \
        tabulate \
        matplotlib \
        six \
        simple-watchdog-timer \
        antlr4-python3-runtime==4.10 \
        graphviz \
        tqdm \
        pyarrow && \
    uv pip install --python /workspace/.venv/bin/python scipy tqdm pyarrow

ENV COSMOS_ROOT=/workspace \
    CARLA_COSMOS_DATA_ROOT=/workspace/data \
    CARLA_SCENARIOS_DIR=/workspace/scenarios \
    SCENARIO_RUNNER_ROOT=/opt/scenario_runner \
    CARLA_PYTHON=/opt/carla-venv/bin/python \
    PYTHONPATH=/workspace:/opt/scenario_runner:/opt/carla-pythonapi

ENTRYPOINT ["python", "-m", "carla_cosmos.pipeline.run_tasks"]
