
FROM python:3.11-slim

WORKDIR /app

# Same caching principle — install dependencies before copying code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--reload"]
