FROM python:3.11-slim

WORKDIR /runner

# Install dependencies
RUN pip install flask requests

# Copy runner service
COPY tutorial-runner/app.py .

# Create non-root user for security
RUN useradd -m runner
USER runner

EXPOSE 5001

CMD ["python", "app.py"]