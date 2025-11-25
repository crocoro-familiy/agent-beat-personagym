# 1. Use the official Python 3.13 image (Slim version is smaller/faster)
FROM python:3.13-slim

# 2. Set the working directory to the container root
WORKDIR /app

# 3. Copy requirements first and install dependencies
# (This assumes requirements.txt is in the same folder as the Dockerfile)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the rest of your project (including the 'code' folder)
COPY . .

# 5. Change directory into 'code' (simulating "cd code")
WORKDIR /app/code

# 6. Run the command with the "green" argument
# This executes: python main.py green
CMD ["agentbeats", "run_ctrl"]