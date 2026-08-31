FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

# CLI используется только headless: он принимает STL и пишет временный G-code,
# из которого приложение читает оценку времени и расход пластика.
RUN apt-get update \
    && apt-get install --no-install-recommends -y prusa-slicer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
