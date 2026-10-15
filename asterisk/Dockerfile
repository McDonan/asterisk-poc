FROM debian:trixie-slim

ARG ASTERISK_VERSION=22.11.0
ENV DEBIAN_FRONTEND=noninteractive

# Pipelining off + no proxy cache: avoids "Hash Sum mismatch" behind some proxies / flaky networks
RUN printf 'Acquire::Retries "5";\nAcquire::http::Pipeline-Depth "0";\nAcquire::http::No-Cache "true";\nAcquire::http::Timeout "60";\n' > /etc/apt/apt.conf.d/80network \
    && apt-get update && apt-get install -y --no-install-recommends --fix-missing \
        build-essential ca-certificates curl wget bzip2 xz-utils patch pkg-config \
        libssl-dev libxml2-dev libncurses-dev libsqlite3-dev uuid-dev libjansson-dev \
        libedit-dev libxslt1-dev libsrtp2-dev libcurl4-openssl-dev \
        python3 sox espeak-ng gettext-base \
    && rm -rf /var/lib/apt/lists/*

# Optional corporate TLS-inspection CA (put *.crt in asterisk/certs/) - keeps TLS verification enabled
COPY certs/ /usr/local/share/ca-certificates/corp/
RUN update-ca-certificates

# Build Asterisk from source (LTS release) - sounds + MOH are downloaded during "make install"
RUN set -eux; \
    mkdir -p /usr/src/asterisk; cd /usr/src/asterisk; \
    curl -fsSL "https://downloads.asterisk.org/pub/telephony/asterisk/asterisk-${ASTERISK_VERSION}.tar.gz" \
        | tar xz --strip-components=1; \
    ./configure --with-jansson-bundled --with-pjproject-bundled --without-x11 >/dev/null; \
    make menuselect.makeopts; \
    menuselect/menuselect \
        --disable BUILD_NATIVE \
        --enable CORE-SOUNDS-EN-ULAW \
        --enable MOH-OPSOUND-ULAW \
        menuselect.makeopts; \
    make -j"$(nproc)"; \
    make install; \
    ldconfig; \
    cd /; rm -rf /usr/src/asterisk

# Thai/English IVR prompts generated with espeak-ng (POC only - replace with studio recordings)
COPY gen-prompts.sh /tmp/gen-prompts.sh
RUN sh /tmp/gen-prompts.sh && rm /tmp/gen-prompts.sh

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5060/udp 5038/tcp 8088/tcp 10000-10050/udp
ENTRYPOINT ["/entrypoint.sh"]
CMD ["asterisk", "-f", "-vvv"]
