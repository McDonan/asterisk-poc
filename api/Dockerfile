FROM python:3.12-slim

# Optional corporate TLS-inspection CA (put *.crt in api/certs/) - keeps TLS verification enabled
COPY certs/ /usr/local/share/ca-certificates/corp/
RUN if ls /usr/local/share/ca-certificates/corp/*.crt >/dev/null 2>&1; then \
        update-ca-certificates; \
    fi
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
