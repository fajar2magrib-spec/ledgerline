FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 LEDGERLINE_ENV=production
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt "psycopg[binary]>=3.2"
COPY . .
RUN mkdir -p /app/data
EXPOSE 8501
# Hosts such as Render/Railway/Fly give the port in $PORT; locally it is 8501.
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]
