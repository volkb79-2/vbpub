FROM node:18-alpine AS webapp-build

WORKDIR /app
COPY webapp/package*.json ./
RUN npm ci --only=production

COPY webapp/ ./
RUN npm run build

# SvelteKit Webapp Runtime
FROM node:18-alpine AS webapp
WORKDIR /app
COPY --from=webapp-build /app/build ./build
COPY --from=webapp-build /app/node_modules ./node_modules
COPY webapp/package.json ./
EXPOSE 3000
CMD ["node", "build"]