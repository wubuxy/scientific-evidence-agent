FROM node:24-alpine AS build
WORKDIR /app/frontend
COPY package.json package-lock.json ./
RUN npm ci
COPY . ./
RUN npm run build

FROM node:24-alpine
WORKDIR /app/frontend
COPY --from=build /app/frontend/package.json ./
COPY --from=build /app/frontend/node_modules ./node_modules
COPY --from=build /app/frontend/dist ./dist
COPY --from=build /app/frontend/vite.config.ts ./

EXPOSE 5183
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5183"]
