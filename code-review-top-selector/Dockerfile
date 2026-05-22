FROM python:3.12-slim

WORKDIR /app
COPY SKILL.md service.py ./

EXPOSE 18898
ENV PYTHONUNBUFFERED=1

CMD ["python", "service.py", "--host", "0.0.0.0", "--port", "18898"]
