# full (non-slim) image: already contains libgomp, which LightGBM needs - no apt step required
FROM python:3.12
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
RUN useradd -m nbi && mkdir -p /app/data && chown -R nbi /app
USER nbi
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
