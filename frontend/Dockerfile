FROM node:22-alpine AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts


FROM dependencies AS development

ENV NODE_ENV=development
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]


FROM dependencies AS build

ARG VITE_API_BASE_URL=
ARG VITE_WIDGET_UI_TRANSPORT=iframe
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    VITE_WIDGET_UI_TRANSPORT=${VITE_WIDGET_UI_TRANSPORT}
COPY . .
RUN npm run build


FROM nginx:1.30-alpine AS production

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

USER nginx
EXPOSE 5173
CMD ["nginx", "-g", "daemon off;"]
