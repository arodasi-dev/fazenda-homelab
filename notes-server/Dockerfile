FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
ENV NOTES_DIR=/data PORT=8787
VOLUME /data
EXPOSE 8787
CMD ["python", "-u", "server.py"]
