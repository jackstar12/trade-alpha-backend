
# This file has been autogenerated by the docker.py tool.
# Do not edit directly

FROM python:3.10.3-bullseye

RUN curl -sSL https://install.python-poetry.org | python3 -

COPY projects/collector/pyproject.toml /app/projects/collector/pyproject.toml
COPY projects/collector/collector/__init__.py /app/projects/collector/collector/__init__.py

COPY projects/collector/../../lib/common/pyproject.toml /app/projects/collector/../../lib/common/pyproject.toml
COPY projects/collector/../../lib/common/common/__init__.py /app/projects/collector/../../lib/common/common/__init__.py

COPY projects/collector/../../lib/common/../database/pyproject.toml /app/projects/collector/../../lib/common/../database/pyproject.toml
COPY projects/collector/../../lib/common/../database/database/__init__.py /app/projects/collector/../../lib/common/../database/database/__init__.py

COPY projects/collector/../../lib/core/pyproject.toml /app/projects/collector/../../lib/core/pyproject.toml
COPY projects/collector/../../lib/core/core/__init__.py /app/projects/collector/../../lib/core/core/__init__.py



WORKDIR /app/projects/collector/

RUN export PATH=$PATH:$HOME/.local/bin &&    poetry config virtualenvs.create false &&    poetry install --without dev    

COPY projects/collector/ /app/projects/collector/
COPY lib /app/lib/


CMD [ "python3", "__main__.py" ]
