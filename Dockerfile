FROM python:3.12-alpine

WORKDIR /app
COPY app.py schedules.json ./
RUN mkdir outputs

ENV PORT=10000
EXPOSE 10000
CMD ["python", "app.py"]
