FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 4200

# --poll: file-change events don't cross the Windows -> Linux bind-mount reliably, so
# without polling `ng serve` silently never rebuilds on edits made from the host.
CMD ["npx", "ng", "serve", "--host", "0.0.0.0", "--port", "4200", "--poll", "1500"]
