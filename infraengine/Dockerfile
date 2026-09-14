# InfraEngine — productie-image (FastAPI serveert ook de frontend)
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# data/ (projecten, ZRO-dossiers, richtlijnen) komt als volume — zie compose
EXPOSE 8000

# Eén worker, bewust: het rekenresultaat, de voortgang (/api/progress) en de
# caches leven in het procesgeheugen; meerdere workers zouden die staat
# uit elkaar trekken (de app is ontworpen voor één gelijktijdige gebruiker).
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "backend", \
     "--host", "0.0.0.0", "--port", "8000"]
