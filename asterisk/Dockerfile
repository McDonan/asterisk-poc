FROM debian:trixie-slim

ARG ASTERISK_VERSION=22.11.0
ENV DEBIAN_FRONTEND=noninteractive

# Pipelining off + no proxy cache: avoids "Hash Sum mismatch" behind some proxies / flaky networks
# apt over HTTPS (port 443 only): the slim image has no CA bundle yet, so the first step skips peer
# verification just to fetch ca-certificates (packages stay GPG-verified), then verification is re-enabled
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Retries "5";\nAcquire::http::Pipeline-Depth "0";\nAcquire::http::No-Cache "true";\nAcquire::http::Timeout "60";\n' > /etc/apt/apt.conf.d/80network \
    && printf 'Acquire::https::Verify-Peer "false";\n' > /etc/apt/apt.conf.d/99bootstrap-tls \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm /etc/apt/apt.conf.d/99bootstrap-tls \
    && apt-get update && apt-get install -y --no-install-recommends --fix-missing \
        build-essential curl wget bzip2 xz-utils patch pkg-config \
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
    curl -fsSL "https://downloads.asterisk.org/pub/telephony/asterisk/releases/asterisk-${ASTERISK_VERSION}.tar.gz" \
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

# tzdata: business hours are evaluated in Asia/Bangkok (GotoIfTime). Separate layer so the Asterisk build stays cached.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

# Thai/English IVR prompts neural TTS wavs from prompts/ (espeak-ng fallback) (POC only - replace with studio recordings)
COPY prompts/ /var/lib/asterisk/sounds/poc/
COPY gen-prompts.sh /tmp/gen-prompts.sh
RUN sh /tmp/gen-prompts.sh && rm /tmp/gen-prompts.sh

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5060/udp 5038/tcp 8088/tcp 10000-10100/udp
ENTRYPOINT ["/entrypoint.sh"]
CMD ["asterisk", "-f", "-vvv"]
