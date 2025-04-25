FROM ubuntu:22.04

# Install required packages in a single layer
RUN apt-get update -qq && \
    apt-get install -y -qq \
    wget \
    aria2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
