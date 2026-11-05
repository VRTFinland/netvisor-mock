FROM python:3.15-slim

ARG VCS_REF
LABEL org.opencontainers.image.source="https://github.com/VRTFinland/netvisor-mock" \
      org.opencontainers.image.revision="${VCS_REF}"

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV BASE_URL="http://localhost:5001" \
    PORT=5001 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/netvisor-mock/.venv/bin:$PATH"

WORKDIR /netvisor-mock

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache

COPY app.py payroll_mock.py invoice.pdf ./

CMD ["sh", "-c", "exec flask run --host=0.0.0.0 --port $PORT"]
EXPOSE $PORT
