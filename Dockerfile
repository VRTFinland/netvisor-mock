FROM python:3.8-alpine

ARG VCS_REF
LABEL org.opencontainers.image.source="https://github.com/VRTFinland/netvisor-mock" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV BASE_URL="http://localhost:5001"
ENV PORT=5001

RUN apk add --no-cache --virtual .build-deps gcc libc-dev libxslt-dev && \
    apk add --no-cache libxslt py3-lxml
RUN mkdir /netvisor-mock
COPY ./ /netvisor-mock
WORKDIR /netvisor-mock
RUN rm -f data.json
RUN pip install pipenv && \
    pipenv install --ignore-pipfile --clear && \
    apk del .build-deps

CMD ["sh", "-c", "exec pipenv run flask run --host=0.0.0.0 --port $PORT"]
EXPOSE $PORT
