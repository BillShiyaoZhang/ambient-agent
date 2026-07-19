FROM node:22-alpine

WORKDIR /app

# Copy dependency configs
COPY package.json package-lock.json /app/

# Install dependencies
RUN npm install

# Copy frontend source code
COPY . /app

# Expose Vite dev server port
EXPOSE 5173

# Run Vite dev server in host-exposed mode
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
