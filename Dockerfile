# Stage 1: Download Restic
FROM alpine as restic-downloader
WORKDIR /download
RUN apk add --no-cache curl
ENV RESTIC_VERSION=0.16.0
RUN curl -L -O https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_amd64.bz2 && \
    bunzip2 restic_${RESTIC_VERSION}_linux_amd64.bz2 && \
    chmod +x restic_${RESTIC_VERSION}_linux_amd64 && \
    mv restic_${RESTIC_VERSION}_linux_amd64 restic  # Rename the binary to 'restic'

# Stage 2: Build the Application
FROM python:3.8-slim
WORKDIR /app

# Install Restic
COPY --from=restic-downloader /download/restic /usr/local/bin/restic

# Install docker-cli
RUN apt-get update && apt-get install -y docker.io

# Install Python dependencies and copy application files


COPY requirements.txt .
RUN pip install -r requirements.txt

COPY ./app .

CMD ["python", "app.py"]
