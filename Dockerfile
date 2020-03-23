FROM python:3.8-alpine

ENV BASE_URL="http://localhost:5001"
ENV PORT=5001

RUN apk add --no-cache --virtual .build-deps gcc libc-dev libxslt-dev && \
    apk add --no-cache libxslt py3-lxml
RUN mkdir /netvisor-mock
COPY ./ /netvisor-mock
WORKDIR /netvisor-mock
RUN rm data.json
RUN pip install pipenv && \
    pipenv install --ignore-pipfile --clear && \
    apk del .build-deps

CMD pipenv run flask run --host=0.0.0.0 --port $PORT
EXPOSE $PORT
