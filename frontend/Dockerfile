# Start from an official Node image — this is your base OS + runtime
FROM node:20-alpine

# Set the working directory inside the container
WORKDIR /app

# Copy dependency files first (before copying all code)
# Docker caches layers — if package.json hasn't changed, it skips reinstalling
COPY package.json package-lock.json ./
RUN npm install

# Now copy the rest of the code
COPY . .

# Expose the port this service runs on
EXPOSE 3000

# The command that starts the service
CMD ["npm", "run", "dev"]
