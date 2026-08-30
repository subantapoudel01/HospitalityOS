FROM node:20-alpine

WORKDIR /app

# Lockfile copied too, so builds are reproducible and `npm ci` can be used.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]
